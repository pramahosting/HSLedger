import json
import os
import re
import time
from typing import Any, Dict, List, Optional

import streamlit as st

ALLOWED = ["Inventory", "Fixed_Asset", "Transfer", "Revenue", "Expense", "Other"]
GST_ALLOWED = [
    "",
    "GST on Expenses",
    "GST on Capital",
    "GST on Income",
    "GST Free Expenses",
    "GST Free Income",
    "BAS Excluded",
]


# -----------------------------
# IO helpers
# -----------------------------
def load_rules(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Rules JSON must be a list of rule objects.")
    return data


def atomic_write_json(path: str, data: Any) -> None:
    tmp_path = f"{path}.tmp.{int(time.time() * 1000)}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


# -----------------------------
# RDR matching (for testing)
# -----------------------------
def get_priority(rule: Dict[str, Any]) -> int:
    try:
        return int(rule.get("priority", 0))
    except Exception:
        return 0


def rdr_apply(desc: str, debit: float, credit: float, rules: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    d = (desc or "").lower()
    rules_sorted = sorted(rules, key=get_priority, reverse=True)

    for rule in rules_sorted:
        cond = rule.get("if", {}) or {}
        if not isinstance(cond, dict):
            continue

        # numeric checks
        if "debit_gt" in cond and not (debit > float(cond["debit_gt"])):
            continue
        if "credit_gt" in cond and not (credit > float(cond["credit_gt"])):
            continue

        # text checks
        if "contains_any" in cond:
            needles = cond["contains_any"]
            if not isinstance(needles, list) or not any(str(k).lower() in d for k in needles):
                continue

        if "regex_any" in cond:
            rxs = cond["regex_any"]
            if not isinstance(rxs, list) or not any(re.search(rx, d) for rx in rxs):
                continue

        return rule

    return None


# -----------------------------
# Helpers for easy rule creation
# -----------------------------
def normalize_list_field(raw: str) -> List[str]:
    # comma/newline separated
    parts = []
    for chunk in raw.replace("\n", ",").split(","):
        s = chunk.strip()
        if s:
            parts.append(s)
    return parts


def next_rule_id(rules: List[Dict[str, Any]]) -> str:
    # find max rNNN, return next
    max_n = 0
    for r in rules:
        rid = str(r.get("id", "")).strip()
        m = re.fullmatch(r"r(\d+)", rid)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"r{max_n + 1:03d}"


def validate_regex_list(regex_list: List[str]) -> List[str]:
    errs = []
    for rx in regex_list:
        try:
            re.compile(rx)
        except Exception as e:
            errs.append(f"Invalid regex: {rx!r} ({e})")
    return errs


def build_condition(direction: str, keywords: List[str], regexes: List[str]) -> Dict[str, Any]:
    cond: Dict[str, Any] = {}

    # Direction presets (keeps UX simple)
    if direction == "Debit > 0 (Money Out)":
        cond["debit_gt"] = 0
    elif direction == "Credit > 0 (Money In)":
        cond["credit_gt"] = 0
    elif direction == "Either (ignore debit/credit)":
        pass

    if keywords:
        cond["contains_any"] = keywords
    if regexes:
        cond["regex_any"] = regexes

    return cond


def ensure_priorities(rules: List[Dict[str, Any]]) -> None:
    # If some rules already have priority, keep them.
    # If missing, set based on current order.
    base = 1000
    for i, r in enumerate(rules):
        if "priority" not in r:
            r["priority"] = base + i


def render():
    st.set_page_config(page_title="RDR Rules Editor", layout="wide")
    st.title("RDR Rules Editor (JSON)")

    with st.sidebar:
        st.subheader("Storage")
        rules_path = st.text_input("Rules JSON path", value=os.path.join("data", "rdr_rules.json"))
        reload_btn = st.button("Reload from disk")
        st.caption("Rule IDs auto-generate like r001, r002, ...")

    if "rules" not in st.session_state or reload_btn:
        try:
            st.session_state["rules"] = load_rules(rules_path)
            st.session_state["load_error"] = ""
        except Exception as e:
            st.session_state["rules"] = []
            st.session_state["load_error"] = str(e)

    rules: List[Dict[str, Any]] = st.session_state["rules"]

    if st.session_state.get("load_error"):
        st.error(f"Failed to load rules: {st.session_state['load_error']}")

    ensure_priorities(rules)

    left, right = st.columns([1.25, 0.75])

    with left:
        st.subheader("Rules (newest wins)")
        if not rules:
            st.info("No rules yet. Add one on the right.")
        else:
            # Sort by priority DESC so newest/strongest show first
            view = sorted(enumerate(rules), key=lambda t: get_priority(t[1]), reverse=True)
            for idx, rule in view:
                rid = rule.get("id", f"rule_{idx}")
                then = rule.get("then", "?")
                cond = rule.get("if", {})
                summary_bits = []
                if isinstance(cond, dict):
                    if "debit_gt" in cond:
                        summary_bits.append("Debit>0")
                    if "credit_gt" in cond:
                        summary_bits.append("Credit>0")
                    if "contains_any" in cond:
                        summary_bits.append(f"kw:{len(cond['contains_any'])}")
                    if "regex_any" in cond:
                        summary_bits.append(f"re:{len(cond['regex_any'])}")
                summary = ", ".join(summary_bits) if summary_bits else "no conditions?"

                with st.expander(f"{rid} → {then}   ({summary})", expanded=False):
                    st.json(rule)

                    c1, c2, c3 = st.columns([1, 1, 2])
                    with c1:
                        if st.button("Edit", key=f"edit_{idx}"):
                            st.session_state["edit_idx"] = idx
                    with c2:
                        if st.button("Delete", key=f"del_{idx}"):
                            st.session_state["del_idx"] = idx
                    with c3:
                        st.caption("Priority is hidden; newer rules automatically override older ones.")

        # delete flow
        if "del_idx" in st.session_state:
            di = st.session_state["del_idx"]
            st.warning(f"Delete rule at index {di}?")
            d1, d2 = st.columns(2)
            with d1:
                if st.button("Confirm delete"):
                    if 0 <= di < len(rules):
                        rules.pop(di)
                        st.session_state["rules"] = rules
                    st.session_state.pop("del_idx", None)
            with d2:
                if st.button("Cancel"):
                    st.session_state.pop("del_idx", None)

        st.divider()

        s1, s2 = st.columns([1, 2])
        with s1:
            if st.button("Save to JSON"):
                try:
                    atomic_write_json(rules_path, rules)
                    st.success(f"Saved {len(rules)} rules to {rules_path}")
                except Exception as e:
                    st.error(f"Save failed: {e}")
        with s2:
            st.caption("Saving is atomic (protects against corrupted JSON).")

    with right:
        st.subheader("Add / Edit (easy)")

        edit_idx = st.session_state.get("edit_idx", None)
        editing = isinstance(edit_idx, int) and 0 <= edit_idx < len(rules)

        if editing:
            base = rules[edit_idx]
            st.info(f"Editing {base.get('id', f'index {edit_idx}')}")
        else:
            base = {"id": "", "then": "Other", "if": {}}

        # ID is auto for new rules
        auto_id = next_rule_id(rules) if not editing else str(base.get("id", ""))
        st.text_input("Rule ID (auto)", value=auto_id, disabled=True)

        then = st.selectbox(
            "GL_ACCOUNT (then)",
            options=ALLOWED,
            index=ALLOWED.index(base.get("then", "Other")) if base.get("then", "Other") in ALLOWED else ALLOWED.index("Other"),
        )

        existing_gst = str(
            base.get("then_gst_category", base.get("then_gst", base.get("gst_category", "")))
        ).strip()
        if existing_gst not in GST_ALLOWED:
            existing_gst = ""

        then_gst = st.selectbox(
            "GST CATEGORY",
            options=GST_ALLOWED,
            index=GST_ALLOWED.index(existing_gst),
            help="Leave blank to keep model GST prediction. Choose a value to force GST category when this rule matches.",
        )

        direction_default = "Either (ignore debit/credit)"
        cond0 = base.get("if", {}) if isinstance(base.get("if", {}), dict) else {}
        if "debit_gt" in cond0:
            direction_default = "Debit > 0 (Money Out)"
        elif "credit_gt" in cond0:
            direction_default = "Credit > 0 (Money In)"

        direction = st.selectbox(
            "Direction",
            ["Debit > 0 (Money Out)", "Credit > 0 (Money In)", "Either (ignore debit/credit)"],
            index=["Debit > 0 (Money Out)", "Credit > 0 (Money In)", "Either (ignore debit/credit)"].index(direction_default),
            help="Keep it simple: choose whether the rule applies to debit, credit, or either.",
        )

        kw_existing = cond0.get("contains_any", []) if isinstance(cond0.get("contains_any", []), list) else []
        re_existing = cond0.get("regex_any", []) if isinstance(cond0.get("regex_any", []), list) else []

        keywords_raw = st.text_area(
            "Keywords (contains any) — comma/newline separated",
            value=", ".join(kw_existing),
            placeholder="coffee beans, arabica, bulk",
        )

        regex_raw = st.text_area(
            "Regex (optional) — one per line or comma separated",
            value="\n".join(re_existing),
            placeholder=r"\b\d+(\.\d+)?\s*kg\b",
        )
        st.caption(r"Regex example for weights: \b\d+(\.\d+)?\s*kg\b")

        keywords = normalize_list_field(keywords_raw)
        regexes = normalize_list_field(regex_raw)

        regex_errs = validate_regex_list(regexes) if regexes else []

        cond = build_condition(direction, keywords, regexes)

        # Basic validation: must have at least keyword or regex
        errs = []
        if then not in ALLOWED:
            errs.append("Invalid label.")
        if not keywords and not regexes:
            errs.append("Add at least one keyword or regex.")
        errs.extend(regex_errs)

        # Auto priority: new rules win
        auto_priority = (max([get_priority(r) for r in rules], default=999) + 1)

        built_rule = {
            "id": auto_id,
            "priority": int(base.get("priority", auto_priority)) if editing else int(auto_priority),
            "then": then,
            "if": cond,
        }
        if then_gst:
            built_rule["then_gst_category"] = then_gst

        if errs:
            st.error("Fix these issues:")
            for e in errs:
                st.write(f"- {e}")
        else:
            st.success("Ready to save this rule.")

        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button("Add rule", disabled=bool(errs) or editing):
                rules.append(built_rule)
                st.session_state["rules"] = rules
                st.success(f"Added {built_rule['id']}. Click Save to write JSON.")
        with b2:
            if st.button("Update rule", disabled=bool(errs) or not editing):
                rules[edit_idx] = built_rule
                st.session_state["rules"] = rules
                st.session_state.pop("edit_idx", None)
                st.success(f"Updated {built_rule['id']}. Click Save to write JSON.")
        with b3:
            if st.button("Cancel edit", disabled=not editing):
                st.session_state.pop("edit_idx", None)

        st.divider()
        st.subheader("Quick test")

        tdesc = st.text_input("Description", value="COFFEE BEANS ARABICA 20KG")
        tdebit = st.number_input("Debit", value=680.0, step=1.0)
        tcredit = st.number_input("Credit", value=0.0, step=1.0)

        if st.button("Run match"):
            match = rdr_apply(tdesc, float(tdebit), float(tcredit), rules)
            if match:
                forced_gst = str(
                    match.get("then_gst_category", match.get("then_gst", match.get("gst_category", "")))
                ).strip()
                if forced_gst:
                    st.success(f"Matched: {match.get('id')} -> {match.get('then')} (GST: {forced_gst})")
                else:
                    st.success(f"Matched: {match.get('id')} -> {match.get('then')}")
                st.json(match)
            else:
                st.warning("No rule matched.")

        st.caption("Tip: New rules automatically override older ones (priority hidden).")
