from pathlib import Path
from typing import Optional

import joblib
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC

# Paths are relative to this file's location:
#   train_model.py  ->  streamlit_frontend/backend/transaction_classifier/
#   data/           ->  streamlit_frontend/data/
#   classifier_model/ -> streamlit_frontend/classifier_model/  (same dir used by transaction_classify.py)
_HERE = Path(__file__).resolve()
_FRONTEND_DIR = _HERE.parents[2]  # streamlit_frontend/

DEFAULT_DATA_PATH = _FRONTEND_DIR / "data" / "transactions_corrected_full.csv"
DEFAULT_MODEL_DIR = _FRONTEND_DIR / "classifier_model"
CATEGORY_MODEL_NAME = "category_classifier.pkl"
GST_MODEL_NAME = "gst_category_classifier.pkl"


def build_pipeline() -> Pipeline:
    return Pipeline([
        ("tfidf", TfidfVectorizer(ngram_range=(1, 2), max_features=10000, sublinear_tf=True)),
        ("clf", LinearSVC(max_iter=2000)),
    ])


def _prepare_data(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Validate and extract feature/label columns from a DataFrame."""
    text_col = "description" if "description" in df.columns else "transaction_description"
    for col in [text_col, "category", "gst_category"]:
        if col not in df.columns:
            raise ValueError(f"Missing required column: '{col}'")

    X = df[text_col].fillna("").astype(str).str.strip()
    y_cat = df["category"].fillna("Unknown").astype(str).str.strip()
    y_gst = df["gst_category"].fillna("Unknown").astype(str).str.strip()

    valid = X.ne("")
    return X[valid], y_cat[valid], y_gst[valid]


def load_data(data_path: Path) -> tuple[pd.Series, pd.Series, pd.Series]:
    df = pd.read_csv(data_path)
    print(f"Loaded {len(df)} rows from {data_path}")
    return _prepare_data(df)


def _fit_and_evaluate(
    X_train: pd.Series,
    X_test: pd.Series,
    y_train: pd.Series,
    y_test: pd.Series,
) -> tuple[Pipeline, float, str]:
    model = build_pipeline()
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    report = classification_report(y_test, y_pred, zero_division=0)
    return model, acc, report


def train_from_df(df: pd.DataFrame, model_dir: Path = DEFAULT_MODEL_DIR) -> dict:
    """Train both models from a DataFrame and return metrics.

    Returns a dict with keys:
        total_rows, category_accuracy, gst_accuracy,
        category_report, gst_report, cat_path, gst_path
    """
    model_dir.mkdir(parents=True, exist_ok=True)
    X, y_cat, y_gst = _prepare_data(df)

    X_train, X_test, y_cat_train, y_cat_test, y_gst_train, y_gst_test = train_test_split(
        X, y_cat, y_gst,
        test_size=0.2,
        random_state=42,
        stratify=y_cat,
    )

    cat_model, cat_acc, cat_report = _fit_and_evaluate(X_train, X_test, y_cat_train, y_cat_test)
    gst_model, gst_acc, gst_report = _fit_and_evaluate(X_train, X_test, y_gst_train, y_gst_test)

    cat_path = model_dir / CATEGORY_MODEL_NAME
    gst_path = model_dir / GST_MODEL_NAME
    joblib.dump(cat_model, cat_path)
    joblib.dump(gst_model, gst_path)

    return {
        "total_rows": len(X),
        "train_rows": len(X_train),
        "test_rows": len(X_test),
        "category_accuracy": cat_acc,
        "gst_accuracy": gst_acc,
        "category_report": cat_report,
        "gst_report": gst_report,
        "cat_path": str(cat_path),
        "gst_path": str(gst_path),
    }


def train(data_path: Path = DEFAULT_DATA_PATH, model_dir: Path = DEFAULT_MODEL_DIR) -> None:
    """CLI entry point — loads CSV from disk and trains models."""
    df = pd.read_csv(data_path)
    print(f"Loaded {len(df)} rows from {data_path}")
    metrics = train_from_df(df, model_dir)
    print(f"\nCategory Accuracy: {metrics['category_accuracy']:.2%}")
    print(metrics["category_report"])
    print(f"GST Category Accuracy: {metrics['gst_accuracy']:.2%}")
    print(metrics["gst_report"])
    print(f"\nCategory model saved to {metrics['cat_path']}")
    print(f"GST category model saved to {metrics['gst_path']}")


if __name__ == "__main__":
    train()

