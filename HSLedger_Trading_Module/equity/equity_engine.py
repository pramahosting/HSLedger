"""
equity_engine.py — HSLedger Trading Module
FIFO CGT engine for Australian equity transactions.

Consumes a canonical normalised DataFrame (from normaliser.py).
Applies full ATO rules:
  - Trade date is the CGT event date (ATO TR 2023/1)
  - Settlement date stored for reference/reporting only
  - FIFO cost base per asset — oldest lots consumed first
  - 50% CGT discount: held > 365 days AND gain > 0 (individuals/trusts)
  - Brokerage + GST capitalised into cost base on acquisition
  - Brokerage + GST deducted from proceeds on disposal
  - Capital losses offset gains within same FY
  - Carry-forward losses propagate across FYs automatically
  - Income events (DIV/INT/LND/INC) collected separately — not CGT events
  - Options (OB/OS/OPT) tracked in separate queue
  - Missing buy lots → flagged with code/qty/date for user resolution
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Lot:
    code:          str
    qty:           float
    cost_per_unit: float   # price + pro-rated (brokerage + GST)
    trade_date:    date
    settlement_date: date | None = None
    broker:        str = ""
    reference:     str = ""

    @property
    def total_cost(self) -> float:
        return self.qty * self.cost_per_unit


@dataclass
class OptionLot:
    code:             str
    underlying:       str
    qty:              float
    premium_per_unit: float
    trade_date:       date
    broker:           str = ""


@dataclass
class DisposalRow:
    disposal_date:      date
    settlement_date:    date | None
    code:               str
    name:               str
    qty_disposed:       float
    proceeds_per_unit:  float
    cost_per_unit:      float
    acquisition_date:   date
    acquisition_settlement: date | None
    held_days:          int
    discount_eligible:  bool
    gross_gain:         float
    discounted_gain:    float
    broker:             str = ""
    reference:          str = ""
    event_type:         str = "SELL"
    source_file:        str = ""


@dataclass
class IncomeRow:
    event_date:   date
    code:         str
    name:         str
    amount:       float
    income_type:  str
    broker:       str = ""
    description:  str = ""
    source_file:  str = ""


@dataclass
class MissingBuyFlag:
    """Raised when a sell has no matching buy lots."""
    disposal_date:     date
    code:              str
    qty_unmatched:     float
    broker:            str = ""
    reference:         str = ""
    row_index:         int = 0    # 1-based row number in the merged input data
    proceeds_per_unit: float = 0.0  # sell price per unit (for tax estimate UI)


@dataclass
class OptionFlag:
    """An option lot with no corresponding close/exercise event."""
    option_date:   date
    code:          str
    underlying:    str       # e.g. "BHP" extracted from "BHP_PUT_25_JUN2025"
    qty:           float
    premium_paid:  float   # premium per unit paid at open
    broker:        str = ""
    row_index:     int = 0


@dataclass
class OptionTransaction:
    """Every OB/OS/OPT row found in the broker data — full audit trail."""
    txn_type:       str     # "OB" | "OS" | "OPT"
    trade_date:     date
    code:           str
    underlying:     str
    qty:            float
    price_per_unit: float   # premium paid (OB) or proceeds received (OS/OPT)
    total_value:    float   # qty * price_per_unit
    broker:         str
    source_file:    str
    row_index:      int


@dataclass
class FYSummary:
    financial_year:         str
    total_proceeds:         float = 0.0
    total_cost_base:        float = 0.0
    gross_gains:            float = 0.0
    gross_losses:           float = 0.0
    net_capital_gain_pre:   float = 0.0
    cgt_discount_applied:   float = 0.0
    net_capital_gain:       float = 0.0
    carry_forward_in:       float = 0.0
    net_after_carryforward: float = 0.0
    total_income:           float = 0.0
    disposal_count:         int   = 0
    income_count:           int   = 0
    missing_buys:           list  = field(default_factory=list)
    brokers:                list  = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fy(d: date) -> str:
    if d.month >= 7:
        return f"{d.year}-{str(d.year + 1)[2:]}"
    return f"{d.year - 1}-{str(d.year)[2:]}"


def _fifo_consume(
    queue:                deque[Lot],
    qty_needed:           float,
    disposal_date:        date,
    settlement_date:      date | None,
    code:                 str,
    name:                 str,
    net_proceeds_per_unit: float,
    broker:               str,
    reference:            str,
    source_file:          str,
    event_type:           str = "SELL",
) -> tuple[list[DisposalRow], float]:
    """
    Consume qty_needed from FIFO queue.
    Returns (disposals, remaining_unmatched_qty).
    """
    rows: list[DisposalRow] = []
    remaining = qty_needed

    while remaining > 1e-9 and queue:
        lot   = queue[0]
        take  = min(lot.qty, remaining)
        held  = (disposal_date - lot.trade_date).days
        disc  = held > 365
        gross = (net_proceeds_per_unit - lot.cost_per_unit) * take
        disc_gain = gross * 0.5 if (disc and gross > 0) else gross

        rows.append(DisposalRow(
            disposal_date           = disposal_date,
            settlement_date         = settlement_date,
            code                    = code,
            name                    = name,
            qty_disposed            = take,
            proceeds_per_unit       = net_proceeds_per_unit,
            cost_per_unit           = lot.cost_per_unit,
            acquisition_date        = lot.trade_date,
            acquisition_settlement  = lot.settlement_date,
            held_days               = held,
            discount_eligible       = disc,
            gross_gain              = round(gross, 6),
            discounted_gain         = round(disc_gain, 6),
            broker                  = broker,
            reference               = reference,
            event_type              = event_type,
            source_file             = source_file,
        ))
        lot.qty -= take
        remaining -= take
        if lot.qty < 1e-9:
            queue.popleft()

    return rows, remaining


# ── Main CGT engine ───────────────────────────────────────────────────────────

def compute_cgt(
    df: pd.DataFrame,
    carry_forward_losses: dict[str, float] | None = None,
    initial_queues:       dict[str, deque[Lot]] | None = None,
    interactive_missing:  bool = False,
) -> tuple[list[DisposalRow], list[IncomeRow], dict[str, FYSummary],
           dict[str, deque[Lot]], list[MissingBuyFlag], list[OptionFlag]]:
    """
    Run FIFO CGT engine over a canonical normalised DataFrame.

    Parameters
    ----------
    df                    : Canonical DataFrame from normaliser.py
    carry_forward_losses  : dict FY → loss amount carried in from prior FY
    initial_queues        : Pre-populated FIFO queues (from cost_base_loader)
    interactive_missing   : If True, prompt user on stdin for missing buys

    Returns
    -------
    disposals      : list[DisposalRow]
    income_events  : list[IncomeRow]
    fy_summaries   : dict[str, FYSummary]
    final_queues   : dict[str, deque[Lot]]  — remaining equity open positions
    missing_flags  : list[MissingBuyFlag]   — sells with no matching buy
    option_flags   : list[OptionFlag]       — option lots with no close event
    """
    cf_losses = carry_forward_losses or {}
    queues:         dict[str, deque[Lot]]       = defaultdict(deque)
    option_queues:  dict[str, deque[OptionLot]] = defaultdict(deque)
    disposals:      list[DisposalRow]           = []
    income_events:  list[IncomeRow]             = []
    missing_flags:  list[MissingBuyFlag]        = []
    option_txns:    list[OptionTransaction]     = []

    # Merge in pre-populated historical queues
    if initial_queues:
        for code, q in initial_queues.items():
            queues[code].extend(q)

    # Sort by trade_date ascending — critical for correct FIFO
    df = df.copy().sort_values("trade_date").reset_index(drop=True)

    for row_pos, (_, row) in enumerate(df.iterrows()):
        trade_d  = row["trade_date"]
        sett_d   = row.get("settlement_date")
        code     = str(row.get("code", "")).strip().upper()
        name     = str(row.get("name", code)).strip()
        txn      = str(row.get("transaction", "")).strip().upper()
        qty      = float(row.get("qty", 0))
        price    = float(row.get("price", 0))
        brok     = float(row.get("brokerage", 0))
        gst      = float(row.get("gst", 0))
        cv       = float(row.get("contract_value", 0))
        net_proc = float(row.get("net_proceeds", 0))
        ref      = str(row.get("reference", "")).strip()
        src      = str(row.get("source_file", "")).strip()
        broker   = str(row.get("broker", "")).strip()

        # ── BUY ──────────────────────────────────────────────────────────────
        if txn == "BUY":
            if qty <= 0 or price <= 0:
                continue
            cpu = price + (brok + gst) / qty   # cost per unit incl. all costs
            queues[code].append(Lot(
                code            = code,
                qty             = qty,
                cost_per_unit   = cpu,
                trade_date      = trade_d,
                settlement_date = sett_d if pd.notna(sett_d) else None,
                broker          = broker,
                reference       = ref,
            ))

        # ── SELL ─────────────────────────────────────────────────────────────
        elif txn == "SELL":
            if qty <= 0:
                continue

            # Net proceeds per unit — prefer explicit net_proceeds column
            if net_proc and abs(net_proc) > 0:
                npp = abs(net_proc) / qty
            elif cv and abs(cv) > 0:
                npp = (abs(cv) - brok - gst) / qty
            else:
                npp = max(price - (brok + gst) / qty, 0)

            rows, unmatched = _fifo_consume(
                queue                 = queues[code],
                qty_needed            = qty,
                disposal_date         = trade_d,
                settlement_date       = sett_d if pd.notna(sett_d) else None,
                code                  = code,
                name                  = name,
                net_proceeds_per_unit = npp,
                broker                = broker,
                reference             = ref,
                source_file           = src,
            )
            disposals.extend(rows)

            if unmatched > 1e-6:
                flag = MissingBuyFlag(
                    disposal_date     = trade_d,
                    code              = code,
                    qty_unmatched     = round(unmatched, 6),
                    broker            = broker,
                    reference         = ref,
                    row_index         = row_pos + 2,
                    proceeds_per_unit = round(npp, 6),
                )
                missing_flags.append(flag)

                if interactive_missing:
                    from shared.cost_base_loader import prompt_missing_buy
                    manual_df = prompt_missing_buy(
                        code=code, qty_needed=unmatched,
                        disposal_date=trade_d, broker=broker
                    )
                    if manual_df is not None and not manual_df.empty:
                        mrow = manual_df.iloc[0]
                        mprice = float(mrow["price"])
                        mbrok  = float(mrow["brokerage"])
                        mgst   = float(mrow["gst"])
                        mcpu   = mprice + (mbrok + mgst) / float(mrow["qty"])
                        queues[code].appendleft(Lot(
                            code          = code,
                            qty           = float(mrow["qty"]),
                            cost_per_unit = mcpu,
                            trade_date    = mrow["trade_date"],
                            broker        = broker,
                        ))
                        # Re-run consume for the newly added lot
                        extra, _ = _fifo_consume(
                            queue=queues[code], qty_needed=unmatched,
                            disposal_date=trade_d,
                            settlement_date=sett_d if pd.notna(sett_d) else None,
                            code=code, name=name,
                            net_proceeds_per_unit=npp,
                            broker=broker, reference=ref, source_file=src,
                        )
                        disposals.extend(extra)
                        missing_flags.pop()   # resolved

        # ── OPTION BUY ───────────────────────────────────────────────────────
        elif txn == "OB":
            if qty <= 0:
                continue
            # Prefer net_proceeds for cost basis — it already includes the contract
            # multiplier (ASX ETOs: price is per underlying share, not per contract).
            if net_proc and abs(net_proc) > 0:
                ppu = abs(net_proc) / qty
            else:
                ppu = price + (brok + gst) / qty
            underlying = code.split("_")[0] if "_" in code else code
            ol = OptionLot(
                code=code, underlying=underlying,
                qty=qty, premium_per_unit=ppu, trade_date=trade_d, broker=broker,
            )
            ol._row_index = row_pos + 2   # stash for OptionFlag later
            option_queues[code].append(ol)
            option_txns.append(OptionTransaction(
                txn_type="OB", trade_date=trade_d, code=code, underlying=underlying,
                qty=qty, price_per_unit=round(ppu, 6),
                total_value=round(ppu * qty, 2),
                broker=broker, source_file=src, row_index=row_pos + 2,
            ))

        # ── OPTION SELL / EXERCISE ────────────────────────────────────────────
        elif txn in ("OS", "OPT"):
            if qty <= 0 or not option_queues[code]:
                continue
            npt           = abs(net_proc) if net_proc else price * qty - brok - gst
            npp           = npt / qty
            underlying_os = code.split("_")[0] if "_" in code else code
            option_txns.append(OptionTransaction(
                txn_type=txn, trade_date=trade_d, code=code, underlying=underlying_os,
                qty=qty, price_per_unit=round(npp, 6),
                total_value=round(npp * qty, 2),
                broker=broker, source_file=src, row_index=row_pos + 2,
            ))
            remaining_opt = qty
            while remaining_opt > 1e-9 and option_queues[code]:
                opt  = option_queues[code][0]
                take = min(opt.qty, remaining_opt)
                held = (trade_d - opt.trade_date).days
                disc = held > 365
                gross = (npp - opt.premium_per_unit) * take
                dg    = gross * 0.5 if (disc and gross > 0) else gross
                disposals.append(DisposalRow(
                    disposal_date=trade_d, settlement_date=sett_d if pd.notna(sett_d) else None,
                    code=code, name=name, qty_disposed=take,
                    proceeds_per_unit=npp, cost_per_unit=opt.premium_per_unit,
                    acquisition_date=opt.trade_date, acquisition_settlement=None,
                    held_days=held, discount_eligible=disc,
                    gross_gain=round(gross, 6), discounted_gain=round(dg, 6),
                    broker=broker, reference=ref, event_type="OPTION_CLOSE", source_file=src,
                ))
                opt.qty       -= take
                remaining_opt -= take
                if opt.qty < 1e-9:
                    option_queues[code].popleft()

        # ── INCOME ───────────────────────────────────────────────────────────
        elif txn in ("DIV", "INT", "LND", "INC"):
            amt = abs(net_proc) if net_proc else abs(price * qty) if price else cv
            income_events.append(IncomeRow(
                event_date  = trade_d,
                code        = code,
                name        = name,
                amount      = round(abs(amt), 2),
                income_type = txn,
                broker      = broker,
                description = str(row.get("description", "")),
                source_file = src,
            ))

        # ── CASH — ignore ─────────────────────────────────────────────────────
        elif txn in ("DEP", "WD"):
            pass

        else:
            print(f"[equity_engine] Unknown txn type '{txn}' on {trade_d} for {code} — skipped")

    # ── Build FY summaries ────────────────────────────────────────────────────
    fy_disp:   dict[str, list[DisposalRow]] = defaultdict(list)
    fy_inc:    dict[str, list[IncomeRow]]   = defaultdict(list)
    fy_miss:   dict[str, list]              = defaultdict(list)

    for d in disposals:
        fy_disp[_fy(d.disposal_date)].append(d)
    for i in income_events:
        fy_inc[_fy(i.event_date)].append(i)
    for m in missing_flags:
        fy_miss[_fy(m.disposal_date)].append(m)

    all_fys = sorted(set(fy_disp) | set(fy_inc) | set(fy_miss))
    fy_summaries: dict[str, FYSummary] = {}
    running_cf = 0.0

    for fy in all_fys:
        s = FYSummary(financial_year=fy)
        external_cf     = cf_losses.get(fy, 0.0)
        s.carry_forward_in = running_cf + external_cf

        rows = fy_disp.get(fy, [])
        s.disposal_count = len(rows)
        s.brokers        = sorted({r.broker for r in rows if r.broker})

        s.total_proceeds  = sum(r.qty_disposed * r.proceeds_per_unit for r in rows)
        s.total_cost_base = sum(r.qty_disposed * r.cost_per_unit      for r in rows)

        disc_gains   = [r.discounted_gain for r in rows if r.discounted_gain > 0]
        disc_losses  = [r.discounted_gain for r in rows if r.discounted_gain < 0]
        gross_gains  = [r.gross_gain      for r in rows if r.gross_gain > 0]

        s.gross_gains  = round(sum(disc_gains),  2)
        s.gross_losses = round(abs(sum(disc_losses)), 2)
        s.cgt_discount_applied = round(
            sum(r.gross_gain - r.discounted_gain for r in rows
                if r.discount_eligible and r.gross_gain > 0), 2
        )

        net_pre = sum(r.discounted_gain for r in rows)
        s.net_capital_gain_pre   = round(net_pre, 2)
        net_after                = net_pre - s.carry_forward_in
        s.net_after_carryforward = round(max(net_after, 0.0), 2)
        s.net_capital_gain       = s.net_after_carryforward

        running_cf = round(abs(net_after), 2) if net_after < 0 else 0.0

        inc_rows       = fy_inc.get(fy, [])
        s.income_count = len(inc_rows)
        s.total_income = round(sum(i.amount for i in inc_rows), 2)
        s.missing_buys = fy_miss.get(fy, [])

        fy_summaries[fy] = s

    # Collect any option lots that were never closed/exercised
    option_flags: list[OptionFlag] = []
    for code, oq in option_queues.items():
        for opt in oq:
            option_flags.append(OptionFlag(
                option_date  = opt.trade_date,
                code         = opt.code,
                underlying   = opt.underlying,
                qty          = opt.qty,
                premium_paid = opt.premium_per_unit,
                broker       = opt.broker,
                row_index    = getattr(opt, "_row_index", 0),
            ))

    return disposals, income_events, fy_summaries, dict(queues), missing_flags, option_flags, option_txns


# ── DataFrame converters ──────────────────────────────────────────────────────

def disposals_to_df(disposals: list[DisposalRow]) -> pd.DataFrame:
    if not disposals:
        return pd.DataFrame()
    return pd.DataFrame([{
        "FY":                   _fy(d.disposal_date),
        "Disposal Date":        d.disposal_date,
        "Settlement Date":      d.settlement_date,
        "Asset Code":           d.code,
        "Asset Name":           d.name,
        "Qty Disposed":         round(d.qty_disposed, 4),
        "Proceeds/Unit ($)":    round(d.proceeds_per_unit, 4),
        "Cost Base/Unit ($)":   round(d.cost_per_unit, 4),
        "Total Proceeds ($)":   round(d.qty_disposed * d.proceeds_per_unit, 2),
        "Total Cost Base ($)":  round(d.qty_disposed * d.cost_per_unit, 2),
        "Acquisition Date":     d.acquisition_date,
        "Acq Settlement Date":  d.acquisition_settlement,
        "Held (days)":          d.held_days,
        "Discount Eligible":    d.discount_eligible,
        "Gross Gain/Loss ($)":  round(d.gross_gain, 2),
        "Discounted Gain ($)":  round(d.discounted_gain, 2),
        "Event Type":           d.event_type,
        "Broker":               d.broker,
        "Reference":            d.reference,
        "Source File":          d.source_file,
    } for d in disposals])


def income_to_df(income_events: list[IncomeRow]) -> pd.DataFrame:
    if not income_events:
        return pd.DataFrame()
    return pd.DataFrame([{
        "FY":           _fy(i.event_date),
        "Event Date":   i.event_date,
        "Asset Code":   i.code,
        "Asset Name":   i.name,
        "Amount ($)":   round(i.amount, 2),
        "Income Type":  i.income_type,
        "Broker":       i.broker,
        "Description":  i.description,
        "Source File":  i.source_file,
    } for i in income_events])


def missing_to_df(missing_flags: list[MissingBuyFlag]) -> pd.DataFrame:
    if not missing_flags:
        return pd.DataFrame()
    return pd.DataFrame([{
        "FY":                   _fy(m.disposal_date),
        "Disposal Date":        m.disposal_date,
        "Asset Code":           m.code,
        "Qty Unmatched":        m.qty_unmatched,
        "Sale Price/Unit ($)":  round(m.proceeds_per_unit, 4) if m.proceeds_per_unit else "—",
        "Broker":               m.broker,
        "Reference":            m.reference,
        "Action Required":      "Provide historical buy details via cost_base_history.csv or manual entry",
    } for m in missing_flags])


def summary_to_df(fy_summaries: dict[str, FYSummary]) -> pd.DataFrame:
    if not fy_summaries:
        return pd.DataFrame()
    return pd.DataFrame([{
        "Financial Year":        s.financial_year,
        "Disposals":             s.disposal_count,
        "Total Proceeds ($)":    s.total_proceeds,
        "Total Cost Base ($)":   s.total_cost_base,
        "Gross Gains ($)":       s.gross_gains,
        "Gross Losses ($)":      s.gross_losses,
        "CGT Discount Applied ($)": s.cgt_discount_applied,
        "Net Cap Gain (pre CF) ($)": s.net_capital_gain_pre,
        "Carry-Forward In ($)":  s.carry_forward_in,
        "Net Taxable Cap Gain ($)": s.net_after_carryforward,
        "Income Events":         s.income_count,
        "Total Income ($)":      s.total_income,
        "Missing Buys":          len(s.missing_buys),
        "Brokers":               ", ".join(s.brokers),
    } for s in sorted(fy_summaries.values(), key=lambda x: x.financial_year)])


def option_flags_to_df(option_flags: list[OptionFlag]) -> pd.DataFrame:
    if not option_flags:
        return pd.DataFrame()
    return pd.DataFrame([{
        "Underlying":            f.underlying,
        "Option Code":           f.code,
        "Open Date":             f.option_date,
        "Contracts":             f.qty,
        "Premium Paid/Unit ($)": round(f.premium_paid, 4),
        "Total Premium at Risk ($)": round(f.premium_paid * f.qty, 2),
        "Broker":                f.broker,
        "Status":                "Unresolved — specify outcome",
    } for f in option_flags])
