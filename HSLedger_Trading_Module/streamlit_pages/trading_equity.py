"""
trading_equity.py — HSLedger Equity CGT
Standalone Streamlit app.  Run with:
    streamlit run trading/streamlit_pages/trading_equity.py

Reads broker files automatically from trading/inputs/.
Processes all transactions where data is complete (FIFO).
Flags rows with missing data and presents an interactive resolution UI.
Generates the final report once all flagged rows are resolved.
"""

from __future__ import annotations

import contextlib
import glob
import io
import os
import sys
from datetime import date
from itertools import groupby

import pandas as pd
import streamlit as st

# ── Path setup ───────────────────────────────────────────────────────────────
_TRADING_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TRADING_ROOT not in sys.path:
    sys.path.insert(0, _TRADING_ROOT)

try:
    from config import INPUT_DIR_STR, OUTPUT_DIR_STR, DEFAULT_TARGET_FY, LOCAL_COST_BASE_DB_STR
    from main import run_trading_pipeline, TradingPipelineResult
    from equity.equity_engine import (
        MissingBuyFlag, OptionFlag,
        disposals_to_df, income_to_df, missing_to_df, summary_to_df,
    )
    from output.excel_exporter import export_to_excel
    from shared.local_cost_base_db import (
        ensure_local_db, load_local_lots,
        add_lot_for_missing_buy, log_resolution, get_resolution_log, get_lot_count,
    )
    _MODULE_OK = True
except ImportError as _e:
    _MODULE_OK = False
    _IMPORT_ERR = str(_e)


# ── Outcome option sets ───────────────────────────────────────────────────────
_OUTCOMES_OPTION = [
    "— select outcome —",
    "Expired (worthless)",
    "Sold / Closed position",
    "Exercised into shares",
    "Still held (open position)",
]

_OUTCOMES_MISSING_BUY = [
    "— select outcome —",
    "Enter purchase price per unit",
    "Pre-CGT asset (acquired before 20 Sep 1985)",
    "Gifted / Inherited (zero cost base)",
    "Skip (exclude from report)",
]

_PENDING = "Pending selection"


# ── Session state helpers ────────────────────────────────────────────────────

