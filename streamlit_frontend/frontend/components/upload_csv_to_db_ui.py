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


def _normalize_gl_account(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    for option in classify_category.CATEGORY_ENUM:
        if option.lower() == text.lower():
            return option

    return ""


def _is_blank(value: str | None) -> bool:
    return not str(value or "").strip()


def _enrich_gl_gst_who(df: pd.DataFrame, selected_model: str | None) -> pd.DataFrame:
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

    # If model is not selected/available, keep WHO enrichment and existing GL/GST values.
    if not selected_model:
        return enriched

    unique_descs = [d for d in pd.unique(desc_series) if str(d).strip()]

    try:
        disk_cache = classify_category.load_disk_cache()
    except Exception:
        disk_cache = {}

    mem_cache: dict[str, dict[str, str]] = {}
    gl_mapping: dict[str, str] = {}
    gst_mapping: dict[str, str] = {}
    gl_errors = 0
    gst_errors = 0

    # Give explicit defaults to blank descriptions so they are not silently skipped.
    for desc in pd.unique(desc_series):
        if _is_blank(desc):
            gl_mapping[desc] = FALLBACK_GL_ACCOUNT
            gst_mapping[desc] = FALLBACK_GST_CATEGORY

    if not unique_descs:
        enriched["gl account"] = desc_series.map(gl_mapping).fillna(enriched["gl account"]).replace("", FALLBACK_GL_ACCOUNT)
        enriched["gst category"] = desc_series.map(gst_mapping).fillna(enriched["gst category"]).replace("", FALLBACK_GST_CATEGORY)
        return enriched

    progress = st.progress(0.0, text="Classifying GL account...")

    # Phase 1: classify GL account for all unique descriptions.
    for i, desc in enumerate(unique_descs, start=1):
        dnorm = classify_category.normalize_desc(desc)
        gl_key = classify_category.cache_key(selected_model, dnorm, classify_category._DEFAULT_TXN_PROMPT)

        gl_label = ""
        if gl_key in mem_cache:
            gl_label = mem_cache[gl_key].get("gl_account", mem_cache[gl_key].get("category", ""))
        elif gl_key in disk_cache:
            gl_label = disk_cache[gl_key].get("gl_account", disk_cache[gl_key].get("category", ""))
            mem_cache[gl_key] = disk_cache[gl_key]

        # Reclassify when cache is missing/blank to avoid silent empty categories.
        if _is_blank(gl_label):
            try:
                gl_label = classify_category.ollama_classify_gl_account_cached(
                    model=selected_model,
                    prompt=f"{classify_category._DEFAULT_TXN_PROMPT}\n{dnorm}",
                    base_url=classify_category.OLLAMA_CHAT_URL_DEFAULT,
                    temperature=0.0,
                    top_p=1.0,
                    cache_version=classify_category.CACHE_VERSION,
                )["category"]
            except Exception:
                gl_errors += 1
                gl_label = FALLBACK_GL_ACCOUNT

            mem_cache[gl_key] = {"gl_account": gl_label, "category": gl_label}
            disk_cache[gl_key] = {"gl_account": gl_label, "category": gl_label}

        gl_mapping[desc] = _normalize_gl_account(gl_label) or FALLBACK_GL_ACCOUNT

        progress.progress(i / max(1, len(unique_descs)), text=f"Classifying GL account... {i}/{len(unique_descs)}")

    # Phase 2: classify GST category using the classified GL account.
    progress.progress(0.0, text="Classifying GST category...")
    for i, desc in enumerate(unique_descs, start=1):
        dnorm = classify_category.normalize_desc(desc)
        gst_key = classify_category.cache_key(
            selected_model,
            f"{dnorm}||{gl_mapping[desc]}",
            classify_category._DEFAULT_GST_PROMPT,
        )
        gst_label = ""
        if gst_key in mem_cache:
            gst_label = mem_cache[gst_key].get("gst_category", FALLBACK_GST_CATEGORY)
        elif gst_key in disk_cache:
            gst_label = disk_cache[gst_key].get("gst_category", FALLBACK_GST_CATEGORY)
            mem_cache[gst_key] = disk_cache[gst_key]

        if _is_blank(gst_label):
            try:
                gst_label = classify_category.ollama_predict_gst_cached(
                    model=selected_model,
                    prompt=(
                        f"{classify_category._DEFAULT_GST_PROMPT}\n"
                        f"Category: {gl_mapping[desc]}\nDescription: {dnorm}"
                    ),
                    base_url=classify_category.OLLAMA_CHAT_URL_DEFAULT,
                    temperature=0.0,
                    top_p=1.0,
                    cache_version=classify_category.CACHE_VERSION,
                )["gst_category"]
            except Exception:
                gst_errors += 1
                gst_label = FALLBACK_GST_CATEGORY

            mem_cache[gst_key] = {"gst_category": gst_label}
            disk_cache[gst_key] = {"gst_category": gst_label}

        gst_mapping[desc] = gst_label or FALLBACK_GST_CATEGORY
        progress.progress(i / max(1, len(unique_descs)), text=f"Classifying GST category... {i}/{len(unique_descs)}")

    classify_category.save_disk_cache(disk_cache)
    progress.empty()

    enriched["gl account"] = desc_series.map(gl_mapping).fillna(enriched["gl account"]).replace("", FALLBACK_GL_ACCOUNT)
    enriched["gst category"] = (
        desc_series.map(gst_mapping)
        .fillna(enriched["gst category"])
        .replace("", FALLBACK_GST_CATEGORY)
    )

    if gl_errors or gst_errors:
        st.warning(
            f"Classification fallback used for {gl_errors} GL and {gst_errors} GST rows. "
            f"They were set to '{FALLBACK_GL_ACCOUNT}'/'{FALLBACK_GST_CATEGORY}'."
        )

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
        use_ai_classifier = st.checkbox("Local Ollama model", value=True)
        selected_model = None
        if use_ai_classifier:
            try:
                models = classify_category.list_ollama_models()
                if models:
                    selected_model = st.selectbox("Model", options=models)
                else:
                    st.warning("No Ollama model found. GL/GST classification will be skipped.")
            except Exception as exc:
                st.warning(f"Ollama unavailable: {exc}. GL/GST classification will be skipped.")
        else:
            st.caption("WHO will still be extracted from description.")
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
            classified = _enrich_gl_gst_who(classified, selected_model)

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