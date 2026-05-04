"""
detect_file_type.py — HSLedger Trading Module
Auto-classifier: detects broker identity and asset class (equity/crypto)
from column fingerprints, known symbols, and event type values.

Returns structured result with confidence score, broker name, warnings,
and actionable fallback suggestion when confidence is below threshold.
"""

from __future__ import annotations
import os
from typing import Any
import pandas as pd

# ── Confidence threshold ──────────────────────────────────────────────────────
CONFIDENCE_THRESHOLD = 0.50   # below this → unsupported broker fallback

# ── Reference-number broker prefixes ─────────────────────────────────────────
# When multiple broker files share the same column layout (e.g., custom exports
# that all use CommSec headers), the contract note / reference number prefix
# disambiguates the actual broker.  Checked as a secondary signal after column
# fingerprints, so real broker-specific column files are never affected.
REFERENCE_BROKER_PREFIXES: dict[str, str] = {
    "CN": "commsec",       # CommSec contract notes: CN...
    "NT": "nabtrade",      # NABtrade reference numbers: NT...
    "SH": "superhero",     # Superhero reference numbers: SH...
    "SW": "selfwealth",    # SelfWealth: SW...
}

# ── Broker column fingerprints ────────────────────────────────────────────────
# Maps broker_id → set of column substrings that strongly identify it.
# Matching is case-insensitive partial match.
BROKER_FINGERPRINTS: dict[str, dict[str, Any]] = {
    # ── Equity ────────────────────────────────────────────────────────────────
    "commsec": {
        "asset_class": "equity",
        "required":    ["Contract Note", "Stock Code", "Stock Name"],
        "supporting":  ["Brokerage ($)", "GST ($)", "Net Proceeds"],
        "weight":      1.0,
    },
    "nabtrade": {
        "asset_class": "equity",
        "required":    ["ASX Code", "Reference Number"],
        "supporting":  ["Brokerage (inc GST)", "GST on Brokerage", "Net Amount"],
        "weight":      1.0,
    },
    "stake": {
        "asset_class": "equity",
        "required":    ["Ticker", "Order Type", "Trade Value (AUD)"],
        "supporting":  ["Brokerage (AUD)", "GST (AUD)", "Company"],
        "weight":      1.0,
    },
    "superhero": {
        "asset_class": "equity",
        "required":    ["Total Value ($)", "Unit Price ($)", "Net Amount ($)"],
        "supporting":  ["Brokerage ($)", "GST ($)", "Settlement Date"],
        "weight":      1.0,
    },
    "selfwealth": {
        "asset_class": "equity",
        "required":    ["Security Code", "Security Name", "Consideration ($)"],
        "supporting":  ["Transaction Ref", "Net Consideration", "Portfolio"],
        "weight":      1.0,
    },
    # ── Crypto ────────────────────────────────────────────────────────────────
    "coinspot": {
        "asset_class": "crypto",
        "required":    ["Market", "Type", "Amount AUD"],
        "supporting":  ["Rate AUD", "Fee AUD", "Transaction ID"],
        "weight":      1.0,
    },
    "binance": {
        "asset_class": "crypto",
        "required":    ["Pair", "Side", "Executed"],
        "supporting":  ["Fee", "Price", "Total"],
        "weight":      1.0,
    },
    "swyftx": {
        "asset_class": "crypto",
        "required":    ["Asset", "Action", "Amount (AUD)"],
        "supporting":  ["Price (AUD)", "Fee (AUD)"],
        "weight":      1.0,
    },
    "kraken": {
        "asset_class": "crypto",
        "required":    ["txid", "pair", "vol"],
        "supporting":  ["cost", "fee", "margin"],
        "weight":      1.0,
    },
    # ── Standard template ─────────────────────────────────────────────────────
    "standard": {
        "asset_class": "equity",
        "required":    ["asset_code", "transaction", "quantity", "unit_price"],
        "supporting":  ["brokerage", "gst", "net_proceeds"],
        "weight":      0.7,   # lower weight — it's a fallback
    },
}

# ── Known crypto symbols (top 60) ─────────────────────────────────────────────
CRYPTO_SYMBOLS = {
    "BTC","ETH","USDT","BNB","XRP","ADA","SOL","DOGE","DOT","MATIC",
    "LTC","SHIB","TRX","AVAX","LINK","UNI","ATOM","XLM","ETC","ALGO",
    "VET","MANA","SAND","AXS","CRO","FTM","NEAR","HBAR","ICP","EGLD",
    "XTZ","THETA","AAVE","GRT","MKR","COMP","SUSHI","SNX","YFI","BAT",
    "ZEC","DASH","DCR","KSM","WAVES","QTUM","ZIL","ENJ","CHZ","HOT",
    "OMG","BTT","1INCH","CAKE","LUNA","UST","RUNE","FIL","AR","STX",
}

# ── Known ASX equity symbols (sample — expand as needed) ──────────────────────
ASX_SYMBOLS = {
    "CBA","ANZ","NAB","WBC","MQG","WES","BHP","RIO","FMG","CSL",
    "TLS","WDS","ALL","TCL","XRO","COL","WOW","GMG","QBE","IAG",
    "SUN","AMP","ASX","ORG","AGL","APA","AMC","BXB","CWY","DXS",
    "JHX","MPL","NXT","ORI","REA","RHC","SGP","SHL","SKC","TWE",
}

