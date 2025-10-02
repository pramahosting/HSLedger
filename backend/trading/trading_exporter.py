import io
import pandas as pd

def export_report_trading(trades_df, gains_df, tax_df, monthly_summary_df=None):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        if tax_df is not None and not tax_df.empty:
            tax_df.to_excel(writer, sheet_name="Tax Summary", index=False)
        if gains_df is not None and not gains_df.empty:
            gains_df.to_excel(writer, sheet_name="Capital Gains (realized lots)", index=False)
        if trades_df is not None and not trades_df.empty:
            trades_df.to_excel(writer, sheet_name="Trades", index=False)
        if monthly_summary_df is not None and not monthly_summary_df.empty:
            monthly_summary_df.to_excel(writer, sheet_name="Monthly Summary", index=False)
    return output.getvalue()
