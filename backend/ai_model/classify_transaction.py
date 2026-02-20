import json
import hashlib
import time
import re
from pathlib import Path
from typing import Callable, Dict, Optional

import pandas as pd
import ollama
import requests
import streamlit as st

OLLAMA_CHAT_URL_DEFAULT = "http://localhost:11434/api/chat"
CACHE_FILE = Path("ollama_cache.json")
CACHE_VERSION = "v2"
_DEFAULT_TXN_PROMPT = "Classify this transaction description:"
_DEFAULT_GST_PROMPT = "Given the GL account and transaction description, return the GST category label:"

CATEGORY_ENUM = [
    "Revenue",
    "Direct Costs",
    "Expense",
    "Inventory",
    "Fixed Asset",
    "GST",
    "Equity",
    "Transfer",
    "Liability",
]

GST_ENUM = [
    "GST on Expenses",
    "GST on Capital",
    "GST on Income",
    "GST Free Expenses",
    "GST Free Income",
    "BAS Excluded",
]

SYSTEM_MSG = (
    "You are a strict classifier for bank transactions. "
    "Return ONLY the GL account label as plain text. "
    "No explanations, no extra keys, no markdown. "
    "Rules: common merchant purchases (shops, restaurants, fuel, convenience) -> "
    "gl_account=Expense. "
    "Use Revenue only for income/credits/sales. "
    "Use Transfer only for internal transfers between accounts. "
    "Use GST only for tax payment transactions."
)

GST_SYSTEM_MSG = (
    "You are a strict GST classifier for bank transactions. "
    "Return ONLY the GST category label as plain text. "
    "No explanations, no extra keys, no markdown."
)


def normalize_desc(s: str) -> str:
    return " ".join(str(s or "").split()).strip().lower()


def cache_key(model: str, desc_normed: str, prompt_prefix: str) -> str:
    raw = f"{CACHE_VERSION}||{model}||{prompt_prefix}||{desc_normed}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def load_disk_cache() -> Dict[str, Dict[str, str]]:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_disk_cache(cache: Dict[str, Dict[str, str]]) -> None:
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def list_ollama_models() -> list[str]:
    return [m.model for m in ollama.list().models]