# ── Crypto event-type values (CoinSpot "Type" column etc.) ───────────────────
CRYPTO_EVENT_TYPES = {
    "TRADE_BUY","TRADE_SELL","STAKING_REWARD","AIRDROP","MINING",
    "INTEREST","YIELD","FORK","DEPOSIT","WITHDRAWAL","buy","sell",
    "Buy","Sell","trade","staking","reward",
}


def _find_best_header_row(path: str, ext: str) -> tuple[pd.DataFrame, int]:
    """
    Try header rows 0-3 for Excel files and return the (df, header_row) pair
    whose column names score highest against broker fingerprints.
    Handles broker files that have 1-3 metadata rows above the real headers.
    CSV files always use header=0.
    """
    if ext == ".csv":
        return pd.read_csv(path, nrows=10), 0

    best_df: pd.DataFrame | None = None
    best_row = 0
    best_score = -1

    for row in range(4):
        try:
            df = pd.read_excel(path, header=row, nrows=10)
            cols_lower = [str(c).lower() for c in df.columns]
            score = sum(
                1 for fp in BROKER_FINGERPRINTS.values()
                for req in fp["required"]
                if any(req.lower() in c for c in cols_lower)
            )
            if score > best_score:
                best_score = score
                best_df = df
                best_row = row
        except Exception:
            continue

    if best_df is None:
        return pd.read_excel(path, nrows=10), 0
    return best_df, best_row


def _detect_broker_from_ref_prefix(df: pd.DataFrame) -> str | None:
    """
    Scan string columns for values whose first 2 chars match a known broker
    reference-number prefix (CN/NT/SH/SW).  Returns the broker id or None.
    Only looks at columns whose name contains a reference-like keyword.
    """
    ref_keywords = ("reference", "contract", "ref", "note", "transaction ref")
    candidate_cols = [
        c for c in df.columns
        if any(kw in str(c).lower() for kw in ref_keywords)
    ]
    # Fall back to all string columns if no labelled reference column found
    if not candidate_cols:
        candidate_cols = [c for c in df.columns if df[c].dtype == object]

    prefix_votes: dict[str, int] = {}
    for col in candidate_cols:
        for val in df[col].dropna().astype(str):
            prefix = val.strip()[:2].upper()
            if prefix in REFERENCE_BROKER_PREFIXES:
                broker_id = REFERENCE_BROKER_PREFIXES[prefix]
                prefix_votes[broker_id] = prefix_votes.get(broker_id, 0) + 1

    if not prefix_votes:
        return None
    return max(prefix_votes, key=lambda b: prefix_votes[b])


