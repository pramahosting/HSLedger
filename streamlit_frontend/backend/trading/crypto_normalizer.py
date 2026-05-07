"""
crypto_normalizer.py
Generic exchange ledger normalizer for HSLedger crypto CGT.

Accepts CSV exports from any exchange and produces a standardized DataFrame
whose columns (_dt, _event, _asset, _qty, _amount_aud, _fee_aud) the FIFO
engine can process without knowing anything about the original format.

Supported out-of-the-box (tested headers):
  CoinSpot · Binance · Kraken · Coinbase · Independent Reserve · Generic ledgers

Column resolution strategy:
  1. Normalize all header names: lowercase, strip, spaces/hyphens → underscores.
  2. Walk each logical field's alias list and return the first match.
  3. For 'asset': fall back to splitting a pair/symbol column (e.g. "BTC/AUD" → "BTC").
  4. For 'amount_aud': fall back to price_aud × quantity when a price_aud column exists.
  5. For 'fee_aud': default to 0 when no fee column is found.
  6. If any required field still can't be resolved, raise ValueError listing detected
     columns so the user knows exactly what is missing.

Event-type normalization:
  Raw values (buy / trade_buy / market buy / sold / disposal / staking / …) are
  mapped to canonical internal types: BUY | SELL | STAKING | REWARD | AIRDROP |
  TRANSFER_IN | TRANSFER_OUT | FEE | UNKNOWN.
  UNKNOWN events are silently skipped by the CGT engine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


# ── Logical-field alias tables ─────────────────────────────────────────────────
# Keys are the logical field names the engine expects.
# Values are ordered candidate column names (normalized: lower + underscores).
# First match in the CSV wins.

FIELD_ALIASES: dict[str, list[str]] = {
    "datetime": [
        "datetime_utc", "event_time_utc", "event_datetime_utc", "time_utc",
        "datetime", "date", "time", "timestamp", "created_at", "completed_at",
        "order_time", "trade_time", "trade_date", "txn_date", "transaction_date",
        "settlement_date", "value_date",
    ],
    "event_type": [
        "event_type", "type", "transaction_type", "action", "side",
        "event", "category", "txn_type", "order_type", "trans_type",
        "transaction_kind",
    ],
    "asset": [
        "asset", "coin", "currency", "symbol", "base_asset",
        "cryptocurrency", "token", "crypto_currency", "base_currency",
        "asset_id",
    ],
    "quantity": [
        "quantity", "qty", "units", "volume",
        "filled", "executed_qty", "crypto_amount", "coin_amount",
        "amount",  # generic — resolved last to avoid colliding with AUD amount
    ],
    "amount_aud": [
        "amount_aud", "aud_total", "aud_value", "total_aud", "value_aud",
        "fiat_value", "fiat_amount", "net_aud", "proceeds_aud", "aud_amount",
        "value_quote", "quote_amount", "total_value", "total", "subtotal",
        "debit_aud", "credit_aud",
    ],
    "fee_aud": [
        "fee_aud", "fee_aud_inc_gst", "fee_aud_total", "fee",
        "fee_amount", "commission", "commission_aud", "total_fee",
        "trading_fee", "network_fee",
    ],
}

# Columns tried when amount_aud is absent but a per-unit AUD price exists
_PRICE_AUD_ALIASES = [
    "price_aud", "unit_price_aud", "aud_price", "rate_aud",
    "price_per_unit_aud", "unit_price", "price",
]

# Columns tried when asset is absent but a trading pair column exists
_PAIR_ALIASES = ["pair", "market", "trading_pair", "symbol_pair", "instrument"]


# ── Event-type normalisation map ───────────────────────────────────────────────
# Each entry: ([list of raw substrings], internal_type).
# Matching is done against the lowercased, stripped raw value.
# First match wins; order matters (more specific first).

_EVENT_RULES: list[tuple[list[str], str]] = [
    # --- Pass-through: values already in engine-canonical form ---
    (["trade_buy"],          "BUY"),
    (["trade_sell"],         "SELL"),
    (["staking_reward"],     "STAKING"),
    (["air_drop"],           "AIRDROP"),
    (["mining_reward"],      "REWARD"),
    (["interest"],           "REWARD"),
    (["yield"],              "REWARD"),
    (["fork"],               "AIRDROP"),
    # --- Acquisitions ---
    (["buy", "purchase", "market buy", "limit buy", "bought",
      "crypto buy", "ob", "buy order"],                          "BUY"),
    # --- Disposals ---
    (["sell", "disposal", "market sell", "limit sell", "sold",
      "crypto sell", "os", "sell order"],                        "SELL"),
    # --- Transfers in ---
    (["deposit", "receive", "transfer_in", "incoming",
      "credit", "receive crypto", "crypto deposit"],             "TRANSFER_IN"),
    # --- Transfers out ---
    (["withdrawal", "withdraw", "send", "transfer_out",
      "outgoing", "debit", "send crypto", "crypto withdrawal"],  "TRANSFER_OUT"),
    # --- Fees ---
    (["trading fee", "network_fee", "network fee",
      "fee_in_crypto", "fee-in-crypto", "fee"],                  "FEE"),
    # --- Staking ---
    (["staking", "stake", "pos_reward", "pos reward",
      "proof of stake", "validator reward"],                     "STAKING"),
    # --- Airdrops ---
    (["airdrop", "air drop", "token_distribution",
      "token distribution", "hard fork"],                        "AIRDROP"),
    # --- Rewards / income ---
    (["reward", "earn", "cashback", "referral",
      "lending", "mining"],                                       "REWARD"),
    # --- Internal / non-CGT ---
    (["internal_transfer", "internal transfer", "move",
      "wallet transfer"],                                         "TRANSFER_IN"),
    # --- Everything else — handled by rules engine or skipped ---
    (["convert", "swap"],                                         "UNKNOWN"),
]


# ── Public types ───────────────────────────────────────────────────────────────

# Events that trigger an acquisition (cost-base entry)
ACQ_EVENTS = frozenset({"BUY", "STAKING", "REWARD", "AIRDROP"})

# Events that trigger a disposal (CGT event)
DISP_EVENTS = frozenset({"SELL"})


@dataclass
class NormalizationReport:
    """Summary of what the normalizer resolved and any problems it found."""
    mapping:           dict[str, "str | None"]  # logical field → original column used
    unresolved:        list[str]                 # required fields still missing after all fallbacks
    event_type_counts: dict[str, int]            # internal type → row count
    aud_fallback_used: bool = False              # True if amount_aud = price_aud × quantity
    asset_from_pair:   bool = False              # True if asset was split from a pair column
    warnings:          list[str] = field(default_factory=list)

    def to_display_text(self) -> str:
        lines = ["=== Column Mapping ==="]
        for logical, col in self.mapping.items():
            annotation = ""
            if logical == "amount_aud" and self.aud_fallback_used:
                annotation = "  (calculated: price_aud × quantity)"
            if logical == "asset" and self.asset_from_pair:
                annotation = "  (extracted base asset from pair)"
            lines.append(f"  {logical:15} →  {col or '(not found)'}{annotation}")
        lines += ["", "Event type mapping:"]
        for et, n in sorted(self.event_type_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {et:20}  {n:>5} row(s)")
        if self.warnings:
            lines += ["", f"Normalizer warnings ({len(self.warnings)}):"]
            for w in self.warnings[:20]:
                lines.append(f"  - {w}")
            if len(self.warnings) > 20:
                lines.append(f"  ... {len(self.warnings) - 20} more")
        return "\n".join(lines)


# ── Main public function ───────────────────────────────────────────────────────

def normalize_ledger(df: pd.DataFrame) -> tuple[pd.DataFrame, NormalizationReport]:
    """
    Normalize any exchange ledger DataFrame into standard internal columns.

    Output columns added to the returned DataFrame:
      _dt          — UTC pd.Timestamp (NaT if the value could not be parsed)
      _event       — BUY | SELL | STAKING | REWARD | AIRDROP |
                     TRANSFER_IN | TRANSFER_OUT | FEE | UNKNOWN
      _asset       — uppercase asset symbol (e.g. "BTC")
      _qty         — absolute float quantity (always ≥ 0)
      _amount_aud  — absolute AUD transaction value (≥ 0)
      _fee_aud     — fee in AUD (0.0 when no fee column is present)

    Raises:
      ValueError — with a clear human-readable message listing unresolved required
                   fields and the actual column names found in the CSV, so the user
                   knows exactly what to rename or add.
    """
    norm_map  = _build_norm_col_map(df)
    warnings: list[str] = []

    # ── 1. Resolve every logical field ────────────────────────────────────────
    mapping: dict[str, "str | None"] = {
        lf: _resolve(norm_map, aliases)
        for lf, aliases in FIELD_ALIASES.items()
    }

    # ── 2. Fallback: extract asset from a pair column ─────────────────────────
    asset_from_pair = False
    if mapping["asset"] is None:
        pair_col = _resolve(norm_map, _PAIR_ALIASES)
        if pair_col is not None:
            mapping["asset"] = pair_col
            asset_from_pair  = True
            warnings.append(
                f"'asset' column not found — base asset will be split from '{pair_col}' "
                "(e.g. 'BTC/AUD' → 'BTC')."
            )

    # ── 3. Fallback: calculate amount_aud = price_aud × quantity ──────────────
    aud_fallback_used = False
    _price_col_for_fallback: "str | None" = None
    if mapping["amount_aud"] is None and mapping["quantity"] is not None:
        price_col = _resolve(norm_map, _PRICE_AUD_ALIASES)
        if price_col is not None:
            _price_col_for_fallback = price_col
            mapping["amount_aud"]   = f"__calc__{price_col}"
            aud_fallback_used       = True
            warnings.append(
                f"'amount_aud' column not found — will calculate as "
                f"'{price_col}' × '{mapping['quantity']}'."
            )

    # ── 4. Validate required fields are all resolved ──────────────────────────
    required   = ["datetime", "event_type", "asset", "quantity", "amount_aud"]
    unresolved = [f for f in required if mapping.get(f) is None]
    if unresolved:
        detected = ", ".join(df.columns.tolist())
        raise ValueError(
            f"Missing required fields: {', '.join(unresolved)}.\n"
            f"Detected columns were: {detected}\n\n"
            "Tip: rename your CSV columns or add a column matching one of these aliases:\n"
            + "\n".join(
                f"  {f:15} → try: {', '.join(FIELD_ALIASES[f][:4])}, …"
                for f in unresolved
            )
        )

    # ── 5. Build normalized output columns ────────────────────────────────────
    out = df.copy()

    # _dt — UTC timestamp
    out["_dt"] = out[mapping["datetime"]].apply(_parse_dt_utc)

    # _event — internal canonical type
    out["_event"] = (
        out[mapping["event_type"]]
        .fillna("UNKNOWN")
        .astype(str)
        .apply(_map_event_type)
    )

    # _asset — uppercase symbol, split from pair if needed
    raw_asset = out[mapping["asset"]].fillna("").astype(str)
    if asset_from_pair:
        out["_asset"] = raw_asset.apply(_split_pair_base)
    else:
        out["_asset"] = raw_asset.str.upper().str.strip()

    # _qty — absolute quantity
    out["_qty"] = out[mapping["quantity"]].apply(_abs_float)

    # _amount_aud — absolute AUD value (may be calculated)
    if aud_fallback_used and _price_col_for_fallback is not None:
        qty_s   = out[mapping["quantity"]].apply(_abs_float)
        price_s = out[_price_col_for_fallback].apply(_abs_float)
        out["_amount_aud"] = (qty_s * price_s).round(8)
    else:
        real_col = mapping["amount_aud"]
        out["_amount_aud"] = out[real_col].apply(_abs_float)

    # _fee_aud — default to 0 when absent
    fee_col = mapping["fee_aud"]
    if fee_col is not None:
        out["_fee_aud"] = out[fee_col].apply(_abs_float).fillna(0.0)
    else:
        out["_fee_aud"] = 0.0
        warnings.append("'fee_aud' column not found — all fees defaulted to 0.")

    # ── 6. Build display-friendly mapping for the report ──────────────────────
    display_mapping: dict[str, "str | None"] = {}
    for lf, col in mapping.items():
        if col and col.startswith("__calc__"):
            display_mapping[lf] = col[len("__calc__"):]
        else:
            display_mapping[lf] = col

    event_counts: dict[str, int] = out["_event"].value_counts().to_dict()

    report = NormalizationReport(
        mapping           = display_mapping,
        unresolved        = unresolved,
        event_type_counts = event_counts,
        aud_fallback_used = aud_fallback_used,
        asset_from_pair   = asset_from_pair,
        warnings          = warnings,
    )

    return out, report


# ── Private helpers ────────────────────────────────────────────────────────────

def _normalize_col_name(name: str) -> str:
    """Lowercase + trim + collapse spaces/hyphens/dots to underscores."""
    s = str(name).lower().strip()
    s = re.sub(r"[\s\-\.]+", "_", s)
    return re.sub(r"_+", "_", s)


def _build_norm_col_map(df: pd.DataFrame) -> dict[str, str]:
    """Return {normalized_header: original_header} for every column in df."""
    return {_normalize_col_name(c): c for c in df.columns}


def _resolve(norm_map: dict[str, str], aliases: list[str]) -> "str | None":
    """Return the first original column whose normalized name matches any alias."""
    for alias in aliases:
        if alias in norm_map:
            return norm_map[alias]
    return None


def _map_event_type(raw: str) -> str:
    """Map a raw event_type string to a canonical internal type."""
    s = raw.lower().strip()
    s_space = s.replace("_", " ")
    for keywords, internal in _EVENT_RULES:
        for kw in keywords:
            kw_space = kw.replace("_", " ")
            if kw == s or kw_space == s_space:
                return internal
            # Substring match only for keywords long enough to be unambiguous
            if len(kw_space) >= 4 and kw_space in s_space:
                return internal
    return "UNKNOWN"


def _split_pair_base(pair_str: str) -> str:
    """Extract the base asset from 'BTC/USDT', 'ETH-AUD', 'BNB_USDT', etc."""
    s = pair_str.strip()
    for sep in ("/", "-", "_"):
        if sep in s:
            return s.split(sep)[0].upper().strip()
    return s.upper().strip() if s else "UNKNOWN"


def _parse_dt_utc(x) -> "pd.Timestamp | None":
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    for attempt in (lambda: pd.to_datetime(x, utc=True),
                    lambda: pd.to_datetime(str(x), utc=True)):
        try:
            return attempt()
        except Exception:
            pass
    return None


def _abs_float(x) -> float:
    """Return abs(float(x)), or NaN on failure."""
    try:
        if pd.isna(x):
            return np.nan
        return abs(float(x))
    except Exception:
        return np.nan
