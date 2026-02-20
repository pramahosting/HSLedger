# CLI transaction classifier:
# - Reads a CSV with a Description column
# - Classifies unique descriptions into category and GST category
# - Uses persistent disk cache across runs (ollama_cache.json)

import argparse
import json
import hashlib
import time
import re
from pathlib import Path
from typing import Dict

import pandas as pd
import ollama
import requests

# -------------------------
# Config
# -------------------------
OLLAMA_CHAT_URL_DEFAULT = "http://localhost:11434/api/chat"
CACHE_FILE = Path("ollama_cache.json")
CACHE_VERSION = "v2"
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
    "Return ONLY the category label as plain text. "
    "No explanations, no extra keys, no markdown. "
    "Rules: common merchant purchases (shops, restaurants, fuel, convenience) -> "
    "category=Expense. "
    "Use Revenue only for income/credits/sales. "
    "Use Transfer only for internal transfers between accounts. "
    "Use GST only for tax payment transactions."
)

GST_SYSTEM_MSG = (
    "You are a strict GST classifier for bank transactions. "
    "Return ONLY the GST category label as plain text. "
    "No explanations, no extra keys, no markdown."
)

# -------------------------
# Helpers: caching
# -------------------------
def normalize_desc(s: str) -> str:
    return " ".join(str(s or "").split()).strip().lower()

def cache_key(model: str, desc_normed: str, prompt_prefix: str) -> str:
    # Include model and prompt so cache updates when instructions change
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

# -------------------------
# Ollama call (schema-locked)
# -------------------------
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

def ollama_classify_cached(
    model: str,
    prompt: str,
    base_url: str,
    temperature: float,
    top_p: float,
) -> Dict[str, str]:
    return ollama_classify(model, prompt, base_url, temperature, top_p)

def ollama_predict_gst_cached(
    model: str,
    prompt: str,
    base_url: str,
    temperature: float,
    top_p: float,
) -> Dict[str, str]:
    return ollama_predict_gst(model, prompt, base_url, temperature, top_p)

# -------------------------
# CLI flow
# -------------------------
def classify_csv(
    input_csv: Path,
    output_csv: Path,
    model: str,
    base_url: str,
) -> None:
    df = pd.read_csv(input_csv)
    if "Description" not in df.columns:
        raise ValueError("The input CSV is missing the 'Description' column.")

    start_time = time.time()

    desc_series = df["Description"].fillna("").astype(str)
    unique_descs = [d for d in pd.unique(desc_series) if str(d).strip()]

    mapping: Dict[str, str] = {}
    gst_mapping: Dict[str, str] = {}
    cache_hits = 0
    cache_misses = 0
    disk_cache = load_disk_cache()
    mem_cache: Dict[str, Dict[str, str]] = {}

    for i, desc in enumerate(unique_descs):
        print(f"Classifying unique {i + 1}/{len(unique_descs)}", flush=True)
        dnorm = normalize_desc(desc)
        k = cache_key(model, dnorm, _DEFAULT_TXN_PROMPT)

        if k in mem_cache:
            mapping[desc] = mem_cache[k].get("category", "")
            cache_hits += 1
        elif k in disk_cache:
            mapping[desc] = disk_cache[k].get("category", "")
            mem_cache[k] = disk_cache[k]
            cache_hits += 1
        else:
            cache_misses += 1
            mapping[desc] = ollama_classify_cached(
                model=model,
                prompt=f"{_DEFAULT_TXN_PROMPT}\n{dnorm}",
                base_url=base_url,
                temperature=0.0,
                top_p=1.0,
            )["category"]
            mem_cache[k] = {"category": mapping[desc]}
            disk_cache[k] = {"category": mapping[desc]}

        gst_k = cache_key(model, f"{dnorm}||{mapping[desc]}", _DEFAULT_GST_PROMPT)
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
                model=model,
                prompt=f"{_DEFAULT_GST_PROMPT}\nCategory: {mapping[desc]}\nDescription: {dnorm}",
                base_url=base_url,
                temperature=0.0,
                top_p=1.0,
            )["gst_category"]
            mem_cache[gst_k] = {"gst_category": gst_mapping[desc]}
            disk_cache[gst_k] = {"gst_category": gst_mapping[desc]}

    save_disk_cache(disk_cache)

    df["Predicted_Category"] = desc_series.map(mapping).fillna("")
    df["Predicted_GST_Category"] = desc_series.map(gst_mapping).fillna("")
    df.to_csv(output_csv, index=False)

    duration = time.time() - start_time
    unknown_gst_count = int((df["Predicted_GST_Category"] == "Unknown").sum())
    print(
        f"Done! {len(df)} rows, {len(unique_descs)} unique. "
        f"Time: {duration:.2f}s | Cache: {len(disk_cache)} entries, {cache_hits} hits, {cache_misses} misses."
    )
    print(f"Unknown GST rows: {unknown_gst_count}")
    print(f"Saved: {output_csv}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify bank transaction CSV using Ollama.")
    parser.add_argument("input_csv", type=Path, help="Path to input CSV file")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV path (default: <input_stem>_categorized.csv)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Ollama model name (default: first available model)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=OLLAMA_CHAT_URL_DEFAULT,
        help=f"Ollama chat endpoint (default: {OLLAMA_CHAT_URL_DEFAULT})",
    )
    args = parser.parse_args()

    if not args.input_csv.exists():
        raise FileNotFoundError(f"Input file not found: {args.input_csv}")

    output_csv = args.output or args.input_csv.with_name(f"{args.input_csv.stem}_categorized.csv")

    model = args.model
    if not model:
        models = list_ollama_models()
        if not models:
            raise RuntimeError("No Ollama models found. Ensure Ollama is running and a model is available.")
        model = models[0]
        print(f"Using model: {model}")

    classify_csv(
        input_csv=args.input_csv,
        output_csv=output_csv,
        model=model,
        base_url=args.base_url,
    )

if __name__ == "__main__":
    main()
