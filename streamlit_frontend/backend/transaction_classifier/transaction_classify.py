import json
from pathlib import Path
import re
from typing import Optional

import joblib


_DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[2] / "classifier_model"
_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
_CATEGORY_MODEL_NAME = "category_classifier.pkl"
_GST_MODEL_NAME = "gst_category_classifier.pkl"
_RDR_RULE_PATHS = [
    _WORKSPACE_ROOT / "data" / "rdr_rules.json",
    _WORKSPACE_ROOT / "rdr_rules.json",
]

_category_model = None
_gst_model = None
_loaded_model_base: Optional[Path] = None
_rdr_rules: list[dict] = []
_rdr_rules_loaded = False
_rdr_signature: Optional[tuple] = None


def load_models(model_dir: Optional[str] = None, force_reload: bool = False) -> None:
    """Load category and GST models into memory.

    Args:
        model_dir: Directory containing model .pkl files. Defaults to the
            streamlit_frontend/classifier_model directory.
        force_reload: If True, reload models even if already loaded.
    """
    global _category_model, _gst_model, _loaded_model_base

    model_base = Path(model_dir).expanduser() if model_dir else _DEFAULT_MODEL_DIR
    model_base = model_base.resolve()

    if (
        _category_model is not None
        and _gst_model is not None
        and _loaded_model_base == model_base
        and not force_reload
    ):
        return

    category_path = model_base / _CATEGORY_MODEL_NAME
    gst_path = model_base / _GST_MODEL_NAME

    if not category_path.exists():
        raise FileNotFoundError(f"Category model not found: {category_path}")
    if not gst_path.exists():
        raise FileNotFoundError(f"GST model not found: {gst_path}")

    _category_model = joblib.load(category_path)
    _gst_model = joblib.load(gst_path)
    _loaded_model_base = model_base


def _get_priority(rule: dict) -> int:
    try:
        return int(rule.get("priority", 0))
    except Exception:
        return 0


def load_rdr_rules(force_reload: bool = False) -> list[dict]:
    """Load RDR rules from disk and cache them in memory."""
    global _rdr_rules, _rdr_rules_loaded, _rdr_signature

    current_signature = tuple(
        (str(path), path.stat().st_mtime_ns if path.exists() else None)
        for path in _RDR_RULE_PATHS
    )

    if _rdr_rules_loaded and not force_reload and _rdr_signature == current_signature:
        return _rdr_rules

    rules: list[dict] = []
    for path in _RDR_RULE_PATHS:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                continue
            cleaned: list[dict] = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                cond = item.get("if", {})
                if not isinstance(cond, dict):
                    continue
                then_label = str(item.get("then", "")).strip()
                then_gst = str(
                    item.get("then_gst_category", item.get("then_gst", item.get("gst_category", "")))
                ).strip()
                if not then_label and not then_gst:
                    continue
                cleaned.append(item)

            cleaned.sort(key=_get_priority, reverse=True)
            rules = cleaned
            break
        except Exception:
            continue

    _rdr_rules = rules
    _rdr_rules_loaded = True
    _rdr_signature = current_signature
    return _rdr_rules


def _rdr_apply(description: str, debit: float = 0.0, credit: float = 0.0) -> dict:
    """Return RDR override fields for a transaction.

    The return value can include:
      - gl_account
      - gst_category
      - rule_id
    """
    text = (description or "").strip().lower()
    if not text:
        return {}

    for rule in load_rdr_rules():
        cond = rule.get("if", {}) or {}
        if not isinstance(cond, dict):
            continue

        try:
            if "debit_gt" in cond and not (float(debit or 0) > float(cond["debit_gt"])):
                continue
            if "credit_gt" in cond and not (float(credit or 0) > float(cond["credit_gt"])):
                continue
        except Exception:
            continue

        if "contains_any" in cond:
            needles = cond.get("contains_any")
            if not isinstance(needles, list) or not any(str(k).lower() in text for k in needles):
                continue

        if "regex_any" in cond:
            patterns = cond.get("regex_any")
            if not isinstance(patterns, list):
                continue
            try:
                if not any(re.search(rx, text) for rx in patterns):
                    continue
            except re.error:
                continue

        gl_value = str(rule.get("then", "")).strip()
        gst_value = str(
            rule.get("then_gst_category", rule.get("then_gst", rule.get("gst_category", "")))
        ).strip()

        if not gl_value and not gst_value:
            continue

        return {
            "gl_account": gl_value,
            "gst_category": gst_value,
            "rule_id": str(rule.get("id", "")).strip(),
        }

    return {}


def classify_gl_account(
    description: str,
    model_dir: Optional[str] = None,
    debit: float = 0.0,
    credit: float = 0.0,
) -> str:
    """Predict only the accounting category from a transaction description."""
    text = (description or "").strip()
    if not text:
        raise ValueError("Description is required for category classification.")

    forced = _rdr_apply(text, debit=debit, credit=credit)
    if forced.get("gl_account"):
        return forced["gl_account"]

    load_models(model_dir=model_dir)
    return _category_model.predict([text])[0]


def classify_gst_category(description: str, model_dir: Optional[str] = None) -> str:
    """Predict only the GST category from a transaction description."""
    text = (description or "").strip()
    if not text:
        raise ValueError("Description is required for GST classification.")

    load_models(model_dir=model_dir)
    return _gst_model.predict([text])[0]


def classify_transaction(
    description: str,
    model_dir: Optional[str] = None,
    debit: float = 0.0,
    credit: float = 0.0,
) -> dict:
    """Predict both category and GST category for a transaction description."""
    text = (description or "").strip()
    if not text:
        raise ValueError("Description is required for transaction classification.")

    forced = _rdr_apply(text, debit=debit, credit=credit)
    if forced:
        load_models(model_dir=model_dir)
        return {
            "description": text,
            "gl_account": forced.get("gl_account") or _category_model.predict([text])[0],
            "gst_category": forced.get("gst_category") or _gst_model.predict([text])[0],
            "rule_id": forced.get("rule_id", ""),
        }

    load_models(model_dir=model_dir)

    return {
        "description": text,
        "gl_account": _category_model.predict([text])[0],
        "gst_category": _gst_model.predict([text])[0],
    }
