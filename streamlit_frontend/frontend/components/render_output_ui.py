# frontend/components/render_output_ui.py

import json
import os
from urllib import error, request

import streamlit as st
import pandas as pd
from dotenv import find_dotenv, load_dotenv
from backend.reconciliation import exporter
from backend.reconciliation.session_manager import session_manager
from backend.reconciliation.gst_calculator import GST_CATEGORY_OPTIONS, calculate_gst_value
from backend.ai_model import classify_category
from backend.transaction_classifier import transaction_classify

# Load environment variables from the nearest .env in current/parent directories.
load_dotenv(find_dotenv(), override=False)

API_BASE_URL = os.getenv("HSLEDGER_API_BASE_URL", "http://127.0.0.1:8000").strip() or "http://127.0.0.1:8000"


def _value_or_none(value):
    if pd.isna(value):
        return None
    return value


def _get_logged_in_user_id():
    user = st.session_state.get("user") or {}
    raw_id = user.get("id", user.get("user_id"))
    try:
        return int(raw_id) if raw_id is not None else None
    except (TypeError, ValueError):
        return None


def _build_transactions_payload_from_display(df: pd.DataFrame) -> list[dict]:
    """Build the API payload from the output datatable dataframe."""
    rows: list[dict] = []
    for _, row in df.iterrows():
        rows.append(
            {
                "date": str(_value_or_none(row.get("Date"))) if _value_or_none(row.get("Date")) is not None else None,
                "bank": _value_or_none(row.get("Bank")),
                "account": _value_or_none(row.get("Account")),
                "description": _value_or_none(row.get("Description")),
                "debit": float(row.get("Debit", 0) or 0),
                "credit": float(row.get("Credit", 0) or 0),
                "classification": _value_or_none(row.get("Classification")),
                "pair_id": _value_or_none(row.get("PairID")),
                "gl_account": _value_or_none(row.get("GL Account")),
                "gst": float(row.get("GST", 0) or 0),
                "gst_category": _value_or_none(row.get("GST Category")),
                "who": _value_or_none(row.get("Who")),
            }
        )
    return rows


