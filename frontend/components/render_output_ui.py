# frontend/components/render_output_ui.py

import streamlit as st
import pandas as pd
from backend.reconciliation import exporter
from backend.reconciliation.session_manager import session_manager
from backend.reconciliation.gst_calculator import GST_CATEGORY_OPTIONS, calculate_gst_value
from backend.ai_model.classify_transaction import classify_with_ollama

# GL Account options for dropdown
GL_ACCOUNT_OPTIONS = [
    "Revenue",
    "Direct Costs",
    "Expense",
    "Inventory",
    "Fixed Asset",
    "GST",
    "Equity",
    "Transfer",
    "",  # Empty option
]

def classify_gl_account_with_ollama(model_name, description):
    """
    Classify transaction description to a GL Account category using Ollama.
    """
    from backend.ai_model.classify_transaction import classify_with_ollama
    
    system_prompt = "You are a financial accounting assistant. Classify the transaction description into a GL Account category. Respond with ONLY the category name, nothing else."
    
    try:
        response = classify_with_ollama(model_name, description, system_prompt=system_prompt)
        return response.strip()
    except Exception as e:
        st.error(f"Classification error: {e}")
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
                        target_df["GL Account"] = None

                    rows = list(df_source.index)
                    progress = st.progress(0)
                    status = st.empty()
                    results = []

                    for i, idx in enumerate(rows):
                        desc = str(df_source.at[idx, "Description"]) if pd.notnull(df_source.at[idx, "Description"]) else ""
                        if not desc.strip():
                            continue
                        
                        # Check if GL Account is already set and valid - skip if so
                        current_gl = target_df.at[idx, "GL Account"] if pd.notnull(target_df.at[idx, "GL Account"]) else ""
                        if current_gl and current_gl in GL_ACCOUNT_OPTIONS:
                            continue
                        
                        status.text(f"Classifying row {i+1}/{len(rows)} (idx {idx})...")
                        try:
                            cat = classify_gl_account_with_ollama(selected, desc)
                        except Exception as e:
                            cat = ""
                        target_df.at[idx, "GL Account"] = cat
                        results.append({"Index": idx, "Description": desc, "Predicted": cat})
                        progress.progress((i + 1) / len(rows))

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

                    st.success(f"Applied GL Account prediction to {len(results)} row(s).")
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
                    current_gl = st.session_state.edited_df_cache.at[original_idx, "GL Account"] if pd.notnull(st.session_state.edited_df_cache.at[original_idx, "GL Account"]) else ""
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
                    original_gl = st.session_state.edited_df_cache.at[original_idx, "GL Account"] if pd.notnull(st.session_state.edited_df_cache.at[original_idx, "GL Account"]) else ""
                    if new_gl != original_gl:
                        # Update directly in edited_df_cache and reconciliation_results
                        st.session_state.edited_df_cache.at[original_idx, "GL Account"] = new_gl
                        st.session_state.reconciliation_results.at[original_idx, "GL Account"] = new_gl
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
                        options=GST_CATEGORY_OPTIONS,
                        index=GST_CATEGORY_OPTIONS.index(current_category) if current_category in GST_CATEGORY_OPTIONS else 0,
                        key=f"gst_cat_{original_idx}_{st.session_state.page_number}",
                        label_visibility="collapsed"
                    )
                    
                    # Track changes
                    original_from_cache = st.session_state.edited_df_cache.at[original_idx, "GST Category"]
                    if new_category != original_from_cache:
                        st.session_state.pending_changes[original_idx] = new_category
                    elif original_idx in st.session_state.pending_changes:
                        del st.session_state.pending_changes[original_idx]
                
                # Who column
                with cols[12]:
                    st.markdown(f"<div class='table-cell'>{str(row_data.get('Who', ''))}</div>", unsafe_allow_html=True)
            
            # Pagination controls and Submit button
            pag_col1, pag_col2, pag_col3, pag_col4 = st.columns([1, 1, 1, 1])
            
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
                        
                        # Clear pending changes
                        st.session_state.pending_changes = {}
                        
                        st.success(f"✅ Changes submitted! Page {st.session_state.page_number} updated.")
                        st.rerun()

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