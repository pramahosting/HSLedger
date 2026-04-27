"""
missing_buys_resolver.py — HSLedger
Step-by-step wizard for resolving missing buy records.

Run standalone:
    streamlit run trading/streamlit_pages/missing_buys_resolver.py
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

# ── Path setup ───────────────────────────────────────────────────────────────
_TRADING_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TRADING_ROOT not in sys.path:
    sys.path.insert(0, _TRADING_ROOT)

try:
    from config import INPUT_DIR_STR, DEFAULT_TARGET_FY
    from main import run_trading_pipeline, TradingPipelineResult
    from equity.equity_engine import MissingBuyFlag
    from output.excel_exporter import export_to_excel
    _MODULE_OK = True
except ImportError as _e:
    _MODULE_OK = False
    _IMPORT_ERR = str(_e)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _empty_buy_df() -> pd.DataFrame:
    return pd.DataFrame({
        "Purchase Date (dd/mm/yyyy)": pd.Series([], dtype=str),
        "Quantity": pd.Series([], dtype=float),
        "Unit Price ($)": pd.Series([], dtype=float),
        "Brokerage ($)": pd.Series([], dtype=float),
        "GST ($)": pd.Series([], dtype=float),
    })


def _group_by_ticker(flags: list[MissingBuyFlag]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for f in flags:
        grouped.setdefault(f.code, []).append({
            "date":              f.disposal_date,
            "qty":               f.qty_unmatched,
            "broker":            f.broker,
            "reference":         f.reference,
            "proceeds_per_unit": f.proceeds_per_unit,
        })
    return grouped


def _parse_buy_df(df: pd.DataFrame, ticker: str) -> list[dict]:
    """Convert data-editor DataFrame into validated buy lot list."""
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

        lots.append({
            "ticker":        ticker,
            "date":          buy_date,
            "qty":           qty,
            "qty_remaining": qty,
            "price":         price,
            "brokerage":     brok,
            "gst":           gst,
            "cpu":           price + (brok + gst) / qty,
        })
    return lots


def _fifo_preview(sells: list[dict], buy_lots: list[dict]) -> pd.DataFrame:
    """Run FIFO match against entered lots and return per-lot results table."""
    if not buy_lots:
        return pd.DataFrame()

    queue = deque(sorted(buy_lots, key=lambda x: x["date"]))
    rows: list[dict] = []

    for sell in sorted(sells, key=lambda x: x["date"]):
        remaining  = sell["qty"]
        sale_price = sell.get("proceeds_per_unit", 0.0)

        while remaining > 1e-9 and queue:
            lot  = queue[0]
            take = min(lot["qty_remaining"], remaining)
            held = (sell["date"] - lot["date"]).days
            disc = held > 365
            gross = round((sale_price - lot["cpu"]) * take, 2)
            after = round(gross * 0.5 if (disc and gross > 0) else gross, 2)

            rows.append({
                "Sell Date":           sell["date"].strftime("%d/%m/%Y"),
                "Qty":                 int(take),
                "Buy Date":            lot["date"].strftime("%d/%m/%Y"),
                "Days Held":           held,
                "CGT Discount":        "Yes  (50%)" if disc else "No",
                "Sale Price/Unit ($)": round(sale_price, 4),
                "Cost/Unit ($)":       round(lot["cpu"], 4),
                "Gross Gain/Loss ($)": gross,
                "After Discount ($)":  after,
            })

            lot["qty_remaining"] -= take
            remaining -= take
            if lot["qty_remaining"] < 1e-9:
                queue.popleft()

        if remaining > 1e-9:
            rows.append({
                "Sell Date":           sell["date"].strftime("%d/%m/%Y"),
                "Qty":                 f"{int(remaining)} UNMATCHED",
                "Buy Date":            "—",
                "Days Held":           "—",
                "CGT Discount":        "—",
                "Sale Price/Unit ($)": round(sale_price, 4) if sale_price else "—",
                "Cost/Unit ($)":       "—",
                "Gross Gain/Loss ($)": "—",
                "After Discount ($)":  "—",
            })

    return pd.DataFrame(rows)


def _ticker_status(ticker: str, sells: list[dict]) -> tuple[str, str]:
    """Returns (icon, status_text) based on buy coverage."""
    df   = st.session_state.buy_records.get(ticker, _empty_buy_df())
    lots = _parse_buy_df(df, ticker)
    total_sell = sum(s["qty"] for s in sells)
    total_buy  = sum(l["qty"] for l in lots)

    if total_buy == 0:
        return "⬜", "Not started"
    if total_buy >= total_sell - 1e-6:
        return "✅", f"Covered  {total_buy:,.0f} / {total_sell:,.0f} shares"
    return "⚠️", f"Partial  {total_buy:,.0f} / {total_sell:,.0f} shares"


def _init_state():
    for k, v in {
        "result":           None,
        "buy_records":      {},
        "final_result":     None,
        "selected_ticker":  None,
        "log":              "",
        "report_done":      False,
    }.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _run_pipeline(fy: str) -> TradingPipelineResult:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = run_trading_pipeline(
            source=INPUT_DIR_STR,
            target_fy=fy,
            interactive_missing=False,
        )
    st.session_state.log = buf.getvalue()
    return result


# ── Ticker entry panel ────────────────────────────────────────────────────────

def _render_ticker_panel(ticker: str, sells: list[dict]) -> None:
    st.subheader(ticker)
    total_sell_qty = sum(s["qty"] for s in sells)
    st.caption(
        f"{len(sells)} sell event(s)  ·  {total_sell_qty:,.0f} total shares unmatched"
    )

    # ── Sells table ──────────────────────────────────────────────────────────
    st.markdown("**Sell events with no matching buy:**")
    sells_df = pd.DataFrame([{
        "Sell Date":          s["date"].strftime("%d/%m/%Y"),
        "Qty":                f"{int(s['qty']):,}",
        "Broker":             s["broker"].title() if s["broker"] else "—",
        "Reference":          s["reference"] or "—",
        "Sale Price/Unit ($)": f"${s['proceeds_per_unit']:.4f}" if s.get("proceeds_per_unit") else "—",
    } for s in sorted(sells, key=lambda x: x["date"])])
    st.dataframe(sells_df, use_container_width=True, hide_index=True)

    # ── Buy entry table ──────────────────────────────────────────────────────
    st.markdown(f"**Enter purchase history for {ticker}:**")
    st.caption(
        "Add one row per purchase lot.  "
        "FIFO matches the oldest buy to the earliest sell.  "
        "Holdings over 365 days automatically receive the 50% CGT discount."
    )

    existing = st.session_state.buy_records.get(ticker)
    if existing is None or (hasattr(existing, "empty") and existing.empty):
        existing = _empty_buy_df()

    edited = st.data_editor(
        existing,
        key=f"editor_{ticker}",
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "Purchase Date (dd/mm/yyyy)": st.column_config.TextColumn(
                "Purchase Date",
                help="Format: dd/mm/yyyy   e.g.  01/07/2022",
                width="medium",
            ),
            "Quantity": st.column_config.NumberColumn(
                "Qty (shares)",
                min_value=1,
                step=1,
                format="%d",
            ),
            "Unit Price ($)": st.column_config.NumberColumn(
                "Unit Price ($)",
                min_value=0.0001,
                step=0.01,
                format="%.4f",
            ),
            "Brokerage ($)": st.column_config.NumberColumn(
                "Brokerage ($)",
                min_value=0.0,
                step=0.01,
                format="%.2f",
            ),
            "GST ($)": st.column_config.NumberColumn(
                "GST ($)",
                min_value=0.0,
                step=0.01,
                format="%.2f",
            ),
        },
    )
    st.session_state.buy_records[ticker] = edited

    # ── FIFO preview ─────────────────────────────────────────────────────────
    lots = _parse_buy_df(edited, ticker)
    if not lots:
        st.info("Add at least one valid purchase row above to see the tax estimate.")
        return

    preview = _fifo_preview(sells, lots)
    if preview.empty:
        return

    st.markdown("**FIFO Match and CGT Preview:**")

    # Split matched vs unmatched for colour coding
    matched   = preview[~preview["Gross Gain/Loss ($)"].astype(str).str.contains("UNMATCHED|—", na=False)].copy()
    unmatched = preview[preview["Gross Gain/Loss ($)"].astype(str).str.contains("—", na=False)].copy()

    if not matched.empty:
        # Colour gain/loss columns
        def _colour_val(v):
            try:
                n = float(v)
                if n > 0:
                    return "color: #16a34a; font-weight:600"
                if n < 0:
                    return "color: #dc2626; font-weight:600"
            except (TypeError, ValueError):
                pass
            return ""

        try:
            styled = matched.style.map(
                _colour_val,
                subset=["Gross Gain/Loss ($)", "After Discount ($)"],
            )
            st.dataframe(styled, use_container_width=True, hide_index=True)
        except Exception:
            st.dataframe(matched, use_container_width=True, hide_index=True)

    if not unmatched.empty:
        st.warning(
            f"{len(unmatched)} sell event(s) still unmatched — "
            "add more purchase records above to cover them."
        )

    # ── Summary metrics ──────────────────────────────────────────────────────
    numeric_gain = [
        float(r) for r in matched["After Discount ($)"]
        if isinstance(r, (int, float)) and float(r) > 0
    ]
    numeric_loss = [
        abs(float(r)) for r in matched["After Discount ($)"]
        if isinstance(r, (int, float)) and float(r) < 0
    ]
    total_gain = round(sum(numeric_gain), 2)
    total_loss = round(sum(numeric_loss), 2)

    sc1, sc2, sc3 = st.columns(3)
    icon, status = _ticker_status(ticker, sells)
    sc1.info(f"Coverage:  {icon}  {status}")
    if total_gain:
        sc2.success(f"Est. capital gain:  ${total_gain:,.2f}")
    if total_loss:
        sc3.error(f"Est. capital loss:  ${total_loss:,.2f}")

    disc_count = len(matched[matched["CGT Discount"] == "Yes  (50%)"])
    if disc_count:
        st.caption(
            f"50% CGT discount applies to {disc_count} lot(s) held over 12 months."
        )


# ── Results / export ──────────────────────────────────────────────────────────

def _render_results(result: TradingPipelineResult) -> None:
    tabs = st.tabs(["Summary", "CGT Disposals", "Income", "Open Positions"])

    with tabs[0]:
        df = result.summary_df
        if not df.empty:
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No summary data.")

    with tabs[1]:
        df = result.disposals_df
        if not df.empty:
            choices = ["All"] + sorted(df["Asset Code"].dropna().unique().tolist())
            sel = st.selectbox("Filter by asset", choices, key="final_disp_filter")
            if sel != "All":
                df = df[df["Asset Code"] == sel]
            st.caption(f"{len(df)} disposal row(s)")
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No disposal events.")

    with tabs[2]:
        df = result.income_df
        if not df.empty:
            total = df["Amount ($)"].sum() if "Amount ($)" in df.columns else 0
            st.metric("Total Income", f"${total:,.2f}")
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No income events.")

    with tabs[3]:
        if result.open_positions:
            rows = []
            for code, queue in result.open_positions.items():
                for lot in queue:
                    held = (date.today() - lot.trade_date).days
                    rows.append({
                        "Asset":          code,
                        "Qty":            round(lot.qty, 4),
                        "Cost/Unit ($)":  round(lot.cost_per_unit, 4),
                        "Total Cost ($)": round(lot.qty * lot.cost_per_unit, 2),
                        "Acquired":       lot.trade_date,
                        "Days Held":      held,
                        "CGT Discount":   "Yes" if held > 365 else "No",
                        "Broker":         lot.broker,
                    })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(f"{len(rows)} open lot(s) across {len(result.open_positions)} asset(s)")
        else:
            st.info("No open positions.")


def _render_export(result: TradingPipelineResult, fy: str) -> None:
    excel_bytes = export_to_excel(
        disposals      = result.disposals,
        income_events  = result.income_events,
        fy_summaries   = result.fy_summaries,
        missing_flags  = result.missing_flags,
        open_positions = result.open_positions,
        duplicates_df  = result.duplicates_df,
        output_path    = None,
    )
    st.download_button(
        label     = "Download Full Tax Report (.xlsx)",
        data      = excel_bytes,
        file_name = f"HSLedger_Equity_CGT_{fy}.xlsx",
        mime      = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type      = "primary",
        use_container_width=True,
    )


def _generate_and_save(by_ticker: dict[str, list[dict]], fy: str) -> None:
    """Compile all buy records into a temp CSV and re-run the full pipeline."""
    all_rows = []
    for ticker in by_ticker:
        df = st.session_state.buy_records.get(ticker, _empty_buy_df())
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

    tmp_dir  = tempfile.mkdtemp()
    hist_csv = None
    if all_rows:
        hist_csv = os.path.join(tmp_dir, "_manual_entries.csv")
        pd.DataFrame(all_rows).to_csv(hist_csv, index=False)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        final = run_trading_pipeline(
            source                 = INPUT_DIR_STR,
            cost_base_history_path = hist_csv,
            target_fy              = fy,
            interactive_missing    = False,
        )
    shutil.rmtree(tmp_dir, ignore_errors=True)

    st.session_state.final_result = final
    st.session_state.log         += "\n\n--- FINAL RUN ---\n" + buf.getvalue()
    st.session_state.report_done  = True


# ── Main page ─────────────────────────────────────────────────────────────────

def render_missing_buys_page() -> None:
    _init_state()

    st.title("Missing Buy Resolution")
    st.caption(
        "Enter historical purchase details · "
        "FIFO applied automatically · "
        "50% CGT discount calculated per lot"
    )

    if not _MODULE_OK:
        st.error(f"Import error: `{_IMPORT_ERR}`")
        return

    # ── Sidebar ──────────────────────────────────────────────────────────────
    with st.sidebar:
        st.header("Configuration")
        fy = st.selectbox(
            "Financial Year",
            ["2024-25", "2023-24", "2022-23"],
            index=0,
        )

        if st.button("Scan inputs/ for missing buys", type="primary", use_container_width=True):
            with st.spinner("Loading files and running FIFO..."):
                result = _run_pipeline(fy)
            st.session_state.result       = result
            st.session_state.final_result = None
            st.session_state.report_done  = False
            # Prime buy_records dict (don't overwrite existing entries)
            for code in _group_by_ticker(result.missing_flags):
                if code not in st.session_state.buy_records:
                    st.session_state.buy_records[code] = _empty_buy_df()

        st.divider()
        show_log = st.toggle("Show pipeline log", value=False)

    result: TradingPipelineResult | None = st.session_state.result

    if result is None:
        st.info(
            "Click **Scan inputs/ for missing buys** in the sidebar.  \n"
            "The system will load all files from `inputs/`, run the FIFO calculation, "
            "and list every sell that has no matching historical buy."
        )
        return

    by_ticker = _group_by_ticker(result.missing_flags)
    tickers   = sorted(by_ticker.keys())

    if not tickers:
        st.success("No missing buys detected — all sells matched to historical buys.")
        _render_results(result)
        _render_export(result, fy)
        return

    # ── Summary metrics ───────────────────────────────────────────────────────
    full     = [t for t in tickers if _ticker_status(t, by_ticker[t])[0] == "✅"]
    partial  = [t for t in tickers if _ticker_status(t, by_ticker[t])[0] == "⚠️"]
    pending  = [t for t in tickers if _ticker_status(t, by_ticker[t])[0] == "⬜"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Tickers flagged",   len(tickers))
    m2.metric("Fully resolved",    len(full))
    m3.metric("Partial",           len(partial))
    m4.metric("Not started",       len(pending))

    st.progress(
        len(full) / len(tickers),
        text=f"{len(full)} of {len(tickers)} tickers fully resolved",
    )

    st.divider()

    # ── Two-column layout ────────────────────────────────────────────────────
    left, right = st.columns([1, 3], gap="large")

    with left:
        st.subheader("Tickers")
        st.caption("Click a ticker to enter its purchase history")

        for t in tickers:
            icon, status = _ticker_status(t, by_ticker[t])
            n = len(by_ticker[t])
            label = f"{icon}  {t}   ({n} sell{'s' if n > 1 else ''})"
            if st.button(label, key=f"nav_{t}", use_container_width=True):
                st.session_state.selected_ticker = t

    with right:
        # Default to first unresolved ticker on first render
        if st.session_state.selected_ticker is None:
            first_unresolved = next(
                (t for t in tickers if _ticker_status(t, by_ticker[t])[0] != "✅"),
                tickers[0],
            )
            st.session_state.selected_ticker = first_unresolved

        sel = st.session_state.selected_ticker
        if sel and sel in by_ticker:
            _render_ticker_panel(sel, by_ticker[sel])

    # ── Generate report buttons ───────────────────────────────────────────────
    st.divider()

    all_covered = len(full) == len(tickers)

    c1, c2 = st.columns([2, 1])
    with c1:
        if all_covered:
            if st.button(
                "Generate Final Tax Report",
                type="primary",
                use_container_width=True,
            ):
                with st.spinner("Applying all purchase records and recalculating CGT..."):
                    _generate_and_save(by_ticker, fy)
                st.success("Report generated with all purchase records applied.")
        else:
            remaining = len(tickers) - len(full)
            st.button(
                f"Generate Final Tax Report  —  {remaining} ticker(s) still need buy records",
                disabled=True,
                use_container_width=True,
            )

    with c2:
        if st.button(
            "Generate with partial data",
            use_container_width=True,
            help="Generate the report now, even if some tickers still have missing buys.",
        ):
            with st.spinner("Generating partial report..."):
                _generate_and_save(by_ticker, fy)
            st.info("Report generated. Check the Missing Buys sheet for remaining gaps.")

    # ── Final results ─────────────────────────────────────────────────────────
    if st.session_state.report_done and st.session_state.final_result:
        final = st.session_state.final_result
        st.divider()
        st.subheader("Final Tax Results")

        still_missing = len(final.missing_flags)
        if still_missing:
            st.warning(
                f"{still_missing} sell(s) still have no matching buy. "
                "These are shown in the Missing Buys sheet of the Excel report."
            )
        else:
            st.success("All sell transactions fully matched to historical buys.")

        # Key figures
        if final.fy_summaries:
            fy_sum = list(final.fy_summaries.values())[0]
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Disposals",          fy_sum.disposal_count)
            k2.metric("Gross Gains",        f"${fy_sum.gross_gains:,.2f}")
            k3.metric("Gross Losses",       f"${fy_sum.gross_losses:,.2f}")
            k4.metric("Net Taxable Gain",   f"${fy_sum.net_after_carryforward:,.2f}")

        _render_results(final)
        st.divider()
        _render_export(final, fy)

    if show_log and st.session_state.log:
        with st.expander("Pipeline log", expanded=False):
            st.code(st.session_state.log, language="text")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    st.set_page_config(
        page_title = "HSLedger — Missing Buy Resolver",
        page_icon  = "📋",
        layout     = "wide",
    )
    render_missing_buys_page()