def _init():
    defaults = {
        "result": None,
        "flagged": [],
        "log": "",
        "report_generated": False,
        "final_result": None,
        "last_fy": DEFAULT_TARGET_FY,
        "resolution_progress": [],   # list of resolution dicts for current session
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _outcome_key(item_id: str) -> str:
    return f"outcome__{item_id}"

def _amount_key(item_id: str) -> str:
    return f"amount__{item_id}"

def _buy_date_key(item_id: str) -> str:
    return f"buy_date__{item_id}"

def _buy_brok_key(item_id: str) -> str:
    return f"buy_brok__{item_id}"


def _is_resolved(item_id: str) -> bool:
    return st.session_state.get(_outcome_key(item_id), "— select outcome —") not in (
        "— select outcome —", "", None
    )


# ── Data helpers ─────────────────────────────────────────────────────────────

def _list_input_files() -> list[str]:
    files: list[str] = []
    for ext in ("*.xlsx", "*.xls", "*.csv"):
        files.extend(glob.glob(os.path.join(INPUT_DIR_STR, ext)))
    return sorted(files)


def _run_pipeline(fy: str | None) -> TradingPipelineResult:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = run_trading_pipeline(
            source=INPUT_DIR_STR,
            local_db_path=LOCAL_COST_BASE_DB_STR,
            target_fy=fy,
            interactive_missing=False,
        )
    st.session_state.log = buf.getvalue()
    return result


def _build_flagged(result: TradingPipelineResult) -> list[dict]:
    """Combine missing-buy flags and unresolved option flags into one list."""
    items: list[dict] = []
    ticker_seq: dict[str, int] = {}

    for flag in result.missing_flags:
        n = ticker_seq.get(flag.code, 0) + 1
        ticker_seq[flag.code] = n
        items.append({
            "id":     f"mb_{flag.code}_{n}",
            "ticker": flag.code,
            "row":    flag.row_index or n,
            "type":   "missing_buy",
            "date":   flag.disposal_date,
            "qty":    flag.qty_unmatched,
            "broker": flag.broker,
            "desc":   (
                f"SELL  {flag.qty_unmatched:,.4g} shares  ·  "
                f"{flag.disposal_date.strftime('%d/%m/%Y')}  ·  {flag.broker or '—'}"
            ),
        })

    for flag in result.option_flags:
        n = ticker_seq.get(flag.code, 0) + 1
        ticker_seq[flag.code] = n
        items.append({
            "id":          f"opt_{flag.code}_{n}",
            "ticker":      flag.code,
            "row":         flag.row_index or n,
            "type":        "option",
            "date":        flag.option_date,
            "qty":         flag.qty,
            "broker":      flag.broker,
            "premium":     flag.premium_paid,
            "desc":        (
                f"OPTION  {flag.qty:,.4g} contracts  ·  "
                f"{flag.option_date.strftime('%d/%m/%Y')}  ·  "
                f"premium ${flag.premium_paid:.4f}  ·  {flag.broker or '—'}"
            ),
        })

    return items


def _clear_item_state(items: list[dict]) -> None:
    for item in items:
        for key_fn in (_outcome_key, _amount_key, _buy_date_key, _buy_brok_key):
            k = key_fn(item["id"])
            if k in st.session_state:
                del st.session_state[k]


# ── Tax estimate ─────────────────────────────────────────────────────────────

def _tax_estimate(item: dict) -> tuple[float, float, str]:
    """Returns (capital_gain, capital_loss, display_label)."""
    iid     = item["id"]
    outcome = st.session_state.get(_outcome_key(iid), "— select outcome —")
    amount  = float(st.session_state.get(_amount_key(iid), 0) or 0)
    qty     = float(item["qty"])

    if outcome in ("— select outcome —", "", None):
        return 0.0, 0.0, _PENDING

    if item["type"] == "option":
        pp = float(item.get("premium", 0))
        if outcome == "Expired (worthless)":
            loss = round(pp * qty, 2)
            return 0.0, loss, f"Capital loss  ${loss:,.2f}"
        if outcome == "Sold / Closed position":
            g = round((amount - pp) * qty, 2)
            return (g, 0.0, f"Capital gain  ${g:,.2f}") if g >= 0 else (0.0, abs(g), f"Capital loss  ${abs(g):,.2f}")
        if outcome == "Exercised into shares":
            return 0.0, 0.0, "No immediate CGT — cost base carries forward"
        if outcome == "Still held (open position)":
            return 0.0, 0.0, "Open position — no CGT event"

    if item["type"] == "missing_buy":
        if outcome == "Enter purchase price per unit":
            return 0.0, 0.0, "Will recalculate when you re-run"
        if outcome == "Pre-CGT asset (acquired before 20 Sep 1985)":
            return 0.0, 0.0, "Exempt — pre-CGT asset"
        if outcome == "Gifted / Inherited (zero cost base)":
            return 0.0, 0.0, "Zero cost base — full proceeds are gain"
        if outcome == "Skip (exclude from report)":
            return 0.0, 0.0, "Excluded from report"

    return 0.0, 0.0, "—"


# ── Item card renderer ────────────────────────────────────────────────────────

def _render_card(item: dict) -> None:
    iid      = item["id"]
    resolved = _is_resolved(iid)
    status   = "✅" if resolved else "⬜"
    label    = f"{status}  Row {item['row']}"

    with st.expander(label, expanded=not resolved):
        st.caption(item["desc"])

        opts = _OUTCOMES_OPTION if item["type"] == "option" else _OUTCOMES_MISSING_BUY
        st.selectbox("Outcome type", options=opts, key=_outcome_key(iid))

        outcome = st.session_state.get(_outcome_key(iid), "— select outcome —")

        # Extra inputs depending on outcome
        if item["type"] == "option" and outcome == "Sold / Closed position":
            st.number_input(
                "Net proceeds per contract ($)",
                min_value=0.0, step=0.01, format="%.4f",
                key=_amount_key(iid),
            )
        elif item["type"] == "missing_buy" and outcome == "Enter purchase price per unit":
            col1, col2, col3 = st.columns(3)
            with col1:
                st.number_input(
                    "Purchase price per unit ($)",
                    min_value=0.0, step=0.01, format="%.4f",
                    key=_amount_key(iid),
                )
            with col2:
                st.date_input(
                    "Purchase date",
                    value=date(2023, 7, 1),
                    key=_buy_date_key(iid),
                )
            with col3:
                st.number_input(
                    "Brokerage ($)",
                    min_value=0.0, step=0.01, format="%.2f",
                    key=_buy_brok_key(iid),
                )

        gain, loss, est_label = _tax_estimate(item)
        if outcome == "— select outcome —":
            st.caption(f"Tax estimate:  {est_label}")
        elif gain > 0:
            st.success(f"Tax estimate:  {est_label}")
        elif loss > 0:
            st.error(f"Tax estimate:  {est_label}")
        else:
            st.info(f"Tax estimate:  {est_label}")


# ── Missing buy resolution tab (persistent DB) ───────────────────────────────

def _render_missing_buys_tab(result: TradingPipelineResult, fy: str | None) -> None:
    """
    Show unmatched sells with a form to enter historical purchase details.
    Saves entries permanently to local_cost_base_db.json and re-runs the pipeline.
    """
    missing_flags = result.missing_flags

    if not missing_flags:
        st.success("No missing buy transactions — all sells are fully matched.")
        return

    # ── Progress table header ─────────────────────────────────────────────────
    progress = st.session_state.get("resolution_progress", [])

    st.subheader(f"{len(missing_flags)} unmatched sell(s)")

    miss_df = result.missing_df
    if not miss_df.empty:
        st.dataframe(miss_df, use_container_width=True, hide_index=True)

    if progress:
        st.subheader("Resolution Progress This Session")
        prog_df = pd.DataFrame(progress)
        def _prog_style(row):
            colour = (
                "background-color: #D1FAE5" if row.get("Status") == "Resolved"
                else "background-color: #FEF3C7" if row.get("Status") == "Partially Resolved"
                else "background-color: #FEE2E2"
            )
            return [colour] * len(row)
        try:
            styled = prog_df.style.apply(_prog_style, axis=1)
            st.dataframe(styled, use_container_width=True, hide_index=True)
        except Exception:
            st.dataframe(prog_df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Add Historical Purchase")
    st.caption(
        "Enter the original purchase details for a missing buy. "
        "Details are saved permanently in `data/local_cost_base_db.json` "
        "and will pre-populate the FIFO queue on every future run."
    )

    # ── Select which missing flag to resolve ──────────────────────────────────
    flag_labels = [
        f"{m.code} — sold {m.disposal_date.strftime('%d/%m/%Y')} "
        f"({m.qty_unmatched:,.4g} shares unmatched)"
        for m in missing_flags
    ]
    selected_idx = st.selectbox(
        "Select unmatched sell to resolve",
        options=list(range(len(missing_flags))),
        format_func=lambda i: flag_labels[i],
        key="mb_select",
    )
    flag = missing_flags[selected_idx]

    st.info(
        f"**{flag.code}** · Sold {flag.flag_date if hasattr(flag, 'flag_date') else flag.disposal_date.strftime('%d/%m/%Y')} "
        f"· {flag.qty_unmatched:,.4g} shares unmatched "
        + (f"· Sale price ${flag.proceeds_per_unit:.4f}/share" if flag.proceeds_per_unit else "")
        + (f"· Broker: {flag.broker}" if flag.broker else "")
    )

    # ── Entry form ───────────────────────────────────────────────────────────
    with st.form(key=f"mb_form_{selected_idx}", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            purchase_date = st.date_input(
                "Purchase date *",
                value=date(flag.disposal_date.year - 1, 7, 1),
                max_value=flag.disposal_date,
                key="mb_purchase_date",
            )
            unit_price = st.number_input(
                "Purchase price per unit ($) *",
                min_value=0.0001, step=0.01, format="%.4f",
                key="mb_unit_price",
            )
            qty = st.number_input(
                f"Quantity (need {flag.qty_unmatched:,.4g})",
                min_value=0.0001, step=1.0, format="%.4f",
                value=float(flag.qty_unmatched),
                key="mb_qty",
            )
        with col2:
            brokerage = st.number_input(
                "Brokerage ($)",
                min_value=0.0, step=0.01, format="%.2f",
                key="mb_brokerage",
            )
            gst = st.number_input(
                "GST on brokerage ($)",
                min_value=0.0, step=0.01, format="%.4f",
                key="mb_gst",
            )
            broker_input = st.text_input(
                "Broker / source",
                value=flag.broker or "manual_entry",
                key="mb_broker",
            )
            reference = st.text_input("Reference", key="mb_reference")

        notes = st.text_input(
            "Notes",
            value="Added to resolve missing buy",
            key="mb_notes",
        )
        allow_extra = st.checkbox(
            "Allow quantity > unmatched (surplus becomes open position)",
            key="mb_allow_extra",
        )

        submitted = st.form_submit_button(
            "Add historical buy and recalculate",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        if unit_price <= 0:
            st.error("Purchase price must be positive.")
            return
        if qty <= 0:
            st.error("Quantity must be positive.")
            return
        if purchase_date > flag.disposal_date:
            st.error(f"Purchase date must be on or before sell date ({flag.disposal_date}).")
            return

        user_input = {
            "purchase_date": purchase_date,
            "qty":           float(qty),
            "unit_price":    float(unit_price),
            "brokerage":     float(brokerage),
            "gst":           float(gst),
            "broker":        broker_input.strip() or flag.broker or "manual_entry",
            "reference":     reference.strip(),
            "notes":         notes.strip() or "Added to resolve missing buy",
        }

        try:
            added_lot = add_lot_for_missing_buy(
                LOCAL_COST_BASE_DB_STR, flag, user_input, allow_extra=bool(allow_extra)
            )
            qty_before = flag.qty_unmatched
            qty_added  = float(qty)
            qty_after  = max(0.0, qty_before - qty_added)
            log_resolution(LOCAL_COST_BASE_DB_STR, {
                "code":                 flag.code,
                "sell_date":            flag.disposal_date.isoformat(),
                "qty_unmatched_before": qty_before,
                "qty_added":            qty_added,
                "qty_unmatched_after":  qty_after,
                "lot_ids_added":        [added_lot["lot_id"]],
            })

            if qty_after == 0.0:
                status = "Resolved"
            elif qty_after < qty_before:
                status = "Partially Resolved"
            else:
                status = "Unresolved"

            st.session_state.resolution_progress.append({
                "Code":                 flag.code,
                "Sell Date":            flag.disposal_date.strftime("%d/%m/%Y"),
                "Broker":               flag.broker,
                "Qty Unmatched Before": round(qty_before, 4),
                "Qty Added":            round(qty_added, 4),
                "Qty Unmatched After":  round(qty_after, 4),
                "Status":               status,
                "Lot ID":               added_lot["lot_id"],
                "Purchase Date":        str(purchase_date),
                "Unit Price ($)":       round(unit_price, 4),
                "Cost Per Unit ($)":    round(added_lot["cost_per_unit"], 4),
            })

            st.success(
                f"Saved: {qty:,.4g} × {flag.code} @ ${unit_price:.4f} "
                f"on {purchase_date.strftime('%d/%m/%Y')} — recalculating..."
            )

            # Re-run pipeline with the new lot in the DB
            with st.spinner("Recalculating CGT with new historical lot..."):
                new_result = _run_pipeline(fy)

            # Update session state so the page reflects the new run
            st.session_state.result  = new_result
            st.session_state.flagged = _build_flagged(new_result)
            st.session_state.report_generated = False
            st.session_state.final_result = None
            st.rerun()

        except ValueError as exc:
            st.error(f"Could not save lot: {exc}")

    # ── Show full resolution log from DB ──────────────────────────────────────
    with st.expander("Full resolution history (from database)", expanded=False):
        log = get_resolution_log(LOCAL_COST_BASE_DB_STR)
        if log:
            st.dataframe(pd.DataFrame(log), use_container_width=True, hide_index=True)
        else:
            st.caption("No resolution history yet.")

    # ── Local DB info ────────────────────────────────────────────────────────
    lot_count = get_lot_count(LOCAL_COST_BASE_DB_STR)
    local_df  = load_local_lots(LOCAL_COST_BASE_DB_STR)
    with st.expander(f"Local database contents ({lot_count} lot(s))", expanded=False):
        if not local_df.empty:
            st.dataframe(
                local_df[["trade_date", "code", "name", "qty", "price",
                           "brokerage", "gst", "broker", "source_file", "fingerprint"]],
                use_container_width=True, hide_index=True,
            )
        else:
            st.caption("Database is empty — no historical lots stored yet.")


# ── Results renderer ─────────────────────────────────────────────────────────

def _render_results(result: TradingPipelineResult, fy: str | None = None) -> None:
    tab_sum, tab_disp, tab_inc, tab_open, tab_miss = st.tabs([
        "Summary", "CGT Disposals", "Income", "Open Positions", "⚠ Missing Buys"
    ])

    with tab_sum:
        df = result.summary_df
        if not df.empty:
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No summary data.")

    with tab_disp:
        df = result.disposals_df
        if not df.empty:
            tickers = ["All"] + sorted(df["Asset Code"].dropna().unique().tolist())
            sel = st.selectbox("Filter by asset", tickers, key="disp_filter")
            if sel != "All":
                df = df[df["Asset Code"] == sel]
            st.caption(f"{len(df)} disposal row(s)")
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No disposal events.")

    with tab_inc:
        df = result.income_df
        if not df.empty:
            total = df["Amount ($)"].sum() if "Amount ($)" in df.columns else 0
            st.metric("Total Income", f"${total:,.2f}")
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No income events.")

    with tab_open:
        if result.open_positions:
            rows = []
            for code, queue in result.open_positions.items():
                for lot in queue:
                    held = (date.today() - lot.trade_date).days
                    rows.append({
                        "Asset":           code,
                        "Qty":             round(lot.qty, 4),
                        "Cost/Unit ($)":   round(lot.cost_per_unit, 4),
                        "Total Cost ($)":  round(lot.qty * lot.cost_per_unit, 2),
                        "Acquired":        lot.trade_date,
                        "Days Held":       held,
                        "CGT Discount":    "✅" if held > 365 else "⏳",
                        "Broker":          lot.broker,
                    })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(f"{len(rows)} open lot(s) across {len(result.open_positions)} asset(s)")
        else:
            st.info("No open positions remaining.")

    with tab_miss:
        _render_missing_buys_tab(result, fy)


def _render_export(result: TradingPipelineResult, fy: str | None) -> None:
    resolution_log = get_resolution_log(LOCAL_COST_BASE_DB_STR) if _MODULE_OK else []
    excel_bytes = export_to_excel(
        disposals       = result.disposals,
        income_events   = result.income_events,
        fy_summaries    = result.fy_summaries,
        missing_flags   = result.missing_flags,
        open_positions  = result.open_positions,
        duplicates_df   = result.duplicates_df,
        resolution_log  = resolution_log,
        output_path     = None,
    )
    fy_label = fy or "all-years"
    st.download_button(
        label     = "Download Full Tax Report (.xlsx)",
        data      = excel_bytes,
        file_name = f"HSLedger_Equity_CGT_{fy_label}.xlsx",
        mime      = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type      = "primary",
        use_container_width=True,
    )


# ── Main page ────────────────────────────────────────────────────────────────

def render_trading_equity_page() -> None:
    _init()

    st.title("HSLedger — Equity CGT Calculator")
    st.caption("Australian CGT · ATO rules · FIFO · reads from trading/inputs/")

    if not _MODULE_OK:
        st.error(f"Import error: `{_IMPORT_ERR}`")
        st.info("Ensure all dependencies are installed: `pip install -r requirements.txt`")
        return

    # ── Sidebar ──────────────────────────────────────────────────────────────
    with st.sidebar:
        st.header("Configuration")

        fy = st.selectbox(
            "Financial Year",
            ["2024-25", "2023-24", "2022-23", "All years"],
            index=0,
            key="fy_selector",
        )
        target_fy: str | None = None if fy == "All years" else fy

        st.divider()
        st.subheader("inputs/ folder")
        files = _list_input_files()
        if files:
            for f in files:
                st.caption(f"• {os.path.basename(f)}")
        else:
            st.warning("No files found.  Place broker export files (.xlsx / .csv) in the `inputs/` folder.")

        st.divider()
        load_clicked = st.button(
            "Load & Process",
            type   = "primary",
            disabled = not files,
            use_container_width = True,
        )

        st.divider()
        st.caption("Debug")
        show_log = st.toggle("Show pipeline log", value=False)

    # ── Load & process ────────────────────────────────────────────────────────
    if load_clicked and files:
        with st.spinner("Loading files and running FIFO CGT calculation..."):
            result = _run_pipeline(target_fy)
        st.session_state.result         = result
        st.session_state.flagged        = _build_flagged(result)
        st.session_state.report_generated = False
        st.session_state.final_result   = None
        st.session_state.last_fy        = target_fy
        _clear_item_state(st.session_state.flagged)

    result: TradingPipelineResult | None = st.session_state.result
    flagged: list[dict] = st.session_state.flagged

    if result is None:
        st.info(
            "Place your broker export files in the **`inputs/`** folder, "
            "then click **Load & Process** in the sidebar."
        )
        if show_log and st.session_state.log:
            with st.expander("Pipeline log", expanded=False):
                st.code(st.session_state.log, language="text")
        return

    # ── Metrics bar ───────────────────────────────────────────────────────────
    total    = len(flagged)
    resolved = sum(1 for it in flagged if _is_resolved(it["id"]))

    est_gain = sum(_tax_estimate(it)[0] for it in flagged)
    est_loss = sum(_tax_estimate(it)[1] for it in flagged)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric(
        "Option rows flagged" if any(it["type"] == "option" for it in flagged) else "Rows flagged",
        total,
    )
    m2.metric("Resolved", resolved)
    m3.metric("Est. capital gain", f"${est_gain:,.2f}")
    m4.metric("Est. capital loss", f"${est_loss:,.2f}")

    prog_text = f"{resolved} of {total} resolved"
    st.progress(resolved / total if total > 0 else 1.0, text=prog_text)

    # ── No flagged items → show results directly ──────────────────────────────
    if total == 0:
        st.success("All transactions processed — no missing data detected.")
        st.divider()
        _render_results(result, target_fy)
        st.divider()
        _render_export(result, target_fy)
        if show_log and st.session_state.log:
            with st.expander("Pipeline log", expanded=False):
                st.code(st.session_state.log, language="text")
        return

    st.divider()

    # ── Two-column layout: filter | cards ────────────────────────────────────
    all_tickers = sorted({it["ticker"] for it in flagged})
    left, right = st.columns([1, 3], gap="large")

    with left:
        st.subheader("Filter")

        ticker_choices = ["All tickers"] + all_tickers
        ticker_filter  = st.radio(
            "Tickers",
            ticker_choices,
            label_visibility="collapsed",
            key="ticker_radio",
        )
        unresolved_only = st.checkbox("Unresolved only", value=False, key="unresolved_chk")

    with right:
        # Apply filters
        shown = flagged
        if ticker_filter != "All tickers":
            shown = [it for it in shown if it["ticker"] == ticker_filter]
        if unresolved_only:
            shown = [it for it in shown if not _is_resolved(it["id"])]

        if not shown:
            st.info("No items match the current filter.")
        else:
            shown_sorted = sorted(shown, key=lambda x: x["ticker"])
            for ticker, group_iter in groupby(shown_sorted, key=lambda x: x["ticker"]):
                group = list(group_iter)
                st.subheader(ticker)
                for item in group:
                    _render_card(item)

    # ── Generate report ───────────────────────────────────────────────────────
    st.divider()

    remaining = total - resolved
    btn_label = (
        "Generate Final Report"
        if remaining == 0
        else f"Generate Final Report  ({remaining} item{'s' if remaining != 1 else ''} still unresolved)"
    )
    gen_clicked = st.button(
        btn_label,
        type="primary",
        use_container_width=True,
        key="gen_btn",
    )

    if gen_clicked:
        with st.spinner("Applying resolutions and generating report..."):
            # Build a manual cost_base_history from "Enter purchase price" resolutions
            import tempfile, shutil
            manual_rows = []
            for it in flagged:
                iid     = it["id"]
                outcome = st.session_state.get(_outcome_key(iid), "")
                if it["type"] == "missing_buy" and outcome == "Enter purchase price per unit":
                    price = float(st.session_state.get(_amount_key(iid), 0) or 0)
                    bd    = st.session_state.get(_buy_date_key(iid), date(2020, 1, 1))
                    brok  = float(st.session_state.get(_buy_brok_key(iid), 0) or 0)
                    if price > 0:
                        manual_rows.append({
                            "stock_code":    it["ticker"],
                            "purchase_date": bd.strftime("%d/%m/%Y") if hasattr(bd, "strftime") else str(bd),
                            "quantity":      it["qty"],
                            "unit_price":    price,
                            "brokerage":     brok,
                            "gst":           0.0,
                            "source":        "manual_entry",
                        })

            tmp_dir  = tempfile.mkdtemp()
            hist_csv = None
            if manual_rows:
                hist_csv = os.path.join(tmp_dir, "_manual_entries.csv")
                pd.DataFrame(manual_rows).to_csv(hist_csv, index=False)

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                final = run_trading_pipeline(
                    source                 = INPUT_DIR_STR,
                    cost_base_history_path = hist_csv,
                    local_db_path          = LOCAL_COST_BASE_DB_STR,
                    target_fy              = st.session_state.last_fy,
                    interactive_missing    = False,
                )
            shutil.rmtree(tmp_dir, ignore_errors=True)

        st.session_state.final_result     = final
        st.session_state.report_generated = True
        st.success("Report generated.")

    if st.session_state.report_generated and st.session_state.final_result:
        final = st.session_state.final_result
        st.divider()
        st.subheader("Results")
        _render_results(final, st.session_state.last_fy)
        st.divider()
        _render_export(final, st.session_state.last_fy)

    if show_log and st.session_state.log:
        with st.expander("Pipeline log", expanded=False):
            st.code(st.session_state.log, language="text")


# ── Standalone entry point ────────────────────────────────────────────────────
if __name__ == "__main__":
    st.set_page_config(
        page_title = "HSLedger — Equity CGT",
        page_icon  = "📊",
        layout     = "wide",
    )
    render_trading_equity_page()