def detect(path: str) -> dict[str, Any]:
    """
    Detect asset class and broker from a file.

    Returns
    -------
    {
        "asset_class":  "equity" | "crypto" | "unknown",
        "broker":       str,   # actual broker (may be overridden by ref prefix)
        "col_format":   str,   # column-layout broker (use this to pick column map)
        "confidence":   float (0.0–1.0),
        "header_row":   int,         # 0-based row index used as column header
        "signals":      list[str],   # what matched
        "warnings":     list[str],   # issues found
        "fallback_suggestion": str | None,
        "columns_found":  list[str],
        "columns_expected": dict     # only populated when confidence is low
    }
    """
    result: dict[str, Any] = {
        "asset_class":        "unknown",
        "broker":             "unknown",
        "col_format":         "unknown",
        "confidence":         0.0,
        "header_row":         0,
        "signals":            [],
        "warnings":           [],
        "fallback_suggestion": None,
        "columns_found":      [],
        "columns_expected":   {},
    }

    if not os.path.exists(path):
        result["warnings"].append(f"File not found: {path}")
        return result

    # ── Load file, auto-detecting the correct header row ───────────────────────
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".xlsx", ".xlsm", ".xls"):
            df, header_row = _find_best_header_row(path, ext)
            result["header_row"] = header_row
        elif ext == ".csv":
            df = pd.read_csv(path, nrows=10)
        else:
            result["warnings"].append(f"Unsupported file type: {ext}")
            return result
    except Exception as e:
        result["warnings"].append(f"Failed to read file: {e}")
        return result

    cols = [str(c) for c in df.columns]
    result["columns_found"] = cols
    cols_lower = [c.lower() for c in cols]

    def col_match(pattern: str) -> bool:
        p = pattern.lower()
        return any(p in c for c in cols_lower)

    # ── Score each broker fingerprint ─────────────────────────────────────────
    scores: dict[str, float] = {}
    matched_signals: dict[str, list[str]] = {}

    for broker_id, fp in BROKER_FINGERPRINTS.items():
        req_hits  = sum(1 for r in fp["required"]    if col_match(r))
        supp_hits = sum(1 for s in fp["supporting"]  if col_match(s))
        req_total  = len(fp["required"])
        supp_total = len(fp["supporting"])

        # Required columns are worth 0.7 of score, supporting 0.3
        score = fp["weight"] * (
            0.7 * (req_hits / req_total) +
            0.3 * (supp_hits / supp_total if supp_total else 0)
        )
        scores[broker_id] = score
        matched_signals[broker_id] = (
            [r for r in fp["required"]   if col_match(r)] +
            [s for s in fp["supporting"] if col_match(s)]
        )

    # ── Symbol-based evidence ─────────────────────────────────────────────────
    # Check first 5 rows for known symbols in string-like columns
    crypto_symbol_hits = 0
    equity_symbol_hits = 0
    for col in df.columns:
        vals = df[col].dropna().astype(str).str.upper().tolist()
        for v in vals:
            v_strip = v.strip()
            if v_strip in CRYPTO_SYMBOLS:
                crypto_symbol_hits += 1
            if v_strip in ASX_SYMBOLS:
                equity_symbol_hits += 1

    # ── Event-type value evidence ─────────────────────────────────────────────
    crypto_event_hits = 0
    for col in df.columns:
        vals = df[col].dropna().astype(str).tolist()
        for v in vals:
            if v.strip() in CRYPTO_EVENT_TYPES:
                crypto_event_hits += 1

    # ── Pick best broker ──────────────────────────────────────────────────────
    best_broker = max(scores, key=lambda b: scores[b])
    best_score  = scores[best_broker]

    # Adjust by symbol evidence
    best_fp = BROKER_FINGERPRINTS[best_broker]
    if best_fp["asset_class"] == "equity" and equity_symbol_hits > 0:
        best_score = min(best_score + 0.10, 1.0)
        result["signals"].append(f"Found {equity_symbol_hits} known ASX symbol(s)")
    if best_fp["asset_class"] == "crypto" and (crypto_symbol_hits > 0 or crypto_event_hits > 0):
        best_score = min(best_score + 0.10, 1.0)
        result["signals"].append(f"Found {crypto_symbol_hits} crypto symbol(s), {crypto_event_hits} crypto event type(s)")

    result["signals"].extend([f"Matched column: '{s}'" for s in matched_signals[best_broker]])
    result["broker"]      = best_broker
    result["col_format"]  = best_broker
    result["confidence"]  = round(best_score, 3)
    result["asset_class"] = BROKER_FINGERPRINTS[best_broker]["asset_class"]

    # ── Reference-number prefix override ─────────────────────────────────────
    # When multiple broker files share the same column layout (e.g. all use
    # CommSec headers), the contract note / reference prefix disambiguates the
    # actual broker.  col_format keeps the column-layout broker so the normaliser
    # can still select the right column map.
    if best_score >= CONFIDENCE_THRESHOLD:
        ref_broker = _detect_broker_from_ref_prefix(df)
        if ref_broker and ref_broker != best_broker:
            result["col_format"] = best_broker   # column layout stays as-is
            result["broker"]     = ref_broker
            result["signals"].append(
                f"Reference prefix → broker override: {best_broker} → {ref_broker}"
            )

    # ── Low-confidence fallback ───────────────────────────────────────────────
    if best_score < CONFIDENCE_THRESHOLD:
        result["asset_class"] = "unknown"
        result["broker"]      = "unknown"

        # Find closest broker by required column match
        closest = max(scores, key=lambda b: sum(
            1 for r in BROKER_FINGERPRINTS[b]["required"] if col_match(r)
        ))
        closest_fp = BROKER_FINGERPRINTS[closest]
        missing = [r for r in closest_fp["required"] if not col_match(r)]

        result["fallback_suggestion"] = (
            f"Closest match: '{closest}' ({closest_fp['asset_class']}) — "
            f"missing columns: {missing}. "
            f"Use the HSLedger Standard Template or rename your columns."
        )
        result["columns_expected"] = {
            "closest_broker": closest,
            "required":       closest_fp["required"],
            "missing":        missing,
            "standard_template_url": "See HSLedger_Standard_Template.xlsx",
        }
        result["warnings"].append(
            f"Low confidence ({best_score:.0%}) — broker could not be identified reliably."
        )

    return result


def detect_batch(paths: list[str]) -> list[dict[str, Any]]:
    """Run detect() on a list of file paths and return results in order."""
    return [{"path": p, **detect(p)} for p in paths]


# ── CLI smoke-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, json
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    if os.path.isdir(target):
        paths = [
            os.path.join(target, f) for f in os.listdir(target)
            if f.lower().endswith((".xlsx", ".csv", ".xls"))
        ]
    else:
        paths = [target]

    for r in detect_batch(paths):
        print(f"\n{'─'*55}")
        print(f"File:        {r['path']}")
        print(f"Broker:      {r['broker']}  ({r['asset_class']})")
        print(f"Confidence:  {r['confidence']:.0%}")
        if r["signals"]:
            print(f"Signals:     {', '.join(r['signals'][:4])}")
        if r["warnings"]:
            print(f"Warnings:    {'; '.join(r['warnings'])}")
        if r["fallback_suggestion"]:
            print(f"Suggestion:  {r['fallback_suggestion']}")
