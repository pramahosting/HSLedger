# backend/reconciliation/gst_editor.py
import streamlit as st
import pandas as pd
from backend.reconciliation.gst_calculator import GST_CATEGORY_OPTIONS, calculate_gst_value


def edit_gst_category_inline(df_display: pd.DataFrame) -> pd.DataFrame:
    """
    Adds interactive GST Category editing capability to the dataframe using st.data_editor.
    Updates GST automatically when category changes.
    
    Args:
        df_display: DataFrame to display and edit
        
    Returns:
        Updated DataFrame with modified GST categories and values
    """
    # Initialize session state for tracking the edited dataframe
    if "edited_df" not in st.session_state:
        st.session_state.edited_df = None
    
    # Prepare dataframe for editing
    df_editable = df_display.copy()
    
    # Configure column settings for st.data_editor
    column_config = {
        "GST Category": st.column_config.SelectboxColumn(
            "GST Category",
            help="Select GST category to auto-recalculate GST",
            width="medium",
            options=GST_CATEGORY_OPTIONS,
            required=True,
        ),
        "Date": st.column_config.TextColumn("Date", width="small"),
        "Bank": st.column_config.TextColumn("Bank", width="small"),
        "Account": st.column_config.TextColumn("Account", width="small"),
        "Description": st.column_config.TextColumn("Description", width="large"),
        "Debit": st.column_config.NumberColumn("Debit", format="%.2f", width="small"),
        "Credit": st.column_config.NumberColumn("Credit", format="%.2f", width="small"),
        "GST": st.column_config.NumberColumn("GST", format="%.2f", width="small"),
        "Classification": st.column_config.TextColumn("Classification", width="small"),
        "PairID": st.column_config.TextColumn("PairID", width="small"),
    }
    
    # Use st.data_editor for inline editing
    edited_df = st.data_editor(
        df_editable,
        column_config=column_config,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        disabled=[col for col in df_editable.columns if col not in ["GST Category"]],
        key="gst_editor"
    )
    
    # Check if GST Category changed and recalculate GST
    if edited_df is not None:
        for idx in edited_df.index:
            original_category = df_display.at[idx, "GST Category"]
            new_category = edited_df.at[idx, "GST Category"]
            
            if original_category != new_category:
                debit = edited_df.at[idx, "Debit"]
                credit = edited_df.at[idx, "Credit"]
                
                # Recalculate GST based on new category
                new_gst = calculate_gst_value(debit, credit, new_category)
                edited_df.at[idx, "GST"] = new_gst
    
    return edited_df
