# app.py
# Streamlit: Upload CSV -> classify each row using a description column -> outputs:
#   - category
#   - gst_category
# Uses Ollama /api/chat with JSON Schema (hard constraint) + FAST caching:
#   1) classify ONLY unique descriptions per run
#   2) persistent disk cache across runs (ollama_cache.json)

import json
import hashlib
import time
import re
from pathlib import Path
from typing import Dict

import pandas as pd
import ollama
import requests
import streamlit as st

OLLAMA_CHAT_URL_DEFAULT = "http://localhost:11434/api/chat"
CACHE_FILE = Path("ollama_cache.json")
CACHE_VERSION = "v4"
WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_TXN_PROMPT = "Classify this transaction description:"
_DEFAULT_GST_PROMPT = "Given the category and transaction description, return the GST category label:"

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
    ""
]

GST_ENUM = [
    "GST on Expenses",
    "GST on Capital",
    "GST on Income",
    "GST Free Expenses",
    "GST Free Income",
    "BAS Excluded",
]

WHO_BANK_PATTERNS = [
    ("ANZ", [r"\banz\b", r"\baustralia and new zealand bank\b"]),
    ("CBA", [r"\bcba\b", r"\bcommbank\b", r"\bcommonwealth bank\b", r"\bcommonwealthbk\b"]),
    ("NAB", [r"\bnab\b", r"\bnational australia bank\b"]),
    ("Westpac", [r"\bwestpac\b"]),
    ("St.George", [r"\bst\.?\s*george\b", r"\bstg\b"]),
    ("BankSA", [r"\bbanksa\b", r"\bbank sa\b"]),
    ("Bank of Melbourne", [r"\bbank of melbourne\b", r"\bbom\b"]),
    ("Macquarie", [r"\bmacquarie\b"]),
    ("ING", [r"\bing\b", r"\bing direct\b"]),
    ("Bendigo Bank", [r"\bbendigo\b", r"\bbendigo bank\b"]),
    ("Suncorp", [r"\bsuncorp\b"]),
    ("BOQ", [r"\bboq\b", r"\bbank of queensland\b"]),
    ("ME Bank", [r"\bme bank\b", r"\bmebank\b"]),
    ("UP", [r"\bup bank\b", r"\bup\b"]),
    ("ubank", [r"\bubank\b", r"\bu bank\b"]),
    ("AMP", [r"\bamp\b", r"\bamp bank\b"]),
    ("HSBC", [r"\bhsbc\b"]),
    ("Citi", [r"\bciti\b", r"\bcitibank\b"]),
    ("Other/Unknown", []),
]

GL_SYSTEM_MSG = (
    "You are a strict classifier for bank transactions. "
    "Return ONLY the category label as plain text. "
    "No explanations, no extra keys, no markdown. "
    "Rules: common merchant purchases (shops, restaurants, fuel, convenience) -> "
    "category=Expense. "
    "Use Revenue only for income/credits/sales. "
    "Use Transfer only for internal transfers between accounts. "
    "Use GST only for tax payment transactions."
)

GST_SYSTEM_MSG = (
    "You are a strict Australian GST classifier for bank transactions. "
    "Return exactly ONE label as plain text, and nothing else. "
    "Allowed labels: GST on Expenses, GST on Capital, GST on Income, GST Free Expenses, GST Free Income, BAS Excluded. "
    "Never output JSON, punctuation, explanation, or markdown. "
    "Use both inputs: transaction category and description. Category is the primary signal; description is secondary for refinement. "
    "Decision rules (in priority order): "
    "1) Internal transfers, owner drawings/contributions, wages/salary/payroll, loan repayments, bank charges/interest, tax payments, and purely financial movements -> BAS Excluded. "
    "2) Sales/revenue/income/credit receipts for taxable supply -> GST on Income. "
    "3) Sales/income explicitly GST-free (e.g., GST-free export/medical/education/rent where indicated) -> GST Free Income. "
    "4) Asset/capital purchases (equipment, vehicle, fitout, hardware, long-term assets) -> GST on Capital. "
    "5) Operating/business expenses and merchant purchases (fuel, food, office, software, subscriptions, utilities, repairs, travel) -> GST on Expenses. "
    "6) Expenses explicitly GST-free -> GST Free Expenses. "
    "If uncertain, prefer: Expense -> GST on Expenses, Revenue -> GST on Income, Transfer/Equity/Liability/financial-only movement -> BAS Excluded. "
    "Output only the final label."
)