def _save_transactions_to_db(user_id: int, transactions: list[dict]) -> dict:
    """Send transactions to the FastAPI /transactions/save endpoint."""
    payload = {
        "user_id": user_id,
        "transactions": transactions,
    }
    body = json.dumps(payload).encode("utf-8")

    req = request.Request(
        url=f"{API_BASE_URL.rstrip('/')}/transactions/save",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API error {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Connection error: {exc}") from exc


def _update_transaction_in_db(user_id: int, transaction_id: int, transaction: dict) -> dict:
    """Update a single persisted transaction by ID."""
    payload = {
        "user_id": user_id,
        "transaction": transaction,
    }
    body = json.dumps(payload).encode("utf-8")

    req = request.Request(
        url=f"{API_BASE_URL.rstrip('/')}/transactions/{transaction_id}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="PUT",
    )

    try:
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API error {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Connection error: {exc}") from exc


def _create_transaction_in_db(user_id: int, transaction: dict) -> dict:
    """Create a single transaction row in DB and return the created payload."""
    payload = {
        "user_id": user_id,
        "transaction": transaction,
    }
    body = json.dumps(payload).encode("utf-8")

    req = request.Request(
        url=f"{API_BASE_URL.rstrip('/')}/transactions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API error {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Connection error: {exc}") from exc


def _delete_transaction_in_db(user_id: int, transaction_id: int) -> dict:
    """Delete a single transaction row in DB by ID."""
    req = request.Request(
        url=f"{API_BASE_URL.rstrip('/')}/transactions/{transaction_id}?user_id={user_id}",
        method="DELETE",
    )

    try:
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API error {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Connection error: {exc}") from exc


def _load_transactions_from_db(user_id: int) -> list[dict]:
    """Fetch all persisted transactions for a user from FastAPI."""
    req = request.Request(
        url=f"{API_BASE_URL.rstrip('/')}/transactions/user/{user_id}",
        method="GET",
    )

    try:
        with request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data if isinstance(data, list) else []
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API error {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Connection error: {exc}") from exc


def _db_transactions_to_display_df(rows: list[dict]) -> pd.DataFrame:
    """Convert DB transaction rows to the output grid dataframe format."""
    columns = [
        "DB ID",
        "Date", "Bank", "Account", "Description", "Debit", "Credit",
        "Classification", "PairID", "GL Account", "GST", "GST Category", "Who"
    ]
    if not rows:
        return pd.DataFrame(columns=columns)

    records = []
    for row in rows:
        raw_date = row.get("date")
        parsed_date = pd.to_datetime(raw_date, errors="coerce") if raw_date is not None else pd.NaT
        date_text = parsed_date.strftime("%d/%m/%Y") if pd.notnull(parsed_date) else (str(raw_date) if raw_date else "")

        records.append(
            {
                "DB ID": row.get("id"),
                "Date": date_text,
                "Bank": row.get("bank", ""),
                "Account": row.get("account", ""),
                "Description": row.get("description", ""),
                "Debit": float(row.get("debit", 0) or 0),
                "Credit": float(row.get("credit", 0) or 0),
                "Classification": row.get("classification", ""),
                "PairID": row.get("pair_id", ""),
                "GL Account": row.get("gl_account", ""),
                "GST": float(row.get("gst", 0) or 0),
                "GST Category": row.get("gst_category", ""),
                "Who": row.get("who", ""),
            }
        )

    return pd.DataFrame(records, columns=columns)

# GL Account options for dropdown (keep in sync with classifier enums)
GL_ACCOUNT_OPTIONS = list(dict.fromkeys(classify_category.CATEGORY_ENUM + [""]))

# Only canonical ATO categories in the dropdown. AI labels are normalised via gst_aliases below.
GST_CLASSIFY_OPTIONS = list(GST_CATEGORY_OPTIONS)


def normalize_gl_account(value):
    text = "" if pd.isna(value) else str(value).strip()
    if not text:
        return ""

    for option in classify_category.CATEGORY_ENUM:
        if option.lower() == text.lower():
            return option

    return ""


def normalize_gst_category(value):
    text = "" if pd.isna(value) else str(value).strip()
    if not text:
        return "Unknown"

    for option in GST_CLASSIFY_OPTIONS:
        if str(option).lower() == text.lower():
            return option

    gst_aliases = {
        "gst on income": "GST on Sale",
        "gst on expenses": "GST on Purchase",
        "gst on capital": "GST on Purchase",
        "gst free income": "GST Free Sale",
        "gst free expenses": "GST Free Sale",
    }
    return gst_aliases.get(text.lower(), "Unknown")


def calculate_classified_gst_value(debit, credit, gst_category):
    normalized_category = normalize_gst_category(gst_category)
    return calculate_gst_value(debit, credit, normalized_category)


def classify_gl_and_gst_for_session(username):
    target_df = (
        st.session_state.edited_df_cache.copy()
        if st.session_state.edited_df_cache is not None
        else st.session_state.reconciliation_results.copy()
    )

    if target_df is None or target_df.empty or "Description" not in target_df.columns:
        st.error("No transaction descriptions available to classify in the current session.")
        return

    if "GL Account" not in target_df.columns:
        target_df["GL Account"] = ""
    if "GST Category" not in target_df.columns:
        target_df["GST Category"] = "Unknown"
    if "GST" not in target_df.columns:
        target_df["GST"] = 0.0
    if "Who" not in target_df.columns:
        target_df["Who"] = "Other/Unknown"

    def _to_float(value):
        parsed = pd.to_numeric(value, errors="coerce")
        return float(parsed) if pd.notnull(parsed) else 0.0

    prediction_keys = []
    seen_keys = set()
    for idx in target_df.index:
        desc = str(target_df.at[idx, "Description"]) if pd.notnull(target_df.at[idx, "Description"]) else ""
        if not desc.strip():
            continue
        debit_val = target_df.at[idx, "Debit"] if "Debit" in target_df.columns else 0
        credit_val = target_df.at[idx, "Credit"] if "Credit" in target_df.columns else 0
        debit = _to_float(debit_val)
        credit = _to_float(credit_val)
        key = (desc, debit, credit)
        if key not in seen_keys:
            seen_keys.add(key)
            prediction_keys.append(key)

    if not prediction_keys:
        st.error("No non-empty descriptions available to classify.")
        return

    progress = st.progress(0.0, text="Classifying GL Account and GST Category...")
    prediction_by_key = {}

    try:
        for index, (desc, debit, credit) in enumerate(prediction_keys, start=1):
            prediction_by_key[(desc, debit, credit)] = transaction_classify.classify_transaction(
                desc,
                debit=debit,
                credit=credit,
            )
            progress.progress(
                index / max(1, len(prediction_keys)),
                text=f"Classifying GL Account and GST Category... {index}/{len(prediction_keys)}",
            )

        updated_rows = 0
        for idx in target_df.index:
            desc = str(target_df.at[idx, "Description"]) if pd.notnull(target_df.at[idx, "Description"]) else ""
            if not desc.strip():
                continue

            # Internal transfers must always be BAS Excluded with zero GST — never run through ML
            classification = str(target_df.at[idx, "Classification"]) if "Classification" in target_df.columns and pd.notnull(target_df.at[idx, "Classification"]) else ""
            if "Internal" in classification:
                existing_gl = normalize_gl_account(target_df.at[idx, "GL Account"])
                did_update = False
                if existing_gl == "":
                    target_df.at[idx, "GL Account"] = "Transfer"
                    did_update = True
                if normalize_gst_category(target_df.at[idx, "GST Category"]) != "BAS Excluded":
                    target_df.at[idx, "GST Category"] = "BAS Excluded"
                    target_df.at[idx, "GST"] = 0.0
                    st.session_state.pending_changes.pop(idx, None)
                    did_update = True
                target_df.at[idx, "Who"] = classify_category.extract_who_bank(desc)
                if did_update:
                    updated_rows += 1
                continue

            debit = target_df.at[idx, "Debit"] if "Debit" in target_df.columns else 0
            credit = target_df.at[idx, "Credit"] if "Credit" in target_df.columns else 0
            debit_num = _to_float(debit)
            credit_num = _to_float(credit)
            key = (desc, debit_num, credit_num)
            prediction = prediction_by_key.get(key, {})
            normalized_gl = normalize_gl_account(prediction.get("gl_account", ""))
            normalized_gst = normalize_gst_category(prediction.get("gst_category", "Unknown"))

            existing_gl = normalize_gl_account(target_df.at[idx, "GL Account"])
            existing_gst_category = normalize_gst_category(target_df.at[idx, "GST Category"])
            has_pending_manual_gst = idx in st.session_state.pending_changes

            # Preserve manually set GST values/categories. Only fill GST when unknown and no pending manual change.
            should_update_gl = existing_gl == ""
            should_update_gst = (existing_gst_category == "Unknown") and (not has_pending_manual_gst)

            if should_update_gl:
                target_df.at[idx, "GL Account"] = normalized_gl

            if should_update_gst:
                target_df.at[idx, "GST Category"] = normalized_gst
                target_df.at[idx, "GST"] = float(calculate_classified_gst_value(debit_num, credit_num, normalized_gst))
                st.session_state.pending_changes.pop(idx, None)

            target_df.at[idx, "Who"] = classify_category.extract_who_bank(desc)
            if should_update_gl or should_update_gst:
                updated_rows += 1

        st.session_state.edited_df_cache = target_df.copy()
        st.session_state.reconciliation_results = target_df.copy()
        st.session_state.updated_pages.add(st.session_state.page_number)

        if st.session_state.get("current_session_id"):
            session_manager.save_output_data(
                username,
                st.session_state.current_session_id,
                st.session_state.reconciliation_results,
                st.session_state.pending_changes,
                st.session_state.updated_pages,
                st.session_state.page_number,
            )

        st.success(f"Classified GL Account and GST Category for {updated_rows} row(s).")
        st.rerun()
    except Exception as exc:
        st.error(f"Failed to classify GL Account and GST Category: {exc}")
    finally:
        progress.empty()

def get_excel_bytes(df_total, monthly_summary):
    return exporter.export_excel_bytes(df_total, monthly_summary)

def render_output_ui(username, save_current_session):
    """Render the output UI including results, monthly summary, transaction details, and export."""

    # --- Display Results ---
    if st.session_state.reconciliation_results is not None:
        # Header with Download button
        header_col1, header_col2 = st.columns([3, 1])
        with header_col1:
            st.subheader("🔎Reconciliation Results")
        with header_col2:
            # Placeholder for download button (will be populated after data is processed)
            download_placeholder = st.empty()

        # Auto-classify when a fresh session has just been processed
        if st.session_state.get("needs_classification", False):
            st.session_state.needs_classification = False
            classify_gl_and_gst_for_session(username)
        
        # Use cached edited dataframe
        if st.session_state.edited_df_cache is not None:
            df_total = st.session_state.edited_df_cache.copy()
        else:
            df_total = st.session_state.reconciliation_results.copy()

        # Backward compatibility: older sessions may store lower-case GST columns.
        legacy_col_map = {
            "gst": "GST",
            "gst_category": "GST Category",
            "gl_account": "GL Account",
            "pair_id": "PairID",
        }
        df_total = df_total.rename(columns={k: v for k, v in legacy_col_map.items() if k in df_total.columns})

        if "GST Category" not in df_total.columns:
            df_total["GST Category"] = "Unknown"

        if "GST" not in df_total.columns:
            if "Debit" in df_total.columns and "Credit" in df_total.columns:
                df_total["GST"] = df_total.apply(
                    lambda row: calculate_gst_value(
                        row.get("Debit", 0),
                        row.get("Credit", 0),
                        row.get("GST Category", "Unknown"),
                    ),
                    axis=1,
                )
            else:
                df_total["GST"] = 0.0

        if "Who" not in df_total.columns:
            df_total["Who"] = ""

        # Persist normalized schema back to session state so downstream row edits
        # can safely access canonical column names.
        st.session_state.reconciliation_results = df_total.copy()
        if st.session_state.edited_df_cache is not None:
            st.session_state.edited_df_cache = df_total.copy()

        if not st.session_state.show_gst and "GST" in df_total.columns:
            df_total = df_total.drop(columns=["GST"])

        # --- Monthly Summary ---
        monthly_summary = None
        summary_df = None
        if "Date" in df_total.columns and not df_total["Date"].isna().all():
            df_total["Date_dt"] = pd.to_datetime(df_total["Date"], errors="coerce", dayfirst=True)
            df_total["Month"] = df_total["Date_dt"].dt.month
            df_total["Year"] = df_total["Date_dt"].dt.year
            df_total["Date"] = df_total["Date_dt"].dt.strftime("%d/%m/%Y")

            monthly_summary = []
            for (year, month), group in df_total.groupby(["Year", "Month"]):
                internal_count = (group["Classification"] == "🟢Internal").sum()
                incoming_count = (group["Classification"] == "🔵Incoming").sum()
                outgoing_count = (group["Classification"] == "🟡Outgoing").sum()
                total_income = group.loc[group["Classification"] == "🔵Incoming", "Credit"].sum()
                total_expense = group.loc[group["Classification"] == "🟡Outgoing", "Debit"].sum()
                total_incoming_gst = group.loc[group["Classification"] == "🔵Incoming", "GST"].sum()
                total_outgoing_gst = group.loc[group["Classification"] == "🟡Outgoing", "GST"].sum()
                year_month_str = f"{year}/{month:02d}"

                monthly_summary.append([
                    year_month_str, internal_count, incoming_count, outgoing_count,
                    total_income, total_expense, total_incoming_gst, total_outgoing_gst,
                ])

            summary_df = pd.DataFrame(
                monthly_summary,
                columns=[
                    "Year/Month", "🟢Internal Transfers", "🔵Incoming Count", "🟡Outgoing Count",
                    "Total 🔵Incoming Income", "Total 🟡Outgoing Expense",
                    "Total 🔵Incoming GST", "Total 🟡Outgoing GST",
                ],
            )

            totals = pd.DataFrame([[
                "Grand Total",
                summary_df["🟢Internal Transfers"].sum(),
                summary_df["🔵Incoming Count"].sum(),
                summary_df["🟡Outgoing Count"].sum(),
                summary_df["Total 🔵Incoming Income"].sum(),
                summary_df["Total 🟡Outgoing Expense"].sum(),
                summary_df["Total 🔵Incoming GST"].sum(),
                summary_df["Total 🟡Outgoing GST"].sum(),
            ]], columns=summary_df.columns)

            summary_df = pd.concat([summary_df, totals], ignore_index=True)

            for col in ["Total 🔵Incoming Income", "Total 🟡Outgoing Expense", "Total 🔵Incoming GST", "Total 🟡Outgoing GST"]:
                summary_df[col] = summary_df[col].map(lambda x: f"{x:.2f}" if pd.notnull(x) else "")

            def highlight_total(row):
                return (
                    ["background-color: #fff3cd; font-weight: bold"] * len(row)
                    if row["Year/Month"] == "Grand Total"
                    else [""] * len(row)
                )

            with st.expander("📊Monthly Summary", expanded=False):
                # Add CSS for monthly summary table
                st.markdown("""
                    <style>
                        div[data-testid="stDataFrame"] {
                            font-size: 11px !important;
                        }
                        div[data-testid="stDataFrame"] table {
                            font-size: 11px !important;
                        }
                    </style>
                """, unsafe_allow_html=True)
                st.dataframe(summary_df.style.apply(highlight_total, axis=1))

        # --- Detailed Table ---
        key_columns = [
            "Select", "Date", "Bank", "Account", "Description", "Debit", "Credit",
            "Classification", "PairID", "GL Account", "GST", "GST Category", "Who"
        ]

        df_display = df_total[[col for col in key_columns if col in df_total.columns and col != "Select"]].copy()
        
        # Add Select column
        df_display.insert(0, "Select", False)
        for idx in st.session_state.selected_rows:
            if idx in df_display.index:
                df_display.at[idx, "Select"] = True

        # Sort by PairID and Date
        if "PairID" in df_display.columns and df_display["PairID"].notna().any():
            df_display = df_display.sort_values(by=["PairID", "Date"], ascending=[True, True], na_position='last')
        else:
            df_display = df_display.sort_values(by=["Date"], ascending=True)

        # Pagination
        total_rows = len(df_display)
        total_pages = (total_rows // st.session_state.page_size) + (
            1 if total_rows % st.session_state.page_size > 0 else 0
        )

        with st.expander("📄Transaction Details", expanded=True):
            # Add transaction form (top of section)
            with st.expander("➕ Add Transaction", expanded=False):
                with st.form("add_transaction_form", clear_on_submit=True):
                    form_col1, form_col2, form_col3, form_col4 = st.columns(4)

                    with form_col1:
                        new_date_value = st.date_input("Date")
                        new_bank = st.text_input("Bank")
                        new_account = st.text_input("Account")

                    with form_col2:
                        new_description = st.text_input("Description")
                        new_classification = st.selectbox(
                            "Classification",
                            ["🟢Internal", "🔵Incoming", "🟡Outgoing"],
                            index=1,
                        )
                        new_pairid = st.text_input("PairID", value="")

                    with form_col3:
                        new_debit = st.number_input("Debit", min_value=0.0, value=0.0, step=0.01)
                        new_credit = st.number_input("Credit", min_value=0.0, value=0.0, step=0.01)
                        new_gl_account = st.selectbox("GL Account", GL_ACCOUNT_OPTIONS, index=0)

                    with form_col4:
                        default_gst_index = GST_CLASSIFY_OPTIONS.index("Unknown") if "Unknown" in GST_CLASSIFY_OPTIONS else 0
                        new_gst_category = st.selectbox(
                            "GST Category",
                            GST_CLASSIFY_OPTIONS,
                            index=default_gst_index,
                        )
                        inferred_who = classify_category.extract_who_bank(new_description)
                        new_who = st.text_input("Who", value=inferred_who)

                    add_submit = st.form_submit_button("Add Entry")

                if add_submit:
                    if not new_description.strip():
                        st.error("Description is required.")
                    else:
                        new_date = new_date_value.strftime("%d/%m/%Y") if new_date_value else ""
                        new_gst_value = calculate_gst_value(new_debit, new_credit, new_gst_category)

                        numeric_index = pd.to_numeric(st.session_state.edited_df_cache.index, errors="coerce")
                        if len(numeric_index.dropna()) > 0:
                            new_index = int(numeric_index.max()) + 1
                        else:
                            new_index = len(st.session_state.edited_df_cache)

                        new_row = {col: "" for col in st.session_state.edited_df_cache.columns}
                        new_row.update(
                            {
                                "Date": new_date,
                                "Bank": new_bank,
                                "Account": new_account,
                                "Description": new_description,
                                "Debit": float(new_debit),
                                "Credit": float(new_credit),
                                "Classification": new_classification,
                                "PairID": new_pairid,
                                "GL Account": normalize_gl_account(new_gl_account),
                                "GST Category": new_gst_category,
                                "GST": float(new_gst_value),
                                "Who": new_who.strip() if new_who.strip() else inferred_who,
                            }
                        )

                        user_id = _get_logged_in_user_id()
                        created_db_id = None
                        if user_id is not None:
                            try:
                                payload_tx = {
                                    "date": new_date,
                                    "bank": new_bank,
                                    "account": new_account,
                                    "description": new_description,
                                    "debit": float(new_debit),
                                    "credit": float(new_credit),
                                    "classification": new_classification,
                                    "pair_id": new_pairid,
                                    "gl_account": normalize_gl_account(new_gl_account),
                                    "gst": float(new_gst_value),
                                    "gst_category": new_gst_category,
                                    "who": new_who.strip() if new_who.strip() else inferred_who,
                                }
                                created = _create_transaction_in_db(user_id, payload_tx)
                                created_db_id = created.get("id")
                            except Exception as exc:
                                st.toast(f"DB insert failed, kept in session only: {exc}", icon="❌")

                        if created_db_id is not None:
                            if "DB ID" not in st.session_state.edited_df_cache.columns:
                                st.session_state.edited_df_cache["DB ID"] = pd.NA
                            new_row["DB ID"] = created_db_id

                        st.session_state.edited_df_cache.loc[new_index] = new_row
                        st.session_state.reconciliation_results = st.session_state.edited_df_cache.copy()
                        st.session_state.updated_pages.add(st.session_state.page_number)

                        if st.session_state.get("current_session_id"):
                            session_manager.save_output_data(
                                username,
                                st.session_state.current_session_id,
                                st.session_state.reconciliation_results,
                                st.session_state.pending_changes,
                                st.session_state.updated_pages,
                                st.session_state.page_number,
                            )

                        if created_db_id is not None:
                            st.success(f"Transaction added to session and DB (ID {created_db_id}).")
                        else:
                            st.success("Transaction added and saved to session.")
                        st.rerun()

            with st.expander("✏️ Edit Transaction", expanded=False):
                if st.session_state.edited_df_cache is None or st.session_state.edited_df_cache.empty:
                    st.info("No transactions available to edit.")
                else:
                    edit_indices = sorted(
                        idx
                        for idx in st.session_state.selected_rows
                        if idx in st.session_state.edited_df_cache.index
                    )

                    if not edit_indices:
                        st.info("Select row(s) from the table checkbox first, then edit here.")
                        edit_indices = []

                    def format_edit_option(idx):
                        row = st.session_state.edited_df_cache.loc[idx]
                        date_txt = "" if pd.isna(row.get("Date", "")) else str(row.get("Date", ""))
                        desc_txt = "" if pd.isna(row.get("Description", "")) else str(row.get("Description", ""))
                        desc_txt = desc_txt[:40] + ("..." if len(desc_txt) > 40 else "")
                        return f"{idx} | {date_txt} | {desc_txt}"

                    if edit_indices:
                        selected_edit_idx = st.selectbox(
                            "Select entry to edit",
                            options=edit_indices,
                            format_func=format_edit_option,
                        )

                        selected_row = st.session_state.edited_df_cache.loc[selected_edit_idx]

                        current_date = pd.to_datetime(selected_row.get("Date", ""), errors="coerce", dayfirst=True)
                        default_edit_date = current_date.date() if pd.notnull(current_date) else pd.Timestamp.today().date()

                        current_classification = str(selected_row.get("Classification", "") or "")
                        classification_options = ["🟢Internal", "🔵Incoming", "🟡Outgoing"]
                        default_classification_idx = (
                            classification_options.index(current_classification)
                            if current_classification in classification_options
                            else 1
                        )

                        current_gl = normalize_gl_account(selected_row.get("GL Account", ""))
                        default_gl_idx = (
                            GL_ACCOUNT_OPTIONS.index(current_gl)
                            if current_gl in GL_ACCOUNT_OPTIONS
                            else len(GL_ACCOUNT_OPTIONS) - 1
                        )

                        current_gst_category = str(selected_row.get("GST Category", "") or "")
                        default_gst_idx = next(
                            (
                                idx
                                for idx, option in enumerate(GST_CLASSIFY_OPTIONS)
                                if str(option).lower() == current_gst_category.strip().lower()
                            ),
                            GST_CLASSIFY_OPTIONS.index("Unknown") if "Unknown" in GST_CLASSIFY_OPTIONS else 0,
                        )

                        current_debit = pd.to_numeric(selected_row.get("Debit", 0.0), errors="coerce")
                        current_credit = pd.to_numeric(selected_row.get("Credit", 0.0), errors="coerce")

                        with st.form(f"edit_transaction_form_{selected_edit_idx}"):
                            edit_col1, edit_col2, edit_col3, edit_col4 = st.columns(4)

                            with edit_col1:
                                edit_date_value = st.date_input("Date", value=default_edit_date, key=f"edit_date_{selected_edit_idx}")
                                edit_bank = st.text_input("Bank", value="" if pd.isna(selected_row.get("Bank", "")) else str(selected_row.get("Bank", "")), key=f"edit_bank_{selected_edit_idx}")
                                edit_account = st.text_input("Account", value="" if pd.isna(selected_row.get("Account", "")) else str(selected_row.get("Account", "")), key=f"edit_account_{selected_edit_idx}")

                            with edit_col2:
                                edit_description = st.text_input("Description", value="" if pd.isna(selected_row.get("Description", "")) else str(selected_row.get("Description", "")), key=f"edit_desc_{selected_edit_idx}")
                                edit_classification = st.selectbox(
                                    "Classification",
                                    classification_options,
                                    index=default_classification_idx,
                                    key=f"edit_classification_{selected_edit_idx}",
                                )
                                edit_pairid = st.text_input("PairID", value="" if pd.isna(selected_row.get("PairID", "")) else str(selected_row.get("PairID", "")), key=f"edit_pairid_{selected_edit_idx}")

                            with edit_col3:
                                edit_debit = st.number_input(
                                    "Debit",
                                    min_value=0.0,
                                    value=float(current_debit) if pd.notnull(current_debit) else 0.0,
                                    step=0.01,
                                    key=f"edit_debit_{selected_edit_idx}",
                                )
                                edit_credit = st.number_input(
                                    "Credit",
                                    min_value=0.0,
                                    value=float(current_credit) if pd.notnull(current_credit) else 0.0,
                                    step=0.01,
                                    key=f"edit_credit_{selected_edit_idx}",
                                )
                                edit_gl_account = st.selectbox(
                                    "GL Account",
                                    GL_ACCOUNT_OPTIONS,
                                    index=default_gl_idx,
                                    key=f"edit_gl_{selected_edit_idx}",
                                )

                            with edit_col4:
                                edit_gst_category = st.selectbox(
                                    "GST Category",
                                    GST_CLASSIFY_OPTIONS,
                                    index=default_gst_idx,
                                    key=f"edit_gst_cat_{selected_edit_idx}",
                                )
                                inferred_edit_who = classify_category.extract_who_bank(edit_description)
                                edit_who = st.text_input(
                                    "Who",
                                    value="" if pd.isna(selected_row.get("Who", "")) else str(selected_row.get("Who", "")),
                                    key=f"edit_who_{selected_edit_idx}",
                                )

                            update_submit = st.form_submit_button("Update Entry")

                        if update_submit:
                            if not edit_description.strip():
                                st.error("Description is required.")
                            else:
                                edit_date = edit_date_value.strftime("%d/%m/%Y") if edit_date_value else ""
                                edit_gst_value = calculate_gst_value(edit_debit, edit_credit, edit_gst_category)

                                updates = {
                                    "Date": edit_date,
                                    "Bank": edit_bank,
                                    "Account": edit_account,
                                    "Description": edit_description,
                                    "Debit": float(edit_debit),
                                    "Credit": float(edit_credit),
                                    "Classification": edit_classification,
                                    "PairID": edit_pairid,
                                    "GL Account": normalize_gl_account(edit_gl_account),
                                    "GST Category": edit_gst_category,
                                    "GST": float(edit_gst_value),
                                    "Who": edit_who.strip() if edit_who.strip() else inferred_edit_who,
                                }

                                for col, val in updates.items():
                                    if col in st.session_state.edited_df_cache.columns:
                                        st.session_state.edited_df_cache.at[selected_edit_idx, col] = val

                                user_id = _get_logged_in_user_id()
                                db_id = None
                                if "DB ID" in st.session_state.edited_df_cache.columns:
                                    raw_db_id = st.session_state.edited_df_cache.at[selected_edit_idx, "DB ID"]
                                    if pd.notnull(raw_db_id):
                                        try:
                                            db_id = int(raw_db_id)
                                        except (TypeError, ValueError):
                                            db_id = None

                                if user_id is not None and db_id is not None:
                                    try:
                                        payload_tx = {
                                            "date": updates["Date"],
                                            "bank": updates["Bank"],
                                            "account": updates["Account"],
                                            "description": updates["Description"],
                                            "debit": updates["Debit"],
                                            "credit": updates["Credit"],
                                            "classification": updates["Classification"],
                                            "pair_id": updates["PairID"],
                                            "gl_account": updates["GL Account"],
                                            "gst": updates["GST"],
                                            "gst_category": updates["GST Category"],
                                            "who": updates["Who"],
                                        }
                                        _update_transaction_in_db(user_id, db_id, payload_tx)
                                        st.toast(f"Updated DB row ID {db_id}", icon="✅")
                                    except Exception as exc:
                                        st.toast(f"DB update failed: {exc}", icon="❌")

                                st.session_state.reconciliation_results = st.session_state.edited_df_cache.copy()
                                st.session_state.updated_pages.add(st.session_state.page_number)

                                if st.session_state.get("current_session_id"):
                                    session_manager.save_output_data(
                                        username,
                                        st.session_state.current_session_id,
                                        st.session_state.reconciliation_results,
                                        st.session_state.pending_changes,
                                        st.session_state.updated_pages,
                                        st.session_state.page_number,
                                    )

                                if db_id is not None:
                                    st.success("Transaction updated in session and DB.")
                                else:
                                    st.success("Transaction updated and saved to session. Use Save to DB for new rows.")
                                st.rerun()

            # Status bar and filters in same row
            status_col1, status_col2 = st.columns([3, 1])
            
            with status_col1:
                pending_count = len(st.session_state.pending_changes)
                status_msg = f"**💡Status:** {pending_count} pending change(s) | Pages updated: {len(st.session_state.updated_pages)}/{total_pages} | Session: {st.session_state.current_session_id or 'New'}"
                st.markdown(status_msg)
            
            with status_col2:
                # Filter checkboxes on top right
                filter_cols = st.columns(3)
                with filter_cols[0]:
                    st.session_state.filter_internal = st.checkbox("🟢", value=st.session_state.filter_internal, key=f"filter_internal_{st.session_state.page_number}")
                with filter_cols[1]:
                    st.session_state.filter_incoming = st.checkbox("🔵", value=st.session_state.filter_incoming, key=f"filter_incoming_{st.session_state.page_number}")
                with filter_cols[2]:
                    st.session_state.filter_outgoing = st.checkbox("🟡", value=st.session_state.filter_outgoing, key=f"filter_outgoing_{st.session_state.page_number}")
            
            # Apply filters to df_display before pagination
            df_filtered = df_display.copy()
            filter_conditions = []
            
            if st.session_state.filter_internal:
                filter_conditions.append(df_filtered["Classification"] == "🟢Internal")
            if st.session_state.filter_incoming:
                filter_conditions.append(df_filtered["Classification"] == "🔵Incoming")
            if st.session_state.filter_outgoing:
                filter_conditions.append(df_filtered["Classification"] == "🟡Outgoing")
            
            # Apply combined filter
            if filter_conditions:
                combined_filter = filter_conditions[0]
                for condition in filter_conditions[1:]:
                    combined_filter = combined_filter | condition
                df_filtered = df_filtered[combined_filter]
            else:
                # If no filters selected, show empty dataframe
                df_filtered = df_filtered.iloc[0:0]
            
            # Recalculate pagination based on filtered data
            total_rows_filtered = len(df_filtered)
            total_pages_filtered = (total_rows_filtered // st.session_state.page_size) + (
                1 if total_rows_filtered % st.session_state.page_size > 0 else 0
            )
            
            # Ensure page number is within bounds
            if st.session_state.page_number > total_pages_filtered and total_pages_filtered > 0:
                st.session_state.page_number = total_pages_filtered
            elif total_pages_filtered == 0:
                st.session_state.page_number = 1
            
            start_idx_filtered = (st.session_state.page_number - 1) * st.session_state.page_size
            end_idx_filtered = start_idx_filtered + st.session_state.page_size
            df_page = df_filtered.iloc[start_idx_filtered:end_idx_filtered].copy()

            # Apply ALL pending changes to the current page BEFORE displaying
            for idx in df_page.index:
                if idx in st.session_state.pending_changes:
                    pending_category = st.session_state.pending_changes[idx]
                    df_page.at[idx, "GST Category"] = pending_category
                    debit_val = df_page.at[idx, "Debit"] if "Debit" in df_page.columns else 0
                    credit_val = df_page.at[idx, "Credit"] if "Credit" in df_page.columns else 0
                    df_page.at[idx, "GST"] = calculate_classified_gst_value(debit_val, credit_val, pending_category)
            
            # Sync GL Account values from latest edited_df_cache for current page rows
            if "GL Account" in df_page.columns and "GL Account" in st.session_state.edited_df_cache.columns:
                for idx in df_page.index:
                    gl_val = st.session_state.edited_df_cache.at[idx, "GL Account"]
                    df_page.at[idx, "GL Account"] = gl_val

            # Sync GST Category values from latest edited_df_cache for current page rows
            if "GST Category" in df_page.columns and "GST Category" in st.session_state.edited_df_cache.columns:
                for idx in df_page.index:
                    gst_cat_val = st.session_state.edited_df_cache.at[idx, "GST Category"]
                    df_page.at[idx, "GST Category"] = gst_cat_val

            # Sync Who values from latest edited_df_cache for current page rows
            if "Who" in df_page.columns and "Who" in st.session_state.edited_df_cache.columns:
                for idx in df_page.index:
                    who_val = st.session_state.edited_df_cache.at[idx, "Who"]
                    df_page.at[idx, "Who"] = who_val

            # Prepare display with formatting for non-editable columns
            df_page_display = df_page.copy()
            for col in ["Debit", "Credit", "GST"]:
                if col in df_page_display.columns:
                    df_page_display[col] = df_page_display[col].map(
                        lambda x: f"{x:.2f}" if pd.notnull(x) else ""
                    )
            
            # Delete selected rows button - always visible, disabled if no selection
            delete_button_col1, delete_button_col2 = st.columns([3, 1])
            with delete_button_col1:
                selected_count = len(st.session_state.selected_rows)
                button_label = f"🗑️ Delete Selected Row(s)" if selected_count == 0 else f"🗑️ Delete {selected_count} Selected Row(s)"
                if st.button(button_label, type="primary", disabled=selected_count == 0, key="delete_selected_rows"):
                    selected_indices = set(st.session_state.selected_rows)
                    rows_deleted = len(selected_indices)

                    db_deleted = 0
                    db_delete_errors = []
                    user_id = _get_logged_in_user_id()

                    if user_id is not None and "DB ID" in st.session_state.edited_df_cache.columns:
                        for idx in selected_indices:
                            if idx not in st.session_state.edited_df_cache.index:
                                continue

                            raw_db_id = st.session_state.edited_df_cache.at[idx, "DB ID"]
                            if pd.isna(raw_db_id) or str(raw_db_id).strip() == "":
                                continue

                            try:
                                _delete_transaction_in_db(user_id, int(raw_db_id))
                                db_deleted += 1
                            except Exception as exc:
                                db_delete_errors.append(f"{raw_db_id}: {exc}")

                    # Remove selected rows from session dataframes while preserving hidden columns like DB ID.
                    remaining_df = st.session_state.edited_df_cache.drop(index=list(selected_indices), errors="ignore").copy()
                    st.session_state.edited_df_cache = remaining_df
                    st.session_state.reconciliation_results = remaining_df.copy()
                    st.session_state.pending_changes = {
                        idx: val
                        for idx, val in st.session_state.pending_changes.items()
                        if idx not in selected_indices
                    }
                    st.session_state.updated_pages.add(st.session_state.page_number)

                    st.session_state.selected_rows = set()
                    
                    # Save to session
                    if st.session_state.current_session_id:
                        session_manager.save_output_data(
                            username,
                            st.session_state.current_session_id,
                            st.session_state.reconciliation_results,
                            st.session_state.pending_changes,
                            st.session_state.updated_pages,
                            st.session_state.page_number
                        )

                    if db_delete_errors:
                        st.warning(
                            f"Deleted {rows_deleted} row(s) from session. "
                            f"Deleted {db_deleted} row(s) from DB. "
                            f"DB delete errors: {len(db_delete_errors)}"
                        )
                    elif db_deleted > 0:
                        st.success(f"Deleted {rows_deleted} row(s) from session and {db_deleted} row(s) from DB.")
                    else:
                        st.success(f"Deleted {rows_deleted} row(s) from session.")
                    st.rerun()
            
            # Add CSS for table styling
            st.markdown("""
                <style>
                    .table-header {
                        font-weight: bold;
                        background-color: #4a6fa5;
                        color: #ffffff;
                        padding: 1px 4px;
                        border-bottom: 2px solid #2d4a7a;
                        font-size: 12px;
                        text-align: center;
                    }
                    .table-cell {
                        font-size: 12px;
                        padding: 4px 2px;
                    }
                    div[data-testid="stText"] > div {
                        font-size: 12px !important;
                    }
                </style>
            """, unsafe_allow_html=True)
            
            # Display table header
            header_cols = st.columns([0.5, 1, 1, 1, 3, 1, 1, 1.5, 1, 1, 1, 1.5, 1])
            headers = ["☑", "Date", "Bank", "Account", "Description", "Debit", "Credit", 
                      "Classification", "PairID", "GL Account", "GST", "GST Category", "Who"]
            
            for col, header in zip(header_cols, headers):
                with col:
                    st.markdown(f"<div class='table-header'>{header}</div>", unsafe_allow_html=True)
            
            # Create a container for the table rows
            for display_idx, original_idx in enumerate(df_page.index):
                row_data = df_page_display.iloc[display_idx]
                
                # Create columns for each row
                cols = st.columns([0.5, 1, 1, 1, 3, 1, 1, 1.5, 1, 1, 1, 1.5, 1])
                
                # Select checkbox
                with cols[0]:
                    # Use a unique key that forces immediate re-render
                    checkbox_key = f"select_{original_idx}_{st.session_state.page_number}_{len(st.session_state.selected_rows)}"
                    is_selected = st.checkbox(
                        "☑", 
                        value=original_idx in st.session_state.selected_rows,
                        key=checkbox_key,
                        label_visibility="collapsed"
                    )
                    if is_selected and original_idx not in st.session_state.selected_rows:
                        st.session_state.selected_rows.add(original_idx)
                        st.rerun()
                    elif not is_selected and original_idx in st.session_state.selected_rows:
                        st.session_state.selected_rows.discard(original_idx)
                        st.rerun()
                
                # Display other columns as text with smaller font
                with cols[1]:
                    st.markdown(f"<div class='table-cell'>{str(row_data.get('Date', ''))}</div>", unsafe_allow_html=True)
                with cols[2]:
                    st.markdown(f"<div class='table-cell'>{str(row_data.get('Bank', ''))}</div>", unsafe_allow_html=True)
                with cols[3]:
                    st.markdown(f"<div class='table-cell'>{str(row_data.get('Account', ''))}</div>", unsafe_allow_html=True)

                with cols[4]:
                    desc = str(row_data.get("Description", "")) \
                        .replace('"', '&quot;') \
                        .replace("'", "&apos;")

                    st.markdown(
                        f"""
                        <div style="
                            max-width: 250px;
                            font-size: 11px;
                            white-space: nowrap;
                            overflow: hidden;
                            text-overflow: ellipsis;
                            cursor: pointer;
                        " title="{desc}">
                            {desc}
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

                with cols[5]:
                    st.markdown(f"<div class='table-cell'>{str(row_data.get('Debit', ''))}</div>", unsafe_allow_html=True)
                with cols[6]:
                    st.markdown(f"<div class='table-cell'>{str(row_data.get('Credit', ''))}</div>", unsafe_allow_html=True)
                with cols[7]:
                    st.markdown(f"<div class='table-cell'>{str(row_data.get('Classification', ''))}</div>", unsafe_allow_html=True)
                with cols[8]:
                    st.markdown(f"<div class='table-cell'>{str(row_data.get('PairID', ''))}</div>", unsafe_allow_html=True)
                with cols[9]:
                    # GL Account selectbox - editable
                    # Always get the latest value from edited_df_cache, not from row_data
                    current_gl = normalize_gl_account(st.session_state.edited_df_cache.at[original_idx, "GL Account"])
                    classification = row_data.get('Classification', '')
                    
                
                    
                    # Apply CSS for font size
                    st.markdown(
                       """
                       <style>
                       div[data-baseweb="select"] > div > div > div {
                          font-size: 12px;
                          padding-top: 0px !important;
                          height: 24px !important;
                       }
                       </style>
                       """,
                       unsafe_allow_html=True
                    )
                    
                    new_gl = st.selectbox(
                        "GL Account",
                        options=GL_ACCOUNT_OPTIONS,
                        index=GL_ACCOUNT_OPTIONS.index(current_gl) if current_gl in GL_ACCOUNT_OPTIONS else len(GL_ACCOUNT_OPTIONS) - 1,
                        key=f"gl_account_{original_idx}_{current_gl}_{st.session_state.page_number}",
                        label_visibility="collapsed"
                    )
                    
                    # Track GL Account changes separately
                    original_gl = normalize_gl_account(st.session_state.edited_df_cache.at[original_idx, "GL Account"])
                    if normalize_gl_account(new_gl) != original_gl:
                        # Update directly in edited_df_cache and reconciliation_results
                        normalized_new_gl = normalize_gl_account(new_gl)
                        st.session_state.edited_df_cache.at[original_idx, "GL Account"] = normalized_new_gl
                        st.session_state.reconciliation_results.at[original_idx, "GL Account"] = normalized_new_gl
                        st.session_state.updated_pages.add(st.session_state.page_number)
                        
                        # Save to session immediately
                        if st.session_state.get("current_session_id"):
                            session_manager.save_output_data(
                                username,
                                st.session_state.current_session_id,
                                st.session_state.reconciliation_results,
                                st.session_state.pending_changes,
                                st.session_state.updated_pages,
                                st.session_state.page_number
                            )
                with cols[10]:
                    st.markdown(f"<div class='table-cell'>{str(row_data.get('GST', ''))}</div>", unsafe_allow_html=True)
                
                # GST Category selectbox - editable
                with cols[11]:
                    current_category = st.session_state.pending_changes.get(
                        original_idx,
                        df_page.at[original_idx, "GST Category"]
                    )

                    # Apply CSS for font size
                    st.markdown(
                       """
                       <style>
                       div[data-baseweb="select"] > div > div > div {
                          font-size: 12px;
                          padding-top: 0px !important;
                          height: 24px !important;  /* adjust height as needed */
                       }
                       </style>
                       """,
                       unsafe_allow_html=True
                    )
                    
                    new_category = st.selectbox(
                        "GST Cat",
                        options=GST_CLASSIFY_OPTIONS,
                        index=next(
                            (
                                idx
                                for idx, option in enumerate(GST_CLASSIFY_OPTIONS)
                                if str(option).lower() == str(current_category).strip().lower()
                            ),
                            GST_CLASSIFY_OPTIONS.index("Unknown") if "Unknown" in GST_CLASSIFY_OPTIONS else 0,
                        ),
                        key=f"gst_cat_{original_idx}_{str(current_category).strip().lower()}_{st.session_state.page_number}",
                        label_visibility="collapsed"
                    )
                    
                    # Track changes
                    original_from_cache = st.session_state.edited_df_cache.at[original_idx, "GST Category"]
                    original_from_cache = "" if pd.isna(original_from_cache) else str(original_from_cache)
                    if new_category != original_from_cache:
                        debit_val = st.session_state.edited_df_cache.at[original_idx, "Debit"] if "Debit" in st.session_state.edited_df_cache.columns else 0
                        credit_val = st.session_state.edited_df_cache.at[original_idx, "Credit"] if "Credit" in st.session_state.edited_df_cache.columns else 0

                        # Keep the same validation rules but apply updates immediately.
                        if new_category == "GST on Sale" and float(credit_val or 0) == 0:
                            st.warning(f"Row index {original_idx}: GST on Sale requires non-zero Credit value")
                            st.session_state.pending_changes[original_idx] = new_category
                        elif new_category == "GST on Purchase" and float(debit_val or 0) == 0:
                            st.warning(f"Row index {original_idx}: GST on Purchase requires non-zero Debit value")
                            st.session_state.pending_changes[original_idx] = new_category
                        else:
                            live_gst = calculate_classified_gst_value(debit_val, credit_val, new_category)
                            st.session_state.edited_df_cache.at[original_idx, "GST Category"] = new_category
                            st.session_state.edited_df_cache.at[original_idx, "GST"] = float(live_gst)
                            st.session_state.reconciliation_results.at[original_idx, "GST Category"] = new_category
                            st.session_state.reconciliation_results.at[original_idx, "GST"] = float(live_gst)
                            st.session_state.updated_pages.add(st.session_state.page_number)
                            if original_idx in st.session_state.pending_changes:
                                del st.session_state.pending_changes[original_idx]
                    elif original_idx in st.session_state.pending_changes:
                        del st.session_state.pending_changes[original_idx]

                    # Save GST pending changes to session immediately (same behavior as GL edits)
                    if st.session_state.get("current_session_id"):
                        if original_idx in st.session_state.pending_changes:
                            session_manager.save_pending_changes_only(
                                username,
                                st.session_state.current_session_id,
                                st.session_state.pending_changes,
                                st.session_state.updated_pages,
                                st.session_state.page_number
                            )
                        else:
                            session_manager.save_output_data(
                                username,
                                st.session_state.current_session_id,
                                st.session_state.reconciliation_results,
                                st.session_state.pending_changes,
                                st.session_state.updated_pages,
                                st.session_state.page_number,
                            )
                
                # Who column
                with cols[12]:
                    st.markdown(f"<div class='table-cell'>{str(row_data.get('Who', ''))}</div>", unsafe_allow_html=True)

            # Pagination controls, Submit button, and Save to DB
            pag_col1, pag_col2, pag_col3, pag_col4, pag_col5 = st.columns([1, 1, 1, 1, 1])
            
            with pag_col1:
                if st.button("⬅ Previous", key="prev_page") and st.session_state.page_number > 1:
                    st.session_state.page_number -= 1
                    # Save state before navigation
                    save_current_session()
                    st.rerun()
            
            with pag_col2:
                st.markdown(f"<div style='text-align: center; padding-top: 8px;'>Page {st.session_state.page_number} of {total_pages_filtered}</div>", unsafe_allow_html=True)
            
            with pag_col3:
                if st.button("Next ➡", key="next_page") and st.session_state.page_number < total_pages_filtered:
                    st.session_state.page_number += 1
                    # Save state before navigation
                    save_current_session()
                    st.rerun()
            
            with pag_col4:
                if st.button("✅ Change Submit", key="submit_changes", disabled=len(st.session_state.pending_changes) == 0):
                    # Apply all pending changes and recalculate GST
                    validation_errors = []
                    
                    for idx, new_category in st.session_state.pending_changes.items():
                        # Get original numeric values from edited_df_cache
                        debit = st.session_state.edited_df_cache.at[idx, "Debit"] if pd.notnull(st.session_state.edited_df_cache.at[idx, "Debit"]) else 0
                        credit = st.session_state.edited_df_cache.at[idx, "Credit"] if pd.notnull(st.session_state.edited_df_cache.at[idx, "Credit"]) else 0
                        
                        # Validation: GST on Sale requires non-zero credit
                        if new_category == "GST on Sale" and credit == 0:
                            validation_errors.append(f"Row index {idx}: GST on Sale requires non-zero Credit value")
                            continue
                        
                        # Validation: GST on Purchase requires non-zero debit
                        if new_category == "GST on Purchase" and debit == 0:
                            validation_errors.append(f"Row index {idx}: GST on Purchase requires non-zero Debit value")
                            continue
                        
                        # Recalculate GST
                        new_gst = calculate_gst_value(debit, credit, new_category)
                        
                        # Update in main dataframe
                        st.session_state.edited_df_cache.at[idx, "GST Category"] = new_category
                        st.session_state.edited_df_cache.at[idx, "GST"] = new_gst
                    
                    if validation_errors:
                        st.error("Validation Errors:\n\n" + "\n\n".join(validation_errors))
                    else:
                        # Update reconciliation results
                        st.session_state.reconciliation_results = st.session_state.edited_df_cache.copy()
                        st.session_state.updated_pages.add(st.session_state.page_number)

                        # Clear pending changes before save to avoid reloading stale pending values
                        st.session_state.pending_changes = {}
                        
                        # Save to session
                        if st.session_state.current_session_id:
                            session_manager.save_output_data(
                                username,
                                st.session_state.current_session_id,
                                st.session_state.reconciliation_results,
                                st.session_state.pending_changes,
                                st.session_state.updated_pages,
                                st.session_state.page_number
                            )
                        
                        st.success(f"✅ Changes submitted! Page {st.session_state.page_number} updated.")
                        st.rerun()

            with pag_col5:
                if st.button("💾 Save to DB", key="save_to_db_btn", type="primary"):
                    user_id = _get_logged_in_user_id()
                    if user_id is None:
                        st.toast("No logged-in user ID found. Please log in again.", icon="❌")
                    else:
                        df_for_db = st.session_state.edited_df_cache.copy() if st.session_state.edited_df_cache is not None else st.session_state.reconciliation_results.copy()
                        for col in ["Select", "Date_dt", "Month", "Year"]:
                            if col in df_for_db.columns:
                                df_for_db = df_for_db.drop(columns=[col])
                        transactions = _build_transactions_payload_from_display(df_for_db)
                        try:
                            result = _save_transactions_to_db(user_id, transactions)
                            # Keep the current session-scoped dataframe in memory.
                            # Reloading all user DB rows here causes cross-session data bleed.
                            st.session_state.reconciliation_results = df_for_db.copy()
                            st.session_state.edited_df_cache = df_for_db.copy()
                            st.session_state.pending_changes = {}
                            st.session_state.selected_rows = set()
                            st.session_state.page_number = 1

                            if st.session_state.get("current_session_id"):
                                session_manager.save_output_data(
                                    username,
                                    st.session_state.current_session_id,
                                    st.session_state.reconciliation_results,
                                    st.session_state.pending_changes,
                                    st.session_state.updated_pages,
                                    st.session_state.page_number,
                                )

                            st.toast(
                                f"Saved {result.get('saved', 0)} new, "
                                f"updated {result.get('updated', 0)}, "
                                f"skipped {result.get('skipped', 0)} unchanged.",
                                icon="✅"
                            )
                            st.rerun()
                        except Exception as exc:
                            st.toast(f"Failed to save to DB: {exc}", icon="❌")

        # Export with updated GST values - now at the top
        # Remove Select column before export
        df_export = df_display.drop(columns=["Select"]) if "Select" in df_display.columns else df_display
        excel_bytes = get_excel_bytes(df_export, summary_df)
        with download_placeholder:
            st.download_button(
                label="📥 Download Excel",
                data=excel_bytes,
                file_name="reconciliation_results.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        # Auto-save on any interaction
        save_current_session()