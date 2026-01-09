# backend/reconciliation/gst_calculator.py
import pandas as pd

# Australian GST rate
GST_RATE = 0.10

# Mapping of description keywords to GST categories
GST_CATEGORY_KEYWORDS = {
    "GST on Sale": ["sale", "invoice", "product"],
    "GST Free Sale": ["gst free", "exempt"],
    "GST on Purchase": ["purchase", "supplier"],
    "Input Taxed Sales": ["input taxed"],
    "BAS Excluded": ["bas excluded"],
    "Interest Income": ["interest", "bank interest"],
    "Other Exempt Income": ["grant", "donation", "compensation"]
}

# Available GST categories for dropdowns
GST_CATEGORY_OPTIONS = [
    "GST on Sale",
    "GST Free Sale",
    "GST on Purchase",
    "Input Taxed Sales",
    "BAS Excluded",
    "Interest Income",
    "Other Exempt Income",
    "Unknown"
]


def calculate_gst_value(debit: float, credit: float, gst_category: str) -> float:
    """
    Calculate GST value based on transaction amounts and category.
    
    Args:
        debit: Debit amount
        credit: Credit amount
        gst_category: GST category string
        
    Returns:
        Calculated GST value
    """
    # Convert to float and handle None/NaN values
    debit = float(debit) if pd.notnull(debit) else 0.0
    credit = float(credit) if pd.notnull(credit) else 0.0
    
    if gst_category == "GST on Sale" and credit > 0:
        return round(credit * GST_RATE / (1 + GST_RATE), 2)
    elif gst_category == "GST on Purchase" and debit > 0:
        return round(debit * GST_RATE / (1 + GST_RATE), 2)
    elif gst_category == "Unknown":
        # Fallback standard GST calculation
        if debit > 0:
            return round(debit * GST_RATE / (1 + GST_RATE), 2)
        elif credit > 0:
            return round(credit * GST_RATE / (1 + GST_RATE), 2)
    
    return 0.0


def determine_gst_category(description: str) -> str:
    """
    Determine GST category based on description keywords.
    
    Args:
        description: Transaction description
        
    Returns:
        Matched GST category or "Unknown"
    """
    description_lower = str(description).lower()
    
    for category, keywords in GST_CATEGORY_KEYWORDS.items():
        if any(keyword.lower() in description_lower for keyword in keywords):
            return category
    
    return "Unknown"


def calculate_gst(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds GST column and GST Category column for transactions.
    Considers debit/credit amounts and description to determine GST.
    
    Args:
        df: DataFrame with transaction data
        
    Returns:
        DataFrame with GST and GST Category columns added
    """
    def gst_row(row):
        description = row.get("Description", row.get("description", ""))
        debit = row.get("Debit", row.get("debit", 0))
        credit = row.get("Credit", row.get("credit", 0))
        
        # Determine category
        gst_category = determine_gst_category(description)
        
        # Calculate GST value
        gst_value = calculate_gst_value(debit, credit, gst_category)
        
        return pd.Series([gst_value, gst_category])
    
    df[["GST", "GST Category"]] = df.apply(gst_row, axis=1)
    return df
