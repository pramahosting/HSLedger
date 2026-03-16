# frontend/components/render_output_ui.py

import json
from urllib import error, request

import streamlit as st
import pandas as pd
from backend.reconciliation import exporter
from backend.reconciliation.session_manager import session_manager
from backend.reconciliation.gst_calculator import GST_CATEGORY_OPTIONS, calculate_gst_value
from backend.ai_model import classify_category

API_BASE_URL = "http://127.0.0.1:8000"


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
GST_ENUM = [
    "GST on Expenses",
    "GST on Capital",
    "GST on Income",
    "GST Free Expenses",
    "GST Free Income",
    "BAS Excluded",
]

GST_CLASSIFY_OPTIONS = list(dict.fromkeys(GST_CATEGORY_OPTIONS + classify_category.GST_ENUM))


def normalize_gl_account(value):
    text = "" if pd.isna(value) else str(value).strip()
    if not text:
        return ""

    for option in classify_category.CATEGORY_ENUM:
        if option.lower() == text.lower():
            return option

    return ""

def get_excel_bytes(df_total, monthly_summary):
    return exporter.export_excel_bytes(df_total, monthly_summary)

@st.dialog("Configure Local Classifier")
def select_model_dialog(input_text=None):
    """Dialog to select a local Ollama model and start classification.

    This dialog will:
    - Fetch locally available Ollama models
    - Pre-populate a textarea with transaction descriptions from the current session data
    - Allow the user to run classification and display results inline
    """
    st.write("Fetching models from your local Ollama server...")

    # Pre-fill descriptions from the current dataset (edited cache preferred)
    descriptions_text = ""
    prefilled_pairs = []  # list of (index, description) used to map results back
    try:
        df_source = None
        if st.session_state.get("edited_df_cache") is not None:
            df_source = st.session_state.edited_df_cache
        elif st.session_state.get("reconciliation_results") is not None:
            df_source = st.session_state.reconciliation_results

        if df_source is not None and "Description" in df_source.columns:
            # Build pairs of (index, description) and join descriptions with double-newline for readability
            for idx, desc in df_source["Description"].dropna().astype(str).iloc[:10].items():
                prefilled_pairs.append((idx, desc))
            descs = [d for (_i, d) in prefilled_pairs]
            descriptions_text = "\n\n".join(descs)  # limit already applied above
        else:
            st.info("No transaction descriptions available in the current session.")
    except Exception as e:
        st.error(f"Error reading descriptions from session: {e}")

    try:
        import ollama
        models = [m.model for m in ollama.list().models]
        if not models:
            st.warning("No models found. Run 'ollama pull llama3' in your terminal.")
            return

        selected = st.selectbox("Select local engine:", models)

        # Classify all rows in the current session dataframe and write into GL Account
        if st.button("Classify All Rows in Session"):
            st.session_state.selected_model = selected
            try:
                if df_source is None or "Description" not in df_source.columns:
                    st.error("No descriptions available to classify in the current session.")
                else:
                    target_df = st.session_state.edited_df_cache if st.session_state.get("edited_df_cache") is not None else st.session_state.reconciliation_results

                    # Ensure GL Account column exists
                    if "GL Account" not in target_df.columns:
                        target_df["GL Account"] = ""
                    if "GST Category" not in target_df.columns:
                        target_df["GST Category"] = "Unknown"
                    if "Who" not in target_df.columns:
                        target_df["Who"] = "Other/Unknown"

                    rows = list(df_source.index)
                    progress = st.progress(0)
                    status = st.empty()
                    results = []

                    desc_by_idx = {}
                    for idx in rows:
                        desc = str(df_source.at[idx, "Description"]) if pd.notnull(df_source.at[idx, "Description"]) else ""
                        if desc.strip():
                            desc_by_idx[idx] = desc

                    unique_descs = [d for d in pd.unique(pd.Series(list(desc_by_idx.values()))) if str(d).strip()]

                    gl_mapping = {}
                    gst_mapping = {}
                    cache_hits = 0
                    cache_misses = 0
                    disk_cache = classify_category.load_disk_cache()
                    mem_cache = {}

                    for i, desc in enumerate(unique_descs):
                        status.text(f"Classifying unique {i+1}/{len(unique_descs)}")
                        dnorm = classify_category.normalize_desc(desc)
                        k = classify_category.cache_key(selected, dnorm, classify_category._DEFAULT_TXN_PROMPT)

                        if k in mem_cache:
                            gl_mapping[desc] = mem_cache[k].get("gl_account", mem_cache[k].get("category", ""))
                            cache_hits += 1
                        elif k in disk_cache:
                            gl_mapping[desc] = disk_cache[k].get("gl_account", disk_cache[k].get("category", ""))
                            mem_cache[k] = disk_cache[k]
                            cache_hits += 1
                        else:
                            cache_misses += 1
                            gl_mapping[desc] = classify_category.ollama_classify_gl_account_cached(
                                model=selected,
                                prompt=f"{classify_category._DEFAULT_TXN_PROMPT}\n{dnorm}",
                                base_url=classify_category.OLLAMA_CHAT_URL_DEFAULT,
                                temperature=0.0,
                                top_p=1.0,
                                cache_version=classify_category.CACHE_VERSION,
                            )["category"]
                            mem_cache[k] = {"gl_account": gl_mapping[desc], "category": gl_mapping[desc]}
                            disk_cache[k] = {"gl_account": gl_mapping[desc], "category": gl_mapping[desc]}

                        gst_k = classify_category.cache_key(selected, f"{dnorm}||{gl_mapping[desc]}", classify_category._DEFAULT_GST_PROMPT)
                        if gst_k in mem_cache:
                            gst_mapping[desc] = mem_cache[gst_k].get("gst_category", "")
                            cache_hits += 1
                        elif gst_k in disk_cache:
                            gst_mapping[desc] = disk_cache[gst_k].get("gst_category", "")
                            mem_cache[gst_k] = disk_cache[gst_k]
                            cache_hits += 1
                        else:
                            cache_misses += 1
                            gst_mapping[desc] = classify_category.ollama_predict_gst_cached(
                                model=selected,
                                prompt=f"{classify_category._DEFAULT_GST_PROMPT}\nCategory: {gl_mapping[desc]}\nDescription: {dnorm}",
                                base_url=classify_category.OLLAMA_CHAT_URL_DEFAULT,
                                temperature=0.0,
                                top_p=1.0,
                                cache_version=classify_category.CACHE_VERSION,
                            )["gst_category"]
                            mem_cache[gst_k] = {"gst_category": gst_mapping[desc]}
                            disk_cache[gst_k] = {"gst_category": gst_mapping[desc]}

                        progress.progress((i + 1) / max(1, len(unique_descs)))

                    classify_category.save_disk_cache(disk_cache)

                    for idx in rows:
                        desc = desc_by_idx.get(idx, "")
                        if not desc:
                            continue

                        current_gl = target_df.at[idx, "GL Account"] if pd.notnull(target_df.at[idx, "GL Account"]) else ""

                        should_update_gl = not current_gl or current_gl not in GL_ACCOUNT_OPTIONS

                        predicted_gl = normalize_gl_account(gl_mapping.get(desc, ""))
                        predicted_gst = gst_mapping.get(desc, "")

                        if should_update_gl:
                            target_df.at[idx, "GL Account"] = predicted_gl
                        target_df.at[idx, "GST Category"] = predicted_gst
                        target_df.at[idx, "Who"] = classify_category.extract_who_bank(desc)

                        results.append(
                            {
                                "Index": idx,
                                "Description": desc,
                                "Predicted_GL_Account": predicted_gl,
                                "Predicted_GST_Category": predicted_gst,
                                "Predicted_Who": classify_category.extract_who_bank(desc),
                            }
                        )

                    # Persist updates back to session state
                    st.session_state.edited_df_cache = target_df.copy()
                    st.session_state.reconciliation_results = target_df.copy()
                    res_df = pd.DataFrame(results)
                    st.session_state.last_classification_results = res_df

                    # Save to session storage if available
                    if st.session_state.get("current_session_id"):
                        session_manager.save_output_data(
                            st.session_state.get("username", ""),
                            st.session_state.current_session_id,
                            st.session_state.reconciliation_results,
                            st.session_state.pending_changes,
                            st.session_state.updated_pages,
                            st.session_state.page_number,
                        )

                    st.success(
                        f"Applied GL+GST prediction to {len(results)} row(s). "
                        f"Cache: {len(disk_cache)} entries, {cache_hits} hits, {cache_misses} misses."
                    )
                    st.dataframe(res_df)
                    st.session_state.force_refresh = True
                    st.rerun()

            except Exception as e:
                st.error(f"Failed to classify rows: {e}")

    except Exception as e:
        st.error(f"Could not connect to Ollama: {e}")

