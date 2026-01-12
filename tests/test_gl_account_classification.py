import os
import pandas as pd
from backend.ai_model.classify_transaction import (
    classify_gl_account_keyword_fallback,
    classify_gl_account,
)
from backend.reconciliation.classifier import classify_transactions


def test_keyword_fallback_known():
    res = classify_gl_account_keyword_fallback("Bank fees and charges from ANZ")
    assert res == "Bank Fees"


def test_keyword_fallback_unknown():
    res = classify_gl_account_keyword_fallback("Completely unfamiliar merchant xyz123")
    assert res is None


def test_classify_transactions_sets_gl_account():
    # Minimal DataFrame using expected lowercase columns in classifier pipeline
    df = pd.DataFrame([
        {
            "date": "2026-01-01",
            "description": "Bank fees and charges",
            "debit": 10.0,
            "credit": 0.0,
            "bank": "ANZ",
            "account": "ACC1",
        }
    ])

    out = classify_transactions(df.copy(), show_progress=False, use_classifier=False)
    assert "GL Account" in out.columns
    assert out.iloc[0]["GL Account"] == "Bank Fees"
