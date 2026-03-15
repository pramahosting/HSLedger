# backend/utils/file_utils.py
import pandas as pd
from typing import Union
from io import StringIO, BytesIO
from backend.utils.logger import logger

def load_csv(file) -> pd.DataFrame:
    """
    Accepts file path, file-like object, or Streamlit UploadedFile.
    Returns a pandas DataFrame.
    """
    try:
        if hasattr(file, "read"):
            # file-like (Streamlit UploadedFile)
            content = file.read()
            # try decode bytes
            if isinstance(content, (bytes, bytearray)):
                s = content.decode("utf-8", errors="replace")
            else:
                s = str(content)
            return pd.read_csv(StringIO(s))
        else:
            # file path
            return pd.read_csv(file)
    except Exception as e:
        logger.error("Failed to load CSV: %s", e)
        raise

def validate_file(df) -> bool:
    """
    Very basic validation: must have a date and amount column. 
    More checks can be added.
    """
    cols = [c.lower() for c in df.columns]
    has_date = any("date" in c for c in cols)
    has_amount = any("amount" in c or "credit" in c or "debit" in c for c in cols)
    return has_date and has_amount
