"""
shares_trading_taxation_ui.py
Shares Trading Taxation submodule for HSLedger.

Full ATO-compliant equity CGT calculator embedded in the Streamlit frontend:
- Multi-broker Excel/CSV file upload (auto-detected: CommSec, Nabtrade, SelfWealth, Stake, Superhero)
- FIFO engine with full ATO rules (TR 2023/1): brokerage capitalised, 50% CGT discount, loss carry-forward
- Missing buy resolution: interactive data editor per ticker with live FIFO preview
- Final report generation and Excel download
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
from collections import deque
from datetime import date, datetime

import pandas as pd
import streamlit as st

# ── Locate HSLedger_Trading_Module ────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))          # .../frontend/components/
_STREAMLIT_FRONTEND = os.path.dirname(os.path.dirname(_HERE))  # .../streamlit_frontend/
_PROJECT_ROOT = os.path.dirname(_STREAMLIT_FRONTEND)           # project root
_TRADING_MODULE_ROOT = os.path.join(_PROJECT_ROOT, "HSLedger_Trading_Module")

if _TRADING_MODULE_ROOT not in sys.path:
    sys.path.insert(0, _TRADING_MODULE_ROOT)

try:
    from main import run_trading_pipeline, TradingPipelineResult
    from equity.equity_engine import disposals_to_df, income_to_df, summary_to_df
    from output.excel_exporter import export_to_excel
    from shared.local_cost_base_db import ensure_local_db, get_resolution_log
    _MODULE_OK = True
    _IMPORT_ERR = None
except ImportError as _e:
    _MODULE_OK = False
    _IMPORT_ERR = str(_e)

# ── Persistent local DB (shared with standalone module) ───────────────────────
_LOCAL_DB_PATH = os.path.join(_TRADING_MODULE_ROOT, "data", "local_cost_base_db.json")

# ── Session state prefix (avoids collisions with other modules) ───────────────
_P = "stt_"


# ── Session state helpers ─────────────────────────────────────────────────────

def _init() -> None:
    if _MODULE_OK:
        os.makedirs(os.path.dirname(_LOCAL_DB_PATH), exist_ok=True)
        ensure_local_db(_LOCAL_DB_PATH)

    defaults: dict = {
        f"{_P}result":            None,
        f"{_P}final_result":      None,
        f"{_P}report_generated":  False,
        f"{_P}temp_dir":          None,
        f"{_P}log":               "",
        f"{_P}stored_fy":         None,
        f"{_P}buy_records":       {},
        f"{_P}uploader_key":      0,
        f"{_P}selected_ticker":   None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _get(key: str):
    return st.session_state.get(f"{_P}{key}")


def _set(key: str, value) -> None:
    st.session_state[f"{_P}{key}"] = value


# ── File handling ─────────────────────────────────────────────────────────────

def _save_uploads(uploaded_files) -> str:
    tmp = _get("temp_dir")
    if tmp and os.path.isdir(tmp):
        for f in os.listdir(tmp):
            try:
                os.remove(os.path.join(tmp, f))
            except OSError:
                pass
    else:
        tmp = tempfile.mkdtemp(prefix="stt_broker_")
        _set("temp_dir", tmp)

    for uf in uploaded_files:
        dest = os.path.join(tmp, uf.name)
        with open(dest, "wb") as fh:
            fh.write(uf.getvalue())
    return tmp


def _cleanup() -> None:
    tmp = _get("temp_dir")
    if tmp and os.path.isdir(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    _set("temp_dir", None)


def _quick_detect(uf) -> dict:
    """Detect broker for an uploaded file without a full pipeline run."""
    try:
        import tempfile as _tf
        from shared.detect_file_type import detect as _det_fn
        ext = os.path.splitext(uf.name)[1].lower()
        with _tf.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(uf.getvalue())
            tmp_path = tmp.name
        det = _det_fn(tmp_path)
        os.unlink(tmp_path)
        return det
    except Exception:
        return {"broker": "unknown", "confidence": 0.0, "asset_class": "unknown"}


# ── Pipeline runner ───────────────────────────────────────────────────────────

def _run_pipeline(source: str, fy: str | None) -> TradingPipelineResult:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = run_trading_pipeline(
            source=source,
            local_db_path=_LOCAL_DB_PATH,
            target_fy=fy,
            interactive_missing=False,
        )
    _set("log", buf.getvalue())
    return result


def _run_final_pipeline(source: str, fy: str | None) -> TradingPipelineResult:
    """Re-run with any manually entered buy lots included as cost_base_history."""
    buy_records: dict = _get("buy_records") or {}
    all_rows = []

    for ticker, df in buy_records.items():
        for lot in _parse_buy_df(df, ticker):
            all_rows.append({
                "stock_code":    ticker,
                "purchase_date": lot["date"].strftime("%d/%m/%Y"),
                "quantity":      lot["qty"],
                "unit_price":    lot["price"],
                "brokerage":     lot["brokerage"],
                "gst":           lot["gst"],
                "source":        "manual_entry",
            })

    tmp_csv_dir = tempfile.mkdtemp(prefix="stt_hist_")
    hist_csv = None
    try:
        if all_rows:
            hist_csv = os.path.join(tmp_csv_dir, "_manual_entries.csv")
            pd.DataFrame(all_rows).to_csv(hist_csv, index=False)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            final = run_trading_pipeline(
                source=source,
                cost_base_history_path=hist_csv,
                local_db_path=_LOCAL_DB_PATH,
                target_fy=fy,
                interactive_missing=False,
            )
        _set("log", (_get("log") or "") + "\n\n--- FINAL RUN ---\n" + buf.getvalue())
    finally:
        shutil.rmtree(tmp_csv_dir, ignore_errors=True)

    return final


# ── Manual buy entry helpers ──────────────────────────────────────────────────

def _empty_buy_df() -> pd.DataFrame:
    return pd.DataFrame({
        "Purchase Date (dd/mm/yyyy)": pd.Series([], dtype=str),
        "Quantity":                   pd.Series([], dtype=float),
        "Unit Price ($)":             pd.Series([], dtype=float),
        "Brokerage ($)":              pd.Series([], dtype=float),
        "GST ($)":                    pd.Series([], dtype=float),
    })


def _parse_buy_df(df: pd.DataFrame, ticker: str) -> list[dict]:
    lots = []
    for _, row in df.iterrows():
        date_str = str(row.get("Purchase Date (dd/mm/yyyy)", "") or "").strip()
        qty      = float(row.get("Quantity", 0) or 0)
        price    = float(row.get("Unit Price ($)", 0) or 0)
        brok     = float(row.get("Brokerage ($)", 0) or 0)
        gst      = float(row.get("GST ($)", 0) or 0)

        if not date_str or qty <= 0 or price <= 0:
            continue
        try:
            buy_date = datetime.strptime(date_str, "%d/%m/%Y").date()
        except ValueError:
            continue

        cpu = price + (brok + gst) / qty if qty > 0 else price
        lots.append({
            "ticker": ticker, "date": buy_date,
            "qty": qty, "qty_remaining": qty,
            "price": price, "brokerage": brok, "gst": gst, "cpu": cpu,
        })
    return lots


def _fifo_preview(sells: list[dict], buy_lots: list[dict]) -> pd.DataFrame:
    if not buy_lots:
        return pd.DataFrame()

    queue = deque(sorted(buy_lots, key=lambda x: x["date"]))
    rows: list[dict] = []

    for sell in sorted(sells, key=lambda x: x["date"]):
        remaining  = sell["qty"]
        sale_price = sell.get("proceeds_per_unit") or 0.0

        while remaining > 1e-9 and queue:
            lot  = queue[0]
            take = min(lot["qty_remaining"], remaining)
            held = (sell["date"] - lot["date"]).days
            disc = held > 365 and (sale_price - lot["cpu"]) * take > 0
            gross = round((sale_price - lot["cpu"]) * take, 2)
            after = round(gross * 0.5 if disc else gross, 2)

            rows.append({
                "Sell Date":            sell["date"].strftime("%d/%m/%Y"),
                "Qty":                  int(take),
                "Buy Date":             lot["date"].strftime("%d/%m/%Y"),
                "Days Held":            held,
                "CGT Discount":         "Yes (50%)" if disc else "No",
                "Sale Price/Unit ($)":  round(sale_price, 4),
                "Cost/Unit ($)":        round(lot["cpu"], 4),
                "Gross Gain/Loss ($)":  gross,
                "After Discount ($)":   after,
            })

            lot["qty_remaining"] -= take
            remaining -= take
            if lot["qty_remaining"] < 1e-9:
                queue.popleft()

        if remaining > 1e-9:
            rows.append({
                "Sell Date":            sell["date"].strftime("%d/%m/%Y"),
                "Qty":                  f"{int(remaining)} UNMATCHED",
                "Buy Date":             "—",
                "Days Held":            "—",
                "CGT Discount":         "—",
                "Sale Price/Unit ($)":  round(sale_price, 4) if sale_price else "—",
                "Cost/Unit ($)":        "—",
                "Gross Gain/Loss ($)":  "—",
                "After Discount ($)":   "—",
            })

    return pd.DataFrame(rows)


def _group_flags(flags) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for f in flags:
        grouped.setdefault(f.code, []).append({
            "date":              f.disposal_date,
            "qty":               f.qty_unmatched,
            "broker":            f.broker or "—",
            "proceeds_per_unit": f.proceeds_per_unit,
        })
    return grouped


def _coverage(ticker: str, sells: list[dict]) -> tuple[str, str]:
    buy_records: dict = _get("buy_records") or {}
    df   = buy_records.get(ticker, _empty_buy_df())
    lots = _parse_buy_df(df, ticker)
    total_sell = sum(s["qty"] for s in sells)
    total_buy  = sum(l["qty"] for l in lots)
    if total_buy == 0:
        return "⬜", "Not started"
    if total_buy >= total_sell - 1e-6:
        return "✅", f"Covered {total_sell:,.0f} / {total_sell:,.0f} shares"
    return "⚠️", f"Partial {total_buy:,.0f} / {total_sell:,.0f} shares"


# ── Options resolver helpers ──────────────────────────────────────────────────

_OPT_OUTCOMES = [
    "— select outcome —",
    "Expired worthless",
    "Sold / Closed (enter close price)",
    "Exercised into shares",
    "Still held (open position)",
]

_OPT_OUTCOME_HELP = {
    "Expired worthless":                 "Full premium is a capital loss in the year of expiry.",
    "Sold / Closed (enter close price)": "Enter the net proceeds you received to close the position.",
    "Exercised into shares":             "No CGT event now — premium is added to the cost base of shares acquired.",
    "Still held (open position)":        "No CGT event yet — position remains open.",
}


def _opt_key(code: str, field: str) -> str:
    return f"{_P}opt_{code}_{field}"


def _opt_cgt_preview(flag, outcome: str, close_price: float) -> tuple[str, float, float]:
    """Returns (label, gain, loss)."""
    qty = flag.qty
    ppu = flag.premium_paid
    if outcome == "Expired worthless":
        loss = round(ppu * qty, 2)
        return f"Capital loss  −${loss:,.2f}  (full premium written off)", 0.0, loss
    if outcome == "Sold / Closed (enter close price)":
        if close_price <= 0:
            return "Enter close price to see estimate", 0.0, 0.0
        g = round((close_price - ppu) * qty, 2)
        if g >= 0:
            return f"Capital gain  +${g:,.2f}", g, 0.0
        return f"Capital loss  −${abs(g):,.2f}", 0.0, abs(g)
    if outcome == "Exercised into shares":
        return "No immediate CGT — premium carries into share cost base", 0.0, 0.0
    if outcome == "Still held (open position)":
        return "No CGT event yet", 0.0, 0.0
    return "Select an outcome above", 0.0, 0.0


def _render_option_entry(flag, idx: int) -> None:
    code_key    = f"{flag.code}_{idx}"
    outcome     = st.session_state.get(_opt_key(code_key, "outcome"), "— select outcome —")
    resolved    = outcome not in ("— select outcome —", "", None)
    days_open   = (date.today() - flag.option_date).days
    disc_eligible = days_open > 365
    status_icon = "✅" if resolved else "⬜"

    with st.expander(
        f"{status_icon}  {flag.code}  ·  {int(flag.qty)} contract(s)"
        + (f"  ·  *{outcome}*" if resolved else "  ·  *Awaiting outcome*"),
        expanded=not resolved,
    ):
        left_col, right_col = st.columns([1, 1], gap="large")

        with left_col:
            st.markdown("**Position Details**")
            detail_rows = [
                ("Open Date",      flag.option_date.strftime("%d/%m/%Y")),
                ("Contracts",      f"{int(flag.qty)}"),
                ("Premium / Unit", f"${flag.premium_paid:.4f}"),
                ("Total Premium",  f"${flag.premium_paid * flag.qty:,.2f}"),
                ("Days Open",      f"{days_open:,} days"),
                ("CGT Discount",   "Eligible (>12 months)" if disc_eligible else f"Not yet — {days_open} days held"),
            ]
            if flag.broker:
                detail_rows.append(("Broker", flag.broker))
            for lbl, val in detail_rows:
                a, b = st.columns([1, 1])
                a.caption(lbl)
                b.markdown(f"**{val}**")

        with right_col:
            st.markdown("**Select Outcome**")
            st.selectbox(
                "What happened to this position?",
                options=_OPT_OUTCOMES,
                key=_opt_key(code_key, "outcome"),
                label_visibility="collapsed",
            )
            outcome = st.session_state.get(_opt_key(code_key, "outcome"), "— select outcome —")

            if outcome in _OPT_OUTCOME_HELP:
                st.caption(_OPT_OUTCOME_HELP[outcome])

            close_price = 0.0
            if outcome == "Sold / Closed (enter close price)":
                st.markdown("Net proceeds per contract ($)")
                close_price = st.number_input(
                    "close_price",
                    min_value=0.0, step=0.01, format="%.4f",
                    key=_opt_key(code_key, "close_price"),
                    label_visibility="collapsed",
                )

            if outcome not in ("— select outcome —", "", None):
                st.markdown("**Est. CGT Impact**")
                lbl, gain, loss = _opt_cgt_preview(flag, outcome, close_price)
                if gain > 0:
                    st.success(lbl)
                elif loss > 0:
                    st.error(lbl)
                else:
                    st.info(lbl)
            else:
                st.caption("Select an outcome above to preview the tax impact.")


def _render_options_tab(result: TradingPipelineResult) -> None:
    flags = result.option_flags
    txns  = getattr(result, "option_transactions", [])

    # ── Transaction log (collapsible) ─────────────────────────────────────────
    if txns:
        unresolved_codes = {f.code for f in flags}
        txn_label = {"OB": "Buy (OB)", "OS": "Sell / Close (OS)", "OPT": "Exercise (OPT)"}
        rows = []
        for t in txns:
            if t.txn_type == "OB":
                status = "⚠ Unresolved" if t.code in unresolved_codes else "✅ Matched"
            else:
                status = "📋 Closed"
            rows.append({
                "Status":             status,
                "Type":               txn_label.get(t.txn_type, t.txn_type),
                "Date":               t.trade_date.strftime("%d/%m/%Y"),
                "Option Code":        t.code,
                "Underlying":         t.underlying,
                "Contracts":          int(t.qty),
                "Price/Contract ($)": round(t.price_per_unit, 4),
                "Total Value ($)":    round(t.total_value, 2),
                "Broker":             t.broker or "—",
            })
        txn_df = pd.DataFrame(rows)

        n_unresolved = sum(1 for r in rows if "Unresolved" in r["Status"])
        n_matched    = sum(1 for r in rows if "Matched"    in r["Status"])
        n_closed     = sum(1 for r in rows if "Closed"     in r["Status"])

        with st.expander(
            f"📋  All Detected Option Transactions  ({len(txns)} total · "
            f"{n_unresolved} unresolved · {n_matched} matched · {n_closed} closed)",
            expanded=False,
        ):
            def _status_colour(v: str) -> str:
                if "Unresolved" in str(v): return "color:#b45309;font-weight:600"
                if "Matched"    in str(v): return "color:#16a34a;font-weight:600"
                if "Closed"     in str(v): return "color:#2563eb"
                return ""
            try:
                st.dataframe(
                    txn_df.style.map(_status_colour, subset=["Status"]),
                    use_container_width=True, hide_index=True,
                )
            except Exception:
                st.dataframe(txn_df, use_container_width=True, hide_index=True)

    if not flags:
        st.success("All option transactions are matched — no positions need resolution.")
        return

    st.divider()

    # ── Overview metrics ───────────────────────────────────────────────────────
    by_underlying: dict[str, list] = {}
    for f in flags:
        by_underlying.setdefault(f.underlying, []).append(f)
    underlyings = sorted(by_underlying.keys())

    total_contracts = sum(f.qty for f in flags)
    total_at_risk   = sum(f.premium_paid * f.qty for f in flags)

    # Count globally resolved
    all_resolved = sum(
        1 for f in flags
        for i, ff in enumerate(by_underlying[f.underlying])
        if ff is f and st.session_state.get(
            _opt_key(f"{f.code}_{i}", "outcome"), "— select outcome —"
        ) not in ("— select outcome —", "", None)
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Open Positions",     len(flags))
    m2.metric("Underlying Assets",  len(underlyings))
    m3.metric("Total Contracts",    f"{total_contracts:,.0f}")
    m4.metric("Premium at Risk",    f"${total_at_risk:,.2f}")

    resolved_underlyings = sum(
        1 for u in underlyings
        if all(
            st.session_state.get(_opt_key(f"{f.code}_{i}", "outcome"), "— select outcome —")
            not in ("— select outcome —", "", None)
            for i, f in enumerate(by_underlying[u])
        )
    )
    st.progress(
        resolved_underlyings / len(underlyings),
        text=f"{resolved_underlyings} of {len(underlyings)} underlying(s) fully resolved",
    )

    st.caption(
        "Select what happened to each open position. "
        "**Expired worthless** → capital loss (full premium). "
        "**Sold / Closed** → enter proceeds to calculate gain or loss. "
        "**Exercised into shares** → no CGT event now, premium rolls into the share cost base. "
        "**Still held** → no event yet."
    )
    st.divider()

    # ── Two-panel resolver ─────────────────────────────────────────────────────
    sel_key = f"{_P}opt_selected_underlying"
    if not st.session_state.get(sel_key) or st.session_state[sel_key] not in underlyings:
        first_unresolved = next(
            (u for u in underlyings
             if any(
                 st.session_state.get(_opt_key(f"{f.code}_{i}", "outcome"), "— select outcome —")
                 in ("— select outcome —", "", None)
                 for i, f in enumerate(by_underlying[u])
             )),
            underlyings[0],
        )
        st.session_state[sel_key] = first_unresolved

    nav_col, entry_col = st.columns([1, 3], gap="large")

    with nav_col:
        st.markdown("**Underlyings**")
        for u in underlyings:
            positions  = by_underlying[u]
            n          = len(positions)
            res_u      = sum(
                1 for i, f in enumerate(positions)
                if st.session_state.get(_opt_key(f"{f.code}_{i}", "outcome"), "— select outcome —")
                   not in ("— select outcome —", "", None)
            )
            u_icon = "✅" if res_u == n else ("⚠️" if res_u > 0 else "⬜")
            # Estimate total impact for this underlying
            u_gain = u_loss = 0.0
            for i, f in enumerate(positions):
                ck = f"{f.code}_{i}"
                oc = st.session_state.get(_opt_key(ck, "outcome"), "")
                cp = float(st.session_state.get(_opt_key(ck, "close_price"), 0) or 0)
                _, g, l = _opt_cgt_preview(f, oc, cp)
                u_gain += g; u_loss += l
            impact = (f"  +${u_gain:,.0f}" if u_gain else "") + (f"  −${u_loss:,.0f}" if u_loss else "")

            is_sel = st.session_state.get(sel_key) == u
            btn_label = f"{u_icon}  {u}  ({res_u}/{n}){impact}"
            if st.button(btn_label, key=f"{_P}optnav_{u}", use_container_width=True,
                         type="primary" if is_sel else "secondary"):
                st.session_state[sel_key] = u
                st.rerun()

    with entry_col:
        sel = st.session_state.get(sel_key)
        if sel and sel in by_underlying:
            positions = by_underlying[sel]
            res_count = sum(
                1 for i, f in enumerate(positions)
                if st.session_state.get(_opt_key(f"{f.code}_{i}", "outcome"), "— select outcome —")
                   not in ("— select outcome —", "", None)
            )
            st.markdown(
                f"### {sel} &nbsp; "
                f"<span style='font-size:0.9rem;font-weight:400;color:gray'>"
                f"{res_count} / {len(positions)} resolved</span>",
                unsafe_allow_html=True,
            )

            for i, flag in enumerate(positions):
                _render_option_entry(flag, i)

            # ── Subtotal ───────────────────────────────────────────────────────
            st.divider()
            sub_gain = sub_loss = 0.0
            for i, flag in enumerate(positions):
                ck = f"{flag.code}_{i}"
                oc = st.session_state.get(_opt_key(ck, "outcome"), "")
                cp = float(st.session_state.get(_opt_key(ck, "close_price"), 0) or 0)
                _, g, l = _opt_cgt_preview(flag, oc, cp)
                sub_gain += g; sub_loss += l

            if sub_gain or sub_loss:
                st.markdown(f"**Estimated CGT impact for {sel}**")
                s1, s2, s3 = st.columns(3)
                if sub_gain:
                    s1.success(f"Est. gain  +${sub_gain:,.2f}")
                if sub_loss:
                    s2.error(f"Est. loss  −${sub_loss:,.2f}")
                net = sub_gain - sub_loss
                if sub_gain and sub_loss:
                    (s3.success if net >= 0 else s3.error)(
                        f"Net  {'+'if net>=0 else '−'}${abs(net):,.2f}"
                    )


# ── Tab renderers ─────────────────────────────────────────────────────────────

def _render_summary_tab(result: TradingPipelineResult) -> None:
    df = result.summary_df
    if not df.empty:
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No FY summary data. Ensure the selected financial year matches your transaction dates.")


def _render_disposals_tab(result: TradingPipelineResult, key_suffix: str = "preview") -> None:
    df = result.disposals_df
    if df.empty:
        st.info("No disposal events found.")
        return
    tickers = ["All"] + sorted(df["Asset Code"].dropna().unique().tolist())
    sel = st.selectbox("Filter by asset", tickers, key=f"{_P}disp_filter_{key_suffix}")
    if sel != "All":
        df = df[df["Asset Code"] == sel]
    st.caption(f"{len(df)} disposal row(s)")
    _gl_cols = [c for c in df.columns if c in ("Gross Gain/Loss ($)", "Discounted Gain ($)")]
    if _gl_cols:
        def _colour_disp(v):
            try:
                n = float(v)
                if n > 0: return "color: #16a34a; font-weight:600"
                if n < 0: return "color: #dc2626; font-weight:600"
            except (TypeError, ValueError):
                pass
            return ""
        try:
            st.dataframe(df.style.map(_colour_disp, subset=_gl_cols), use_container_width=True, hide_index=True)
        except Exception:
            st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)


def _render_income_tab(result: TradingPipelineResult) -> None:
    df = result.income_df
    if df.empty:
        st.info("No income events (dividends, interest, lending) found.")
        return
    total = df["Amount ($)"].sum() if "Amount ($)" in df.columns else 0
    st.metric("Total Income", f"${total:,.2f}")
    _TYPE_LABELS = {"DIV": "Dividends", "INT": "Interest", "LND": "Lending Income", "INC": "Other Income"}
    if "Income Type" in df.columns and df["Income Type"].nunique() > 1:
        for itype, grp in df.groupby("Income Type"):
            subtotal = grp["Amount ($)"].sum() if "Amount ($)" in grp.columns else 0
            label = _TYPE_LABELS.get(str(itype).upper(), str(itype))
            with st.expander(f"{label}  ·  {len(grp)} event(s)  ·  ${subtotal:,.2f}", expanded=True):
                st.dataframe(grp, use_container_width=True, hide_index=True)
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)


def _render_open_positions_tab(result: TradingPipelineResult) -> None:
    if not result.open_positions:
        st.info("No open positions — all lots were fully disposed of.")
        return
    rows = []
    for code, queue in result.open_positions.items():
        for lot in queue:
            held = (date.today() - lot.trade_date).days
            rows.append({
                "Asset":                  code,
                "Qty":                    round(lot.qty, 4),
                "Cost/Unit ($)":          round(lot.cost_per_unit, 4),
                "Total Cost ($)":         round(lot.qty * lot.cost_per_unit, 2),
                "Acquired":               lot.trade_date,
                "Days Held":              held,
                "CGT Discount Eligible":  "Yes" if held > 365 else "No",
                "Broker":                 lot.broker,
            })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    total_cost_basis = sum(r["Total Cost ($)"] for r in rows)
    _oc1, _oc2 = st.columns(2)
    _oc1.metric("Total Cost Basis", f"${total_cost_basis:,.2f}")
    _oc2.caption(f"{len(rows)} open lot(s) across {len(result.open_positions)} asset(s)")


def _render_ticker_entry(ticker: str, sells: list[dict]) -> None:
    st.subheader(ticker)
    total_qty = sum(s["qty"] for s in sells)
    st.caption(f"{len(sells)} unmatched sell event(s) · {total_qty:,.0f} total shares")

    sells_df = pd.DataFrame([{
        "Sell Date":           s["date"].strftime("%d/%m/%Y"),
        "Qty":                 f"{int(s['qty']):,}",
        "Broker":              (s["broker"] or "—").title(),
        "Sale Price/Unit ($)": f"${s['proceeds_per_unit']:.4f}" if s.get("proceeds_per_unit") else "—",
    } for s in sorted(sells, key=lambda x: x["date"])])
    st.dataframe(sells_df, use_container_width=True, hide_index=True)

    st.markdown(f"**Enter purchase history for {ticker}:**")
    st.caption("One row per purchase lot · date format: dd/mm/yyyy · FIFO applies oldest buy first")

    buy_records: dict = _get("buy_records") or {}
    existing = buy_records.get(ticker)
    if existing is None or (hasattr(existing, "empty") and existing.empty):
        existing = _empty_buy_df()

    edited = st.data_editor(
        existing,
        key=f"{_P}editor_{ticker}",
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "Purchase Date (dd/mm/yyyy)": st.column_config.TextColumn(
                "Purchase Date", help="Format: dd/mm/yyyy  e.g. 01/07/2022", width="medium"
            ),
            "Quantity":     st.column_config.NumberColumn("Qty (shares)", min_value=0.0001, step=1.0, format="%.4f"),
            "Unit Price ($)": st.column_config.NumberColumn("Unit Price ($)", min_value=0.0001, step=0.01, format="%.4f"),
            "Brokerage ($)": st.column_config.NumberColumn("Brokerage ($)", min_value=0.0, step=0.01, format="%.2f"),
            "GST ($)":       st.column_config.NumberColumn("GST ($)",       min_value=0.0, step=0.01, format="%.2f"),
        },
    )

    # Persist edited data
    updated = (buy_records or {}).copy()
    updated[ticker] = edited
    _set("buy_records", updated)

    # FIFO preview
    lots = _parse_buy_df(edited, ticker)
    if not lots:
        st.info("Add at least one valid purchase row to see the FIFO tax estimate.")
        return

    preview = _fifo_preview(sells, lots)
    if preview.empty:
        return

    st.markdown("**FIFO Preview — estimated CGT impact:**")

    unmatched_mask = preview["Qty"].astype(str).str.contains("UNMATCHED", na=False)
    matched   = preview[~unmatched_mask].copy()
    unmatched = preview[unmatched_mask].copy()

    if not matched.empty:
        def _colour(v):
            try:
                n = float(v)
                if n > 0:  return "color: #16a34a; font-weight:600"
                if n < 0:  return "color: #dc2626; font-weight:600"
            except (TypeError, ValueError):
                pass
            return ""
        try:
            styled = matched.style.map(_colour, subset=["Gross Gain/Loss ($)", "After Discount ($)"])
            st.dataframe(styled, use_container_width=True, hide_index=True)
        except Exception:
            st.dataframe(matched, use_container_width=True, hide_index=True)

    if not unmatched.empty:
        st.warning(f"{len(unmatched)} sell event(s) still unmatched — add more purchase rows above.")

    # Summary
    after_vals = []
    for v in matched.get("After Discount ($)", []):
        try:
            after_vals.append(float(v))
        except (TypeError, ValueError):
            pass

    est_gain = round(sum(v for v in after_vals if v > 0), 2)
    est_loss = round(sum(abs(v) for v in after_vals if v < 0), 2)
    icon, status = _coverage(ticker, sells)

    sc1, sc2, sc3 = st.columns(3)
    sc1.info(f"Coverage: {icon} {status}")
    disc_rows = matched[matched["CGT Discount"] == "Yes (50%)"].shape[0] if "CGT Discount" in matched.columns else 0
    if est_gain:
        gain_label = "Est. taxable gain (after 50% CGT discount)" if disc_rows else "Est. gain"
        sc2.success(f"{gain_label}: ${est_gain:,.2f}")
    if est_loss:
        sc3.error(f"Est. loss: ${est_loss:,.2f}")

    if disc_rows:
        st.caption(f"50% CGT discount applied to {disc_rows} lot(s) held >12 months — shown in 'After Discount ($)' column. Est. taxable gain reflects this reduction.")


def _render_missing_buys_tab(result: TradingPipelineResult) -> None:
    flags = result.missing_flags
    if not flags:
        st.success("All sell transactions are fully matched — no missing buy records.")
        return

    by_ticker = _group_flags(flags)
    tickers   = sorted(by_ticker.keys())

    full    = [t for t in tickers if _coverage(t, by_ticker[t])[0] == "✅"]
    partial = [t for t in tickers if _coverage(t, by_ticker[t])[0] == "⚠️"]
    pending = [t for t in tickers if _coverage(t, by_ticker[t])[0] == "⬜"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Tickers flagged",  len(tickers))
    m2.metric("Fully covered",    len(full))
    m3.metric("Partial",          len(partial))
    m4.metric("Not started",      len(pending))

    if tickers:
        st.progress(len(full) / len(tickers), text=f"{len(full)} of {len(tickers)} tickers fully covered")

    st.caption(
        "Enter the original purchase details for each unmatched sell. "
        "The engine applies FIFO (oldest buy matched to earliest sell) and "
        "automatically applies the 50% CGT discount to lots held over 12 months. "
        "Click **Generate Final Tax Report** below once done."
    )
    st.divider()

    # Initialise selected ticker
    sel_key = f"{_P}selected_ticker"
    if not st.session_state.get(sel_key) or st.session_state[sel_key] not in tickers:
        first = next((t for t in tickers if _coverage(t, by_ticker[t])[0] != "✅"), tickers[0])
        st.session_state[sel_key] = first

    left, right = st.columns([1, 3], gap="large")

    with left:
        st.subheader("Tickers")
        for t in tickers:
            icon, _ = _coverage(t, by_ticker[t])
            n = len(by_ticker[t])
            label = f"{icon} {t} ({n} sell{'s' if n > 1 else ''})"
            if st.button(label, key=f"{_P}nav_{t}", use_container_width=True,
                         type="primary" if st.session_state.get(sel_key) == t else "secondary"):
                st.session_state[sel_key] = t

    with right:
        sel = st.session_state.get(sel_key)
        if sel and sel in by_ticker:
            _render_ticker_entry(sel, by_ticker[sel])


# ── Export ─────────────────────────────────────────────────────────────────────

def _render_export(result: TradingPipelineResult, fy: str | None) -> None:
    resolution_log = get_resolution_log(_LOCAL_DB_PATH) if _MODULE_OK else []
    excel_bytes = export_to_excel(
        disposals      = result.disposals,
        income_events  = result.income_events,
        fy_summaries   = result.fy_summaries,
        missing_flags  = result.missing_flags,
        open_positions = result.open_positions,
        duplicates_df  = result.duplicates_df,
        resolution_log = resolution_log,
        output_path    = None,
    )
    fy_label = fy or "all-years"
    st.download_button(
        label     = "📥 Download Full Tax Report (.xlsx)",
        data      = excel_bytes,
        file_name = f"HSLedger_Equity_CGT_{fy_label}.xlsx",
        mime      = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type      = "primary",
        use_container_width=True,
    )


# ── Main render ───────────────────────────────────────────────────────────────

def render() -> None:
    _init()

    st.markdown(
        "<h4 style='margin:0 0 0.25rem 0'>📈 Trading › Shares Trading Taxation</h4>",
        unsafe_allow_html=True,
    )
    st.caption("Australian equity CGT · ATO rules (TR 2023/1) · FIFO · brokerage capitalised · 50% discount")

    if not _MODULE_OK:
        st.error(f"Trading module import error: {_IMPORT_ERR}")
        st.info(
            "Ensure `HSLedger_Trading_Module` is in the project root and all dependencies are installed "
            "(`pip install -r requirements.txt`)."
        )
        return

    st.divider()

    # ── Upload & configuration ─────────────────────────────────────────────────
    col_upload, col_cfg = st.columns([3, 1])

    with col_upload:
        st.markdown("**Upload broker transaction files**")
        st.caption("Auto-detected: CommSec · Nabtrade · SelfWealth · Stake · Superhero · Formats: .xlsx .xls .csv")
        uploaded = st.file_uploader(
            "Drop files here",
            type=["xlsx", "xls", "csv"],
            accept_multiple_files=True,
            key=f"stt_uploader_{_get('uploader_key')}",
            label_visibility="collapsed",
        )
        if uploaded:
            _det_rows = []
            for _uf in uploaded:
                _d = _quick_detect(_uf)
                _icon = "✅" if _d["confidence"] >= 0.5 else "⚠️"
                _broker = _d["broker"].title() if _d["confidence"] >= 0.5 else "Unknown"
                _det_rows.append({
                    "": _icon,
                    "File": _uf.name,
                    "Detected Broker": _broker,
                    "Confidence": f"{_d['confidence']:.0%}",
                })
            st.dataframe(pd.DataFrame(_det_rows), use_container_width=True, hide_index=True)

    with col_cfg:
        st.markdown("**Financial Year**")
        _prev_result = _get("result")
        if _prev_result and _prev_result.fy_summaries:
            _det_fys = sorted(_prev_result.fy_summaries.keys(), reverse=True)
            fy_options = _det_fys + ["All years"]
        else:
            fy_options = ["2024-25", "2023-24", "2022-23", "All years"]
        fy_sel = st.selectbox("FY", fy_options, index=0, label_visibility="collapsed", key=f"{_P}fy_sel")
        target_fy: str | None = None if fy_sel == "All years" else fy_sel

        st.markdown("")
        process_btn = st.button(
            "▶ Process Files",
            type="primary",
            disabled=not uploaded,
            use_container_width=True,
        )
        reset_btn = st.button(
            "↺ Reset",
            disabled=_get("result") is None,
            use_container_width=True,
        )

    # ── Reset ─────────────────────────────────────────────────────────────────
    if reset_btn:
        _cleanup()
        _set("result",           None)
        _set("final_result",     None)
        _set("report_generated", False)
        _set("log",              "")
        _set("stored_fy",        None)
        _set("buy_records",      {})
        _set("selected_ticker",  None)
        _set("uploader_key",     (_get("uploader_key") or 0) + 1)
        st.rerun()

    # ── Process ───────────────────────────────────────────────────────────────
    if process_btn and uploaded:
        with st.status(f"Processing {len(uploaded)} file(s)…", expanded=True) as _proc_status:
            st.write("Saving uploads to workspace…")
            source_dir = _save_uploads(uploaded)
            st.write("Running FIFO CGT engine…")
            result = _run_pipeline(source_dir, target_fy)
            _proc_status.update(label="Processing complete!", state="complete", expanded=False)

        _set("result", result)
        _set("stored_fy", target_fy)
        _set("final_result", None)
        _set("report_generated", False)
        _set("buy_records", {})
        _set("selected_ticker", None)
        st.success(f"Processed {len(uploaded)} file(s) successfully.")

    result: TradingPipelineResult | None = _get("result")

    if result is None:
        st.info(
            "Upload your broker transaction files above then click **▶ Process Files**. "
            "The engine auto-detects broker format, deduplicates records, and calculates "
            "capital gains using the ATO FIFO method."
        )
        return

    stored_fy: str | None = _get("stored_fy")
    missing_count = len(result.missing_flags)
    options_count = len(result.option_flags)

    # ── Key metrics ────────────────────────────────────────────────────────────
    st.divider()
    total_gains = total_losses = net_taxable = cgt_discount = 0.0
    total_disposals = 0
    for s in result.fy_summaries.values():
        total_gains     += s.gross_gains
        total_losses    += s.gross_losses
        net_taxable     += s.net_after_carryforward
        cgt_discount    += s.cgt_discount_applied
        total_disposals += s.disposal_count

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Disposals",              total_disposals)
    m2.metric("Gross Capital Gains",    f"${total_gains:,.2f}")
    m3.metric("Gross Capital Losses",   f"${total_losses:,.2f}")
    m4.metric("CGT Discount Applied",   f"${cgt_discount:,.2f}")
    m5.metric(
        "Net Taxable Gain",
        f"${net_taxable:,.2f}",
        delta=round(net_taxable, 2),
        delta_color="normal",
    )

    # ── Action required callout ────────────────────────────────────────────────
    if missing_count > 0:
        st.error(
            f"**Action required — {missing_count} unmatched sell(s):** open the **Missing Buys** tab "
            "and enter the original purchase details for each flagged ticker."
        )
    if options_count > 0:
        st.warning(
            f"**{options_count} open option position(s):** open the **Options** tab "
            "and select what happened (expired, sold, exercised, or still held)."
        )
    if not missing_count and not options_count:
        st.success("All transactions matched — ready to generate your final tax report.")

    # ── Results tabs ───────────────────────────────────────────────────────────
    tab_names = ["📊 Summary", "📋 CGT Disposals", "💰 Income", "📂 Open Positions"]
    if missing_count > 0:
        tab_names.append(f"⚠ Missing Buys ({missing_count})")
    if options_count > 0:
        tab_names.append(f"⚙ Options ({options_count})")

    tabs = st.tabs(tab_names)

    with tabs[0]:
        _render_summary_tab(result)
    with tabs[1]:
        _render_disposals_tab(result)
    with tabs[2]:
        _render_income_tab(result)
    with tabs[3]:
        _render_open_positions_tab(result)

    tab_idx = 4
    if missing_count > 0:
        with tabs[tab_idx]:
            _render_missing_buys_tab(result)
        tab_idx += 1
    if options_count > 0:
        with tabs[tab_idx]:
            _render_options_tab(result)

    # ── Generate final report ──────────────────────────────────────────────────
    st.divider()
    source_dir = _get("temp_dir")
    by_ticker  = _group_flags(result.missing_flags)
    all_covered = all(_coverage(t, by_ticker[t])[0] == "✅" for t in by_ticker) if by_ticker else True
    remaining_uncovered = len([t for t in by_ticker if _coverage(t, by_ticker[t])[0] != "✅"])

    btn_label = "Generate Final Tax Report"
    if missing_count > 0 and not all_covered:
        btn_label = f"Generate Final Tax Report  ({remaining_uncovered} ticker(s) still need buy records)"

    c1, c2 = st.columns([2, 1])
    with c1:
        gen_clicked = st.button(
            btn_label,
            type="primary",
            use_container_width=True,
            disabled=not source_dir,
        )
    with c2:
        partial_clicked = st.button(
            "Generate with partial data",
            use_container_width=True,
            disabled=not source_dir,
            help="Generate immediately even if some tickers still have unresolved missing buys.",
        )

    if gen_clicked or partial_clicked:
        with st.status("Generating final tax report…", expanded=True) as _fin_status:
            st.write("Merging manual purchase records…")
            final = _run_final_pipeline(source_dir, stored_fy)
            _fin_status.update(label="Final report ready!", state="complete", expanded=False)
        _set("final_result", final)
        _set("report_generated", True)

        still_missing = len(final.missing_flags)
        if still_missing:
            st.info(
                f"{still_missing} sell(s) still unmatched — they appear in the "
                "**Missing Buys** sheet of the downloaded report."
            )
        else:
            st.success("All sell transactions fully matched. Final report is ready to download.")

    # ── Final results & download ───────────────────────────────────────────────
    final: TradingPipelineResult | None = _get("final_result")
    if _get("report_generated") and final:
        st.divider()
        st.subheader("Final Tax Report")

        if final.fy_summaries:
            sums = list(final.fy_summaries.values())
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Disposals",      sum(s.disposal_count      for s in sums))
            k2.metric("Gross Gains",    f"${sum(s.gross_gains     for s in sums):,.2f}")
            k3.metric("Gross Losses",   f"${sum(s.gross_losses    for s in sums):,.2f}")
            k4.metric("Net Taxable",    f"${sum(s.net_after_carryforward for s in sums):,.2f}")

        final_tabs = st.tabs(["📊 Summary", "📋 CGT Disposals", "💰 Income", "📂 Open Positions"])
        with final_tabs[0]: _render_summary_tab(final)
        with final_tabs[1]: _render_disposals_tab(final, key_suffix="final")
        with final_tabs[2]: _render_income_tab(final)
        with final_tabs[3]: _render_open_positions_tab(final)

        st.divider()
        _render_export(final, stored_fy)

    # ── Pipeline debug log ─────────────────────────────────────────────────────
    log = _get("log")
    if log:
        _log_lines = log.count("\n") + 1
        with st.expander(f"Pipeline log  ({_log_lines} lines)", expanded=False):
            st.code(log, language="text")