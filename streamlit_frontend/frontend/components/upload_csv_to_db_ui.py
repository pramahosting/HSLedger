import json
from urllib import error, request

import pandas as pd
import streamlit as st

from backend.ai_model import classify_category
from backend.reconciliation import classifier
from backend.reconciliation.bank_normalizer import BANK_PRESETS, normalize_transactions


FALLBACK_GL_ACCOUNT = "Unclassified"
FALLBACK_GST_CATEGORY = "Unknown"


def _value_or_none(value):
    if pd.isna(value):
        return None
    return value


def _build_transactions_payload(df: pd.DataFrame) -> list[dict]:
    rows: list[dict] = []
    for _, row in df.iterrows():
        rows.append(
            {
                "date": str(_value_or_none(row.get("date"))) if _value_or_none(row.get("date")) is not None else None,
                "bank": _value_or_none(row.get("bank")),
                "account": _value_or_none(row.get("account")),
                "description": _value_or_none(row.get("description")),
                "debit": float(row.get("debit", 0) or 0),
                "credit": float(row.get("credit", 0) or 0),
                "classification": _value_or_none(row.get("classification")),
                "pair_id": _value_or_none(row.get("pairid")),
                "gl_account": _value_or_none(row.get("gl account")),
                "gst": float(row.get("gst", 0) or 0),
                "gst_category": _value_or_none(row.get("gst category")),
                "who": _value_or_none(row.get("who")),
            }
        )
    return rows


def _enrich_gl_gst_who(df: pd.DataFrame) -> pd.DataFrame:
    enriched = df.copy()

    if "description" not in enriched.columns:
        return enriched

    if "gl account" not in enriched.columns:
        enriched["gl account"] = ""
    if "gst category" not in enriched.columns:
        enriched["gst category"] = "Unknown"
    if "who" not in enriched.columns:
        enriched["who"] = "Other/Unknown"

    desc_series = enriched["description"].fillna("").astype(str)
    enriched["who"] = desc_series.apply(classify_category.extract_who_bank)

    enriched["gl account"] = enriched["gl account"].fillna("").replace("", FALLBACK_GL_ACCOUNT)
    enriched["gst category"] = enriched["gst category"].fillna("").replace("", FALLBACK_GST_CATEGORY)

    return enriched


def _get_logged_in_user_id() -> int | None:
    user = st.session_state.get("user") or {}
    raw_id = user.get("id", user.get("user_id"))
    try:
        return int(raw_id) if raw_id is not None else None
    except (TypeError, ValueError):
        return None


def _save_transactions(api_base_url: str, user_id: int, transactions: list[dict]) -> dict:
    payload = {
        "user_id": user_id,
        "transactions": transactions,
    }
    body = json.dumps(payload).encode("utf-8")

    req = request.Request(
        url=f"{api_base_url.rstrip('/')}/transactions/save",
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


def render() -> None:
    st.markdown("### Upload CSV And Save To DB")

    if "upload_preview_df" not in st.session_state:
        st.session_state.upload_preview_df = None
    if "upload_transactions_payload" not in st.session_state:
        st.session_state.upload_transactions_payload = None

    user_id = _get_logged_in_user_id()
    if user_id is None:
        st.error("No logged-in user ID found. Please log in again.")
        return

    col1, col2 = st.columns(2)
    with col1:
        bank_name = st.selectbox("Bank Name", options=sorted(BANK_PRESETS.keys()), index=0)
        account_number = st.text_input("Account Number", value="ACCT-001")
        st.text_input("User ID", value=str(user_id), disabled=True)
    with col2:
        st.caption("GL Account and GST Category stay as existing values or defaults during upload.")
        uploaded_file = st.file_uploader("Upload CSV", type=["csv"], accept_multiple_files=False)

    process_btn = st.button("Prepare Transactions", use_container_width=True)
    if process_btn:
        if uploaded_file is None:
            st.error("Please upload a CSV file.")
            return
        if not account_number.strip():
            st.error("Please enter an account number.")
            return

        try:
            raw_df = pd.read_csv(uploaded_file)
            normalized = normalize_transactions(raw_df, bank_name, account_number.strip())
            if normalized.empty:
                st.error("No rows were produced after normalization.")
                return

            normalized.columns = normalized.columns.str.strip().str.lower()
            classified = classifier.classify_transactions(normalized, show_progress=False)
            classified.columns = classified.columns.str.strip().str.lower()
            classified = _enrich_gl_gst_who(classified)

            payload_rows = _build_transactions_payload(classified)
            st.session_state.upload_preview_df = classified
            st.session_state.upload_transactions_payload = {
                "api_base_url": "http://127.0.0.1:8000".strip(),
                "user_id": user_id,
                "transactions": payload_rows,
            }
            st.success(f"Prepared {len(payload_rows)} transactions. Review and click Save.")
        except Exception as exc:
            st.error(f"Failed to process CSV: {exc}")
            return

    if st.session_state.upload_preview_df is not None:
        st.markdown("#### Preview")
        st.dataframe(st.session_state.upload_preview_df.head(200), use_container_width=True)

    save_btn = st.button("Save To DB", use_container_width=True, type="primary")
    if save_btn:
        payload_data = st.session_state.upload_transactions_payload
        if not payload_data:
            st.error("Prepare transactions before saving.")
            return

        try:
            result = _save_transactions(
                payload_data["api_base_url"],
                payload_data["user_id"],
                payload_data["transactions"],
            )
            st.success("Transactions saved successfully.")
            st.json(result)
        except Exception as exc:
            st.error(str(exc))