def ollama_classify(
    model: str,
    prompt: str,
    base_url: str,
    temperature: float,
    top_p: float,
) -> Dict[str, str]:
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_MSG},
            {"role": "user", "content": prompt},
        ],
        "options": {
            "temperature": temperature,
            "top_p": top_p,
        },
    }

    r = requests.post(base_url, json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()

    content = data.get("message", {}).get("content", "")
    text = str(content).strip().strip('"').strip("'")
    if text in CATEGORY_ENUM:
        return {"gl_account": text, "category": text, "gst_category": ""}

    match = re.search(r"\b(?:" + "|".join(re.escape(c) for c in CATEGORY_ENUM) + r")\b", text, re.IGNORECASE)
    if match:
        normalized = next(c for c in CATEGORY_ENUM if c.lower() == match.group(0).lower())
        return {"gl_account": normalized, "category": normalized, "gst_category": ""}

    raise ValueError(f"Model did not return a known category: {content}")


def ollama_predict_gst(
    model: str,
    prompt: str,
    base_url: str,
    temperature: float,
    top_p: float,
) -> Dict[str, str]:
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": GST_SYSTEM_MSG},
            {"role": "user", "content": prompt},
        ],
        "options": {
            "temperature": temperature,
            "top_p": top_p,
        },
    }

    r = requests.post(base_url, json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()

    content = data.get("message", {}).get("content", "")
    text = str(content).strip().strip('"').strip("'")
    if text in GST_ENUM:
        return {"gst_category": text}

    match = re.search(r"\b(?:" + "|".join(re.escape(c) for c in GST_ENUM) + r")\b", text, re.IGNORECASE)
    if match:
        normalized = next(c for c in GST_ENUM if c.lower() == match.group(0).lower())
        return {"gst_category": normalized}

    return {"gst_category": "Unknown"}


def classify_with_ollama(
    model_name: str,
    description: str,
    system_prompt: Optional[str] = None,
    allowed_options=None,
):
    try:
        if system_prompt and system_prompt != _DEFAULT_TXN_PROMPT:
            response = ollama.generate(
                model=model_name,
                system=system_prompt,
                prompt=f"Transaction: {description}",
                options={"temperature": 0},
            )
            return (response.get("response", "") or "").strip()

        prompt = f"{_DEFAULT_TXN_PROMPT}\n{normalize_desc(description)}"
        result = ollama_classify(
            model=model_name,
            prompt=prompt,
            base_url=OLLAMA_CHAT_URL_DEFAULT,
            temperature=0.0,
            top_p=1.0,
        )
        return result.get("gl_account", result.get("category", ""))
    except Exception as e:
        return f"Error: {e}"


def classify_dataframe(
    df: pd.DataFrame,
    model: str,
    base_url: str = OLLAMA_CHAT_URL_DEFAULT,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> tuple[pd.DataFrame, Dict[str, float]]:
    if "Description" not in df.columns:
        raise ValueError("The input data is missing the 'Description' column.")

    start_time = time.time()

    desc_series = df["Description"].fillna("").astype(str)
    unique_descs = [d for d in pd.unique(desc_series) if str(d).strip()]

    gl_mapping: Dict[str, str] = {}
    gst_mapping: Dict[str, str] = {}
    cache_hits = 0
    cache_misses = 0
    disk_cache = load_disk_cache()
    mem_cache: Dict[str, Dict[str, str]] = {}

    total_unique = max(1, len(unique_descs))
    for index, desc in enumerate(unique_descs, start=1):
        dnorm = normalize_desc(desc)
        k = cache_key(model, dnorm, _DEFAULT_TXN_PROMPT)

        if k in mem_cache:
            gl_mapping[desc] = mem_cache[k].get("gl_account", mem_cache[k].get("category", ""))
            cache_hits += 1
        elif k in disk_cache:
            gl_mapping[desc] = disk_cache[k].get("gl_account", disk_cache[k].get("category", ""))
            mem_cache[k] = disk_cache[k]
            cache_hits += 1
        else:
            cache_misses += 1
            gl_mapping[desc] = ollama_classify(
                model=model,
                prompt=f"{_DEFAULT_TXN_PROMPT}\n{dnorm}",
                base_url=base_url,
                temperature=0.0,
                top_p=1.0,
            )["gl_account"]
            mem_cache[k] = {"gl_account": gl_mapping[desc], "category": gl_mapping[desc]}
            disk_cache[k] = {"gl_account": gl_mapping[desc], "category": gl_mapping[desc]}

        gst_k = cache_key(model, f"{dnorm}||{gl_mapping[desc]}", _DEFAULT_GST_PROMPT)
        if gst_k in mem_cache:
            gst_mapping[desc] = mem_cache[gst_k].get("gst_category", "")
            cache_hits += 1
        elif gst_k in disk_cache:
            gst_mapping[desc] = disk_cache[gst_k].get("gst_category", "")
            mem_cache[gst_k] = disk_cache[gst_k]
            cache_hits += 1
        else:
            cache_misses += 1
            gst_mapping[desc] = ollama_predict_gst(
                model=model,
                prompt=f"{_DEFAULT_GST_PROMPT}\nGL Account: {gl_mapping[desc]}\nDescription: {dnorm}",
                base_url=base_url,
                temperature=0.0,
                top_p=1.0,
            )["gst_category"]
            mem_cache[gst_k] = {"gst_category": gst_mapping[desc]}
            disk_cache[gst_k] = {"gst_category": gst_mapping[desc]}

        if progress_callback is not None:
            progress_callback(index, total_unique)

    save_disk_cache(disk_cache)

    out_df = df.copy()
    out_df["Predicted_GL_Account"] = desc_series.map(gl_mapping).fillna("")
    out_df["Predicted_Category"] = out_df["Predicted_GL_Account"]
    out_df["Predicted_GST_Category"] = desc_series.map(gst_mapping).fillna("")

    duration = time.time() - start_time
    stats = {
        "rows": float(len(out_df)),
        "unique": float(len(unique_descs)),
        "duration": duration,
        "cache_entries": float(len(disk_cache)),
        "cache_hits": float(cache_hits),
        "cache_misses": float(cache_misses),
        "unknown_gst": float((out_df["Predicted_GST_Category"] == "Unknown").sum()),
    }
    return out_df, stats


def render():
    st.set_page_config(page_title="Bank Transaction Classifier", layout="wide")
    st.title("Bank Transaction Classifier")
    st.write("Upload Bank Transaction CSV to automatically categorize spending.")

    with st.sidebar:
        st.header("Settings")
        try:
            models = list_ollama_models()
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
            progress = st.progress(0)
            status = st.empty()

            try:
                def _on_progress(current: int, total: int) -> None:
                    status.text(f"Classifying unique {current}/{total}")
                    progress.progress(current / max(1, total))

                result_df, stats = classify_dataframe(
                    df,
                    selected_model,
                    base_url=OLLAMA_CHAT_URL_DEFAULT,
                    progress_callback=_on_progress,
                )

                st.success(
                    f"Done! {int(stats['rows'])} rows, {int(stats['unique'])} unique. "
                    f"Time: {stats['duration']:.2f}s | Cache: {int(stats['cache_entries'])} entries, "
                    f"{int(stats['cache_hits'])} hits, {int(stats['cache_misses'])} misses."
                )

                def highlight_unknown_gst(row):
                    if row.get("Predicted_GST_Category") == "Unknown":
                        return ["border: 2px solid red"] * len(row)
                    return [""] * len(row)

                st.dataframe(result_df.style.apply(highlight_unknown_gst, axis=1))

                csv_data = result_df.to_csv(index=False).encode("utf-8")
                st.download_button(
                    label="Download Categorized CSV",
                    data=csv_data,
                    file_name="categorized_transactions.csv",
                    mime="text/csv",
                )
            except Exception as ex:
                st.error(f"Classification failed: {ex}")


if __name__ == "__main__":
    render()
