import pandas as pd
import re, json, hashlib
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

CSV_PATH = "TNZ.csv"
MODEL = "0xroyce/Plutus-3B"
OLLAMA_URL = "http://localhost:11434/api/chat"

ALLOWED = {"Inventory", "Fixed_Asset", "Transfer", "Revenue", "Expense", "Other"}

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
RDR_JSON_PATHS = [
    WORKSPACE_ROOT / "data" / "rdr_rules.json",
    WORKSPACE_ROOT / "rdr_rules.json",
]
RDR_RULES = []


def clean_text(x) -> str:
    s = str(x or "")
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def safe_float(x, default=0.0) -> float:
    try:
        if pd.isna(x):
            return default
        # remove commas if any
        return float(str(x).replace(",", "").strip())
    except Exception:
        return default

def pick_col(df, candidates):
    lower_map = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None

def ollama_chat_json(model: str, system: str, user: str) -> dict:
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {"temperature": 0}
    }
    data = json.dumps(payload).encode("utf-8")
    req = Request(OLLAMA_URL, data=data, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))

SYSTEM = """
You are a strict bank-transaction classifier.

Return ONLY one label from this exact set:
- Inventory
- Fixed_Asset
- Transfer
- Revenue
- Expense
- Other

Definitions:
- Revenue: money coming in from sales/services, customer payments, income.
- Transfer: moving money between own accounts, internal transfers, savings, card payments to self.
- Inventory: purchases of stock/materials intended to be sold later or used to make items for sale (wholesale, bulk, cartons, supplier invoices).
- Fixed_Asset: long-term equipment/tools/vehicles/computers/furniture/machinery used over time.
- Expense: operating costs that are not inventory and not fixed assets.
- Other: use only if it doesn't fit above or is unclear.

Hard constraints:
- Output MUST be JSON ONLY like: {"label":"Expense"}
- The label value MUST be exactly one of: Inventory, Fixed_Asset, Transfer, Revenue, Expense, Other
- No extra keys. No extra text.
""".strip()

def cache_key(desc: str, debit: float, credit: float) -> str:
    raw = f"{desc.lower()}|{debit:.2f}|{credit:.2f}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()

# ---------------------------
# Ripple Down Rules (RDR) layer (simple start)
# Add more rules as you find mistakes.
# Each rule is: if condition matches -> force label
# ---------------------------
def load_rdr_rules(paths):
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                rules = json.load(f)

            if not isinstance(rules, list):
                raise ValueError("RDR rules JSON must be a list")

            # highest priority first (so exceptions override)
            rules.sort(key=lambda r: int(r.get("priority", 0)), reverse=True)

            # optional validation
            cleaned = []
            for r in rules:
                label = r.get("then", "")
                cond = r.get("if", {})
                if label not in ALLOWED:
                    continue
                if not isinstance(cond, dict):
                    continue
                cleaned.append(r)

            return cleaned

        except FileNotFoundError:
            continue
        except Exception as e:
            print(f"WARNING: Failed to load RDR rules from {path}: {e}")

    print(f"WARNING: No RDR rules found in: {', '.join(str(p) for p in paths)}")
    return []

def rdr_apply(desc: str, debit: float, credit: float):
    d = (desc or "").lower()

    for rule in RDR_RULES:
        cond = rule.get("if", {})

        if "debit_gt" in cond and not (debit > float(cond["debit_gt"])):
            continue
        if "credit_gt" in cond and not (credit > float(cond["credit_gt"])):
            continue

        if "contains_any" in cond:
            if not any(str(k).lower() in d for k in cond["contains_any"]):
                continue

        if "regex_any" in cond:
            if not any(re.search(rx, d) for rx in cond["regex_any"]):
                continue

        return rule.get("then")

    return None



def extract_label_from_content(content: str) -> str:
    # best effort parse: try JSON first, then regex
    content = (content or "").strip()
    try:
        data = json.loads(content)
        return (data.get("label") or "").strip()
    except Exception:
        m = re.search(r'"label"\s*:\s*"([^"]+)"', content)
        return (m.group(1).strip() if m else "")

def classify_llm(desc: str, debit: float, credit: float) -> str:
    user = f"""Transaction:
Description: {desc}
Debit (money out): {debit}
Credit (money in): {credit}
Return JSON now."""
    resp = ollama_chat_json(MODEL, SYSTEM, user)
    content = (resp.get("message", {}) or {}).get("content", "")
    label = extract_label_from_content(content)

    if label not in ALLOWED:
        label = "Other"
    return label

def direction_guard(label: str, debit: float, credit: float) -> str:
    # If money out only, revenue is suspicious -> Other
    if debit > 0 and credit <= 0 and label == "Revenue":
        return "Other"
    # If money in only, expense is suspicious -> Other
    if credit > 0 and debit <= 0 and label == "Expense":
        return "Other"
    return label

def classify(desc: str, debit: float, credit: float) -> str:
    # 1) RDR override first (fast + consistent)
    forced = rdr_apply(desc, debit, credit)
    if forced in ALLOWED:
        return forced

    # 2) LLM fallback
    label = classify_llm(desc, debit, credit)

    # 3) Guards
    label = direction_guard(label, debit, credit)
    return label

def main():
    df = pd.read_csv(CSV_PATH).dropna(how="all")

    global RDR_RULES
    RDR_RULES = load_rdr_rules(RDR_JSON_PATHS)
    print(f"Loaded {len(RDR_RULES)} RDR rules")

    col_desc = pick_col(df, ["Description", "Narration", "Details", "Transaction Details", "Merchant", "Memo"])
    col_debit = pick_col(df, ["Debit", "Out", "Money Out", "Spent", "Withdrawal", "Amount"])
    col_credit = pick_col(df, ["Credit", "In", "Money In", "Received", "Deposit"])
    col_date = pick_col(df, ["Date", "Transaction Date", "Posted Date"])

    if not col_desc:
        print("ERROR: Could not find a description-like column. Your columns are:")
        print(list(df.columns))
        return

    cache = {}
    printed = 0

    for r in df.itertuples(index=False):
        desc = clean_text(getattr(r, col_desc, ""))
        if not desc:
            continue

        debit = safe_float(getattr(r, col_debit, 0.0)) if col_debit else 0.0
        credit = safe_float(getattr(r, col_credit, 0.0)) if col_credit else 0.0
        date = clean_text(getattr(r, col_date, "")) if col_date else ""

        k = cache_key(desc, debit, credit)
        if k in cache:
            label = cache[k]
        else:
            label = classify(desc, debit, credit)
            cache[k] = label

        printed += 1
        print(f"{date}\t{desc}\tDebit={debit}\tCredit={credit}\t->\t{label}")

    if printed == 0:
        print("No rows printed (empty file or empty description column).")

if __name__ == "__main__":
    try:
        main()
    except HTTPError as e:
        print("HTTP ERROR talking to Ollama:", e)
        print("Fix: ensure Ollama is running: `ollama serve`")
    except URLError as e:
        print("CONNECTION ERROR talking to Ollama:", e)
        print("Fix: ensure Ollama is running and reachable at http://localhost:11434")
