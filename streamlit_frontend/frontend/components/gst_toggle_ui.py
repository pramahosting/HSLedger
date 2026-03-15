import streamlit as st
import pandas as pd

def gst_toggle_ui(df: pd.DataFrame) -> pd.DataFrame:
    """Show interactive GST checkbox and compute GST dynamically."""
    if "GST Applicable" not in df.columns:
        df["GST Applicable"] = True

    # Auto-disable GST for internal or interest-type descriptions
    no_gst_keywords = ["interest", "fee", "bank charge", "refund", "reversal", "tax"]
    df["GST Applicable"] = df.apply(
        lambda r: False
        if r["Classification"] == "Internal"
        or any(k in str(r["Description"]).lower() for k in no_gst_keywords)
        else True,
        axis=1,
    )

    # Editable checkbox column in Streamlit
    edited_df = st.data_editor(
        df,
        column_config={
            "GST Applicable": st.column_config.CheckboxColumn(
                "GST Applicable",
                help="Untick if GST should not apply (e.g., interest or internal transfers)"
            )
        },
        hide_index=True,
        use_container_width=True,
        key="gst_table",
    )

    # --- Recalculate GST dynamically ---
    def calc_gst(row):
        if not row["GST Applicable"]:
            return 0.00
        if row["Classification"] == "External Outgoing":
            return round(row["Debit"] * 10 / 110, 2)
        elif row["Classification"] == "External Incoming":
            return round(row["Credit"] * 10 / 110, 2)
        else:
            return 0.00

    edited_df["GST"] = edited_df.apply(calc_gst, axis=1)
    return edited_df