# GST_SYSTEM_MSG = (
#     "You are a strict GST classifier for bank transactions. "
#     "Return ONLY the GST category label as plain text. "
#     "No explanations, no extra keys, no markdown."
# )

# -------------------------
# Helpers: caching
# -------------------------
def normalize_desc(s: str) -> str:
    return " ".join(str(s or "").split()).strip().lower()


def extract_who_bank(description: str) -> str:
    text = normalize_desc(description)
    if not text:
        return "Other/Unknown"

    for bank_name, patterns in WHO_BANK_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return bank_name

    return "Other/Unknown"

def cache_key(model: str, desc_normed: str, prompt_prefix: str) -> str:
    # Include model + prompt prefixes + system prompt fingerprints so cache updates
    # automatically when classifier instructions are modified.
    gl_sig = hashlib.sha1(GL_SYSTEM_MSG.encode("utf-8")).hexdigest()[:10]
    gst_sig = hashlib.sha1(GST_SYSTEM_MSG.encode("utf-8")).hexdigest()[:10]
    raw = f"{CACHE_VERSION}||{model}||{prompt_prefix}||{gl_sig}||{gst_sig}||{desc_normed}"
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

@st.cache_data(show_spinner=False)
def list_ollama_models() -> list[str]:
    return [m.model for m in ollama.list().models]

