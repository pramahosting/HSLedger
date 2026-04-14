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


def determine_gst_category(description: str, classification: str = "") -> str:
    """
    Determine GST category based on transaction classification and description keywords.

    Classification takes priority for the default assignment:
      🟢Internal  → BAS Excluded
      🔵Incoming  → GST on Sale
      🟡Outgoing  → GST on Purchase

    Description keywords can override the default when a specific exempt / special
    category is detected (e.g. "interest", "grant", "gst free").

    Args:
        description: Transaction description
        classification: Transaction classification string (e.g. "🔵Incoming")

    Returns:
        Matched GST category string
    """
    description_lower = str(description).lower()

    # Check exempt / special keyword overrides first (apply regardless of classification)
    exempt_categories = {
        "GST Free Sale",
        "Input Taxed Sales",
        "BAS Excluded",
        "Interest Income",
        "Other Exempt Income",
    }
    for category in exempt_categories:
        keywords = GST_CATEGORY_KEYWORDS.get(category, [])
        if any(keyword.lower() in description_lower for keyword in keywords):
            return category

    # Use classification to derive the default GST category
    classification_str = str(classification).strip()
    if "Internal" in classification_str:
        return "BAS Excluded"
    if "Incoming" in classification_str:
        return "GST on Sale"
    if "Outgoing" in classification_str:
        return "GST on Purchase"

    # Fall back to full keyword scan if classification is absent
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
        classification = row.get("Classification", row.get("classification", ""))

        # Determine category using classification + description keywords
        gst_category = determine_gst_category(description, classification)

        # Calculate GST value
        gst_value = calculate_gst_value(debit, credit, gst_category)

        return pd.Series([gst_value, gst_category])
    
    df[["GST", "GST Category"]] = df.apply(gst_row, axis=1)
    return df