def render_output_ui(username, save_current_session):
    """Render the output UI including results, monthly summary, transaction details, and export."""
    
    # Ensure session state keys for local model selection exist
    if "selected_model" not in st.session_state:
        st.session_state.selected_model = None
    if "run_inference" not in st.session_state:
        st.session_state.run_inference = False

    # --- Display Results ---
    if st.session_state.reconciliation_results is not None:
        # Header with Download button
        header_col1, header_col2 = st.columns([3, 1])
        with header_col1:
            st.subheader("🔎Reconciliation Results")
        with header_col2:
            # Placeholder for download button (will be populated after data is processed)
            download_placeholder = st.empty()
            # Configure local LLM dialog button
            if st.button("⚙️ Configure Local Classifier", key="open_model_dialog_header"):
                select_model_dialog()
        
        # Use cached edited dataframe
        if st.session_state.edited_df_cache is not None:
            df_total = st.session_state.edited_df_cache.copy()
        else:
            df_total = st.session_state.reconciliation_results.copy()

        # Force refresh after classification by copying fresh from session state
        if st.session_state.get("force_refresh"):
            df_total = st.session_state.edited_df_cache.copy()
            st.session_state.force_refresh = False

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
                # Button to open local model dialog
                if st.button("⚙️ Configure Local Classifier", key="open_model_dialog_summary"):
                    select_model_dialog()
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
                    df_page.at[idx, "GST Category"] = st.session_state.pending_changes[idx]
            
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
                    # Remove selected rows
                    df_display = df_display[~df_display.index.isin(st.session_state.selected_rows)]
                    
                    # Update main dataframes
                    st.session_state.edited_df_cache = df_display.drop(columns=["Select"])
                    st.session_state.reconciliation_results = df_display.drop(columns=["Select"])
                    
                    # Clear selection
                    rows_deleted = len(st.session_state.selected_rows)
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
                    
                    st.success(f"Deleted {rows_deleted} row(s)")
                    st.rerun()
            
            # Add CSS for table styling
            st.markdown("""
                <style>
                    .table-header {
                        font-weight: bold;
                        background-color: #f0f2f6;
                        padding: 1px 4px;
                        border-bottom: 2px solid #ddd;
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
                        st.session_state.pending_changes[original_idx] = new_category
                    elif original_idx in st.session_state.pending_changes:
                        del st.session_state.pending_changes[original_idx]

                    # Save GST pending changes to session immediately (same behavior as GL edits)
                    if st.session_state.get("current_session_id"):
                        session_manager.save_pending_changes_only(
                            username,
                            st.session_state.current_session_id,
                            st.session_state.pending_changes,
                            st.session_state.updated_pages,
                            st.session_state.page_number
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
                            db_rows = _load_transactions_from_db(user_id)
                            db_df = _db_transactions_to_display_df(db_rows)

                            st.session_state.reconciliation_results = db_df.copy()
                            st.session_state.edited_df_cache = db_df.copy()
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
                                f"Saved {result.get('saved', 0)} transaction(s). "
                                f"Skipped {result.get('skipped', 0)} duplicate(s). "
                                f"Reloaded {len(db_rows)} row(s) from DB.",
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