# -------------------------
# Ollama call (schema-locked)
# -------------------------
def ollama_classify_gl_account(
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
            {"role": "system", "content": GL_SYSTEM_MSG},
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
        return {"category": text, "gst_category": ""}

    match = re.search(r"\b(?:" + "|".join(re.escape(c) for c in CATEGORY_ENUM) + r")\b", text, re.IGNORECASE)
    if match:
        normalized = next(c for c in CATEGORY_ENUM if c.lower() == match.group(0).lower())
        return {"category": normalized, "gst_category": ""}

    raise ValueError(f"Model did not return a known category: {content}")

def ollama_predict_gst(
    model: str,
    prompt: str,
    base_url: str,
    temperature: float,
    top_p: float,
) -> Dict[str, str]:
    category_match = re.search(r"Category\s*:\s*(.+)", prompt, re.IGNORECASE)
    category_text = category_match.group(1).strip() if category_match else ""
    category_text = category_text.splitlines()[0].strip() if category_text else ""
    normalized_category = category_text.lower()

    if normalized_category in {"transfer", "equity", "liability"}:
        return {"gst_category": "BAS Excluded"}

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

    if normalized_category in {"expense", "direct costs"}:
        return {"gst_category": "GST on Expenses"}
    if normalized_category == "fixed asset":
        return {"gst_category": "GST on Capital"}
    if normalized_category == "revenue":
        return {"gst_category": "GST on Income"}
    if normalized_category in {"gst", "transfer", "equity", "liability"}:
        return {"gst_category": "BAS Excluded"}

    return {"gst_category": "Unknown"}

# Optional: cache the function at Streamlit level too (helps reruns)
@st.cache_data(show_spinner=False)
def ollama_classify_gl_account_cached(
    model: str,
    prompt: str,
    base_url: str,
    temperature: float,
    top_p: float,
    cache_version: str = CACHE_VERSION,
) -> Dict[str, str]:
    return ollama_classify_gl_account(model, prompt, base_url, temperature, top_p)

@st.cache_data(show_spinner=False)
def ollama_predict_gst_cached(
    model: str,
    prompt: str,
    base_url: str,
    temperature: float,
    top_p: float,
    cache_version: str = CACHE_VERSION,
) -> Dict[str, str]:
    return ollama_predict_gst(model, prompt, base_url, temperature, top_p)

# -------------------------
# UI
# -------------------------
def main():
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
            start_time = time.time()

            # DEDUPE: classify only unique descriptions
            desc_series = df["Description"].fillna("").astype(str)
            unique_descs = [d for d in pd.unique(desc_series) if str(d).strip()]

            progress_bar = st.progress(0)
            status_text = st.empty()

            mapping: Dict[str, str] = {}
            gst_mapping: Dict[str, str] = {}
            cache_hits = 0
            cache_misses = 0
            disk_cache = load_disk_cache()
            mem_cache: Dict[str, Dict[str, str]] = {}

            for i, desc in enumerate(unique_descs):
                status_text.text(f"Classifying unique {i+1}/{len(unique_descs)}")
                dnorm = normalize_desc(desc)
                k = cache_key(selected_model, dnorm, _DEFAULT_TXN_PROMPT)
                gst_k = cache_key(selected_model, f"{dnorm}||{mapping.get(desc, '')}", _DEFAULT_GST_PROMPT)

                if k in mem_cache:
                    mapping[desc] = mem_cache[k].get("gl_account", mem_cache[k].get("category", ""))
                    cache_hits += 1
                elif k in disk_cache:
                    mapping[desc] = disk_cache[k].get("gl_account", disk_cache[k].get("category", ""))
                    mem_cache[k] = disk_cache[k]
                    cache_hits += 1
                else:
                    cache_misses += 1
                    mapping[desc] = ollama_classify_gl_account_cached(
                        model=selected_model,
                        prompt=f"{_DEFAULT_TXN_PROMPT}\n{dnorm}",
                        base_url=OLLAMA_CHAT_URL_DEFAULT,
                        temperature=0.0,
                        top_p=1.0,
                        cache_version=CACHE_VERSION,
                    )["gl_account"]
                    mem_cache[k] = {"gl_account": mapping[desc]}
                    disk_cache[k] = {"gl_account": mapping[desc]}

                gst_k = cache_key(selected_model, f"{dnorm}||{mapping[desc]}", _DEFAULT_GST_PROMPT)
                if gst_k in mem_cache:
                    gst_mapping[desc] = mem_cache[gst_k].get("gst_category", "")
                    cache_hits += 1
                elif gst_k in disk_cache:
                    gst_mapping[desc] = disk_cache[gst_k].get("gst_category", "")
                    mem_cache[gst_k] = disk_cache[gst_k]
                    cache_hits += 1
                else:
                    cache_misses += 1
                    gst_mapping[desc] = ollama_predict_gst_cached(
                        model=selected_model,
                        prompt=f"{_DEFAULT_GST_PROMPT}\nCategory: {mapping[desc]}\nDescription: {dnorm}",
                        base_url=OLLAMA_CHAT_URL_DEFAULT,
                        temperature=0.0,
                        top_p=1.0,
                        cache_version=CACHE_VERSION,
                    )["gst_category"]
                    mem_cache[gst_k] = {"gst_category": gst_mapping[desc]}
                    disk_cache[gst_k] = {"gst_category": gst_mapping[desc]}

                progress_bar.progress((i + 1) / max(1, len(unique_descs)))

            save_disk_cache(disk_cache)

            df["Predicted_Category"] = desc_series.map(mapping).fillna("")
            df["Predicted_GST_Category"] = desc_series.map(gst_mapping).fillna("")

            duration = time.time() - start_time
            st.success(
                f"Done! {len(df)} rows, {len(unique_descs)} unique. "
                f"Time: {duration:.2f}s | Cache: {len(disk_cache)} entries, {cache_hits} hits, {cache_misses} misses."
            )

            def highlight_unknown_gst(row):
                if row.get("Predicted_GST_Category") == "Unknown":
                    return ["border: 2px solid red"] * len(row)
                return [""] * len(row)

            st.dataframe(df.style.apply(highlight_unknown_gst, axis=1))

            csv_data = df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="Download Categorized CSV",
                data=csv_data,
                file_name="categorized_transactions.csv",
                mime="text/csv",
            )

if __name__ == "__main__":
    main()
