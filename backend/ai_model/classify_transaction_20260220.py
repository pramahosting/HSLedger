import hashlib
import json
import os
import re
import time
from difflib import get_close_matches

import pandas as pd
import streamlit as st
import ollama

_DEFAULT_TXN_PROMPT = (
    "You are a financial assistant. "
    "Classify the transaction description into: Food, Travel, Shopping, Groceries, Income, or Utilities. "
    "Respond with ONLY the category name."
)

_DEFAULT_TXN_OPTIONS = [
    "Food",
    "Travel",
    "Shopping",
    "Groceries",
    "Income",
    "Utilities",
]


def _normalize_description(text: str) -> str:
    """Normalize descriptions so 'similar' strings become the same cache key."""
    s = "" if text is None else str(text)
    s = s.lower().strip()
    # replace standalone numbers with a token (helps with invoice/order id differences)
    s = re.sub(r"\b\d+\b", "<num>", s)
    # keep only letters, digits, and spaces
    s = re.sub(r"[^a-z0-9<> ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _cache_dir() -> str:
    # Works on Windows/Linux; override with env var if you want
    return os.environ.get("CLASSIFIER_CACHE_DIR", ".classifier_cache")


def _cache_file(model_name: str, system_prompt: str) -> str:
    os.makedirs(_cache_dir(), exist_ok=True)
    safe_model = re.sub(r"[^a-zA-Z0-9_.-]+", "_", model_name or "model")
    prompt_hash = hashlib.sha1((system_prompt or "").encode("utf-8")).hexdigest()[:10]
    return os.path.join(_cache_dir(), f"{safe_model}_{prompt_hash}.json")


def _get_state():
    if "_clf_cache" not in st.session_state:
        st.session_state._clf_cache = {}
        st.session_state._clf_meta = {}  # cache_key -> {hits, misses, last_save, new_since_save}
    return st.session_state._clf_cache, st.session_state._clf_meta


def _load_cache_into_memory(model_name: str, system_prompt: str):
    caches, meta = _get_state()
    cache_key = f"{model_name}|{hashlib.sha1((system_prompt or '').encode('utf-8')).hexdigest()}"
    if cache_key in caches:
        return cache_key, caches[cache_key]

    path = _cache_file(model_name, system_prompt)
    data = {}
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
    except Exception:
        data = {}

    caches[cache_key] = data
    meta.setdefault(cache_key, {"hits": 0, "misses": 0, "last_save": 0.0, "new_since_save": 0})
    return cache_key, data


def _save_cache(model_name: str, system_prompt: str, cache_key: str, cache_dict: dict):
    path = _cache_file(model_name, system_prompt)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache_dict, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _clean_label(raw: str, allowed_options=None) -> str:
    label = (raw or "").strip()
    # take only first line and strip quotes
    label = label.splitlines()[0].strip().strip('"').strip("'")

    if allowed_options:
        # exact (case-insensitive)
        for opt in allowed_options:
            if label.lower() == str(opt).lower():
                return str(opt)
        # best-effort close match among allowed
        lower_allowed = [str(o).lower() for o in allowed_options]
        m = get_close_matches(label.lower(), lower_allowed, n=1, cutoff=0.6)
        if m:
            return str(allowed_options[lower_allowed.index(m[0])])

    return label


def classify_with_cache(
    model_name: str,
    description: str,
    system_prompt: str,
    allowed_options=None,
    enable_fuzzy_cache: bool = True,
    fuzzy_cutoff: float = 0.92,
):
    """Fast classifier with:
    - exact cache on normalized description
    - optional fuzzy cache hit for 'similar' descriptions
    - persistent json cache per (model + prompt)

    Returns: (label, from_cache)
    """

    cache_key, cache_dict = _load_cache_into_memory(model_name, system_prompt)
    caches, meta = _get_state()

    key = _normalize_description(description)
    if not key:
        return "", True

    # exact hit
    if key in cache_dict:
        meta[cache_key]["hits"] += 1
        return cache_dict[key], True

    # fuzzy hit (only if cache isn't huge)
    if enable_fuzzy_cache and 0 < len(cache_dict) <= 5000:
        match = get_close_matches(key, cache_dict.keys(), n=1, cutoff=fuzzy_cutoff)
        if match:
            meta[cache_key]["hits"] += 1
            val = cache_dict[match[0]]
            # save alias so next time it's O(1)
            cache_dict[key] = val
            meta[cache_key]["new_since_save"] += 1
            _maybe_flush_cache(model_name, system_prompt, cache_key, cache_dict)
            return val, True

    # miss -> call model
    meta[cache_key]["misses"] += 1
    response = ollama.generate(
        model=model_name,
        system=system_prompt,
        prompt=f"Transaction: {description}",
        options={"temperature": 0},
    )
    label = _clean_label(response.get("response", ""), allowed_options=allowed_options)

    cache_dict[key] = label
    meta[cache_key]["new_since_save"] += 1
    _maybe_flush_cache(model_name, system_prompt, cache_key, cache_dict)
    return label, False


def _maybe_flush_cache(model_name: str, system_prompt: str, cache_key: str, cache_dict: dict):
    """Write-through, but throttled so disk IO doesn't slow you down."""
    _caches, meta = _get_state()
    now = time.time()
    last = meta[cache_key].get("last_save", 0.0)
    new_count = meta[cache_key].get("new_since_save", 0)

    # save if many new items OR it's been a couple seconds
    if new_count >= 25 or (now - last) >= 2.0:
        try:
            _save_cache(model_name, system_prompt, cache_key, cache_dict)
            meta[cache_key]["last_save"] = now
            meta[cache_key]["new_since_save"] = 0
        except Exception:
            # if saving fails, don't break classification
            pass


def get_cache_stats(model_name: str, system_prompt: str) -> dict:
    cache_key, cache_dict = _load_cache_into_memory(model_name, system_prompt)
    _caches, meta = _get_state()
    m = meta.get(cache_key, {})
    return {
        "entries": len(cache_dict),
        "hits": int(m.get("hits", 0)),
        "misses": int(m.get("misses", 0)),
    }


# -----------------------------------------------------------------
# Backward-compatible API used by your existing UIs
# -----------------------------------------------------------------

def classify_with_ollama(model_name, description, system_prompt=None, allowed_options=None):
    """Keeps the same function name/signature, but now it is cached and strict."""
    if system_prompt is None:
        system_prompt = _DEFAULT_TXN_PROMPT
    if allowed_options is None and system_prompt == _DEFAULT_TXN_PROMPT:
        allowed_options = _DEFAULT_TXN_OPTIONS

    try:
        label, _from_cache = classify_with_cache(
            model_name=model_name,
            description=description,
            system_prompt=system_prompt,
            allowed_options=allowed_options,
            enable_fuzzy_cache=True,
            fuzzy_cutoff=0.92,
        )
        return (label or "").strip()
    except Exception as e:
        return f"Error: {e}"


# -----------------
# Streamlit UI
# -----------------

def render():
    st.set_page_config(page_title="Bank Transaction Classifier", layout="wide")
    st.title("Bank Transaction Classifier")
    st.write("Upload Bank Transaction CSV to automatically categorize spending.")

    with st.sidebar:
        st.header("Settings")
        try:
            models = [m.model for m in ollama.list().models]
            selected_model = st.selectbox("Select your Fine-Tuned Model", options=models)
        except Exception:
            st.error("Ollama not detected. Ensure Ollama is running locally.")
            selected_model = None

        st.caption("Speed: this UI deduplicates descriptions and caches results (exact + similar).")

    uploaded_file = st.file_uploader("Upload Your CSV File", type=["csv"])

    if uploaded_file and selected_model:
        df = pd.read_csv(uploaded_file)

        if "Description" not in df.columns:
            st.error("The uploaded CSV is missing the 'Description' column.")
            return

        st.success("CSV Loaded Successfully!")
        st.dataframe(df.head(5))

        if st.button("🚀 Run Classification"):
            start_time = time.time()

            # DEDUPE: classify only unique descriptions
            desc_series = df["Description"].fillna("").astype(str)
            unique_descs = [d for d in pd.unique(desc_series) if str(d).strip()]

            progress_bar = st.progress(0)
            status_text = st.empty()

            mapping = {}
            for i, desc in enumerate(unique_descs):
                status_text.text(f"Classifying unique {i+1}/{len(unique_descs)}")
                mapping[desc] = classify_with_ollama(selected_model, desc)
                progress_bar.progress((i + 1) / max(1, len(unique_descs)))

            df["Predicted_Category"] = desc_series.map(mapping).fillna("")

            duration = time.time() - start_time
            stats = get_cache_stats(selected_model, _DEFAULT_TXN_PROMPT)
            st.success(
                f"Done! {len(df)} rows, {len(unique_descs)} unique. "
                f"Time: {duration:.2f}s | Cache: {stats['entries']} entries, {stats['hits']} hits, {stats['misses']} misses."
            )

            st.dataframe(df)

            csv_data = df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="Download Categorized CSV",
                data=csv_data,
                file_name="categorized_transactions.csv",
                mime="text/csv",
            )


if __name__ == "__main__":
    render()
