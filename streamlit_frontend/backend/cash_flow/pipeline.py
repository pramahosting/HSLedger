"""
backend/cash_flow/pipeline.py
Cash Flow Prediction Pipeline — pure computation functions (no CLI, no profiling).
Called by the Streamlit cash_flow_ui.
"""

import warnings
from pathlib import Path
from datetime import datetime

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import skew as compute_skew
from sklearn.preprocessing import RobustScaler, StandardScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.dummy import DummyRegressor
from sklearn.linear_model import (
    LinearRegression, Ridge, Lasso, ElasticNet, BayesianRidge, HuberRegressor,
)
from sklearn.tree import DecisionTreeRegressor
from sklearn.ensemble import (
    RandomForestRegressor, ExtraTreesRegressor,
    GradientBoostingRegressor, HistGradientBoostingRegressor,
    AdaBoostRegressor, BaggingRegressor,
)
from sklearn.neighbors import KNeighborsRegressor
from sklearn.svm import SVR
from sklearn.neural_network import MLPRegressor

# ── Output directories ────────────────────────────────────────────────────────
CASH_FLOW_DIR = Path(__file__).resolve().parent
OUT_DIR       = CASH_FLOW_DIR / "outputs"
PLOTS_DIR     = OUT_DIR / "plots"
OUT_DIR.mkdir(parents=True, exist_ok=True)
PLOTS_DIR.mkdir(parents=True, exist_ok=True)

LEADERBOARD_CSV      = OUT_DIR / "leaderboard.csv"
NEXT_MONTH_CSV       = OUT_DIR / "next_month_prediction.csv"
LEADERBOARD_PLOT     = PLOTS_DIR / "model_leaderboard.png"
NEXT_MONTH_PLOT      = PLOTS_DIR / "next_month_forecast.png"

# ── Column detection candidates ───────────────────────────────────────────────
_DATE_CANDS    = ["Date", "date", "DATE", "Transaction Date", "Trans Date",
                  "trans_date", "Value Date", "Posting Date", "posting_date"]
_DEBIT_CANDS   = ["Debit", "debit", "DEBIT", "DR", "Withdrawal", "withdrawal",
                  "Debit Amount", "debit_amount", "Amount Debit"]
_CREDIT_CANDS  = ["Credit", "credit", "CREDIT", "CR", "Deposit", "deposit",
                  "Credit Amount", "credit_amount", "Amount Credit"]
_BALANCE_CANDS = ["Balance", "balance", "BALANCE", "Running Balance",
                  "Closing Balance", "closing_balance"]
_DESC_CANDS    = ["Description", "description", "DESC", "Narration", "narration",
                  "Particulars", "particulars", "Details", "Reference", "reference"]

_DATE_KW    = ["date", "dt", "posted", "value", "time"]
_DEBIT_KW   = ["debit", "withdrawal", "withdraw", "out", "paid", "payment", "expense"]
_CREDIT_KW  = ["credit", "deposit", "received", "income", "receipt"]
_BALANCE_KW = ["balance", "bal", "running", "closing", "avail"]
_DESC_KW    = ["desc", "narr", "detail", "particular", "memo", "note", "remark", "ref", "text"]

_RECURRING_KW = [
    "rent", "insurance", "subscription", "superannuation", "ato bas", "ato tax",
    "gym", "netflix", "spotify", "utilities", "water bill", "electricity",
    "internet", "loan repayment", "equipment lease", "accounting fees",
    "aws", "telstra", "council rates", "salary", "payroll", "interest earned",
    "bank interest", "interest income", "amazon prime", "mortgage", "pension",
    "dividend", "bpay", "direct debit", "standing order", "regular payment",
]

_MODELS = [
    ("Baseline (Mean)",        False, DummyRegressor(strategy="mean")),
    ("Linear Regression",      True,  LinearRegression()),
    ("Ridge",                  True,  Ridge(alpha=1.0)),
    ("Lasso",                  True,  Lasso(alpha=0.1, max_iter=5000)),
    ("ElasticNet",             True,  ElasticNet(alpha=0.1, l1_ratio=0.5, max_iter=5000)),
    ("Bayesian Ridge",         True,  BayesianRidge()),
    ("Huber Regressor",        True,  HuberRegressor(max_iter=500)),
    ("Decision Tree",          False, DecisionTreeRegressor(max_depth=5, random_state=42)),
    ("Random Forest",          False, RandomForestRegressor(n_estimators=150, random_state=42, n_jobs=-1)),
    ("Extra Trees",            False, ExtraTreesRegressor(n_estimators=150, random_state=42, n_jobs=-1)),
    ("Gradient Boosting",      False, GradientBoostingRegressor(n_estimators=150, random_state=42)),
    ("Hist Gradient Boosting", False, HistGradientBoostingRegressor(max_iter=150, random_state=42)),
    ("AdaBoost",               False, AdaBoostRegressor(n_estimators=100, random_state=42)),
    ("Bagging",                False, BaggingRegressor(n_estimators=100, random_state=42, n_jobs=-1)),
    ("KNN (k=5)",              True,  KNeighborsRegressor(n_neighbors=5)),
    ("SVR (RBF)",              True,  SVR(kernel="rbf", C=10, epsilon=0.1)),
    ("MLP Neural Net",         True,  MLPRegressor(
        hidden_layer_sizes=(64, 32), max_iter=1000,
        random_state=42, early_stopping=True,
    )),
]


# ── Column detection ──────────────────────────────────────────────────────────

def find_col(df: pd.DataFrame, candidates: list[str], keywords: list[str] | None = None) -> str | None:
    lower_map = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    if keywords:
        for col_lower, col_orig in lower_map.items():
            for kw in keywords:
                if kw in col_lower:
                    return col_orig
    return None


def auto_detect_columns(df: pd.DataFrame) -> dict[str, str | None]:
    """Return best-guess column mapping for date/debit/credit/balance/desc."""
    return {
        "date":    find_col(df, _DATE_CANDS,    _DATE_KW),
        "debit":   find_col(df, _DEBIT_CANDS,   _DEBIT_KW),
        "credit":  find_col(df, _CREDIT_CANDS,  _CREDIT_KW),
        "balance": find_col(df, _BALANCE_CANDS, _BALANCE_KW),
        "desc":    find_col(df, _DESC_CANDS,    _DESC_KW),
    }


# ── Preprocessing ─────────────────────────────────────────────────────────────

def _classify_recurring(desc_series: pd.Series) -> pd.Series:
    upper = desc_series.fillna("").str.lower()
    mask = pd.Series(False, index=desc_series.index)
    for kw in _RECURRING_KW:
        mask |= upper.str.contains(kw, regex=False)
    return mask


def preprocess(df: pd.DataFrame, col_map: dict) -> tuple[pd.DataFrame, dict, dict]:
    """
    Clean and enrich raw transaction data.

    Returns:
        df_proc       — processed DataFrame
        transform_meta — skewness / transform info per column
        outlier_bounds — IQR upper bounds per column
    """
    df = df.copy()

    # Rename to standard names
    rename = {v: k for k, v in col_map.items() if v}
    df = df.rename(columns=rename)

    # Date parsing
    df["date"] = pd.to_datetime(df["date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    # Numeric conversion
    for col in ("debit", "credit"):
        df[col] = (
            df[col]
            .astype(str)
            .str.replace(r"[$,£€\s]", "", regex=True)
            .pipe(pd.to_numeric, errors="coerce")
            .fillna(0.0)
            .abs()
        )
    if "balance" in df.columns:
        df["balance"] = (
            df["balance"]
            .astype(str)
            .str.replace(r"[$,£€\s]", "", regex=True)
            .pipe(pd.to_numeric, errors="coerce")
        )

    # Derived columns
    df["amount_signed"] = df["credit"] - df["debit"]
    df["year_month"]    = df["date"].dt.to_period("M")
    df["month_num"]     = df["date"].dt.month
    df["year"]          = df["date"].dt.year

    # Recurring classification
    if "desc" in df.columns:
        df["is_recurring"] = _classify_recurring(df["desc"])
    else:
        df["is_recurring"] = False

    # Skewness & log1p transform
    transform_meta: dict[str, dict] = {}
    for col in ("debit", "credit"):
        non_zero = df[df[col] > 0][col]
        if len(non_zero) < 4:
            continue
        sk_before = compute_skew(non_zero, bias=False)
        transform_meta[col] = {"skew_before": round(sk_before, 3)}
        if sk_before > 1.0:
            df[f"{col}_log"] = np.log1p(df[col])
            sk_after = compute_skew(df[df[f"{col}_log"] > 0][f"{col}_log"], bias=False)
            transform_meta[col]["transform"]  = "log1p"
            transform_meta[col]["skew_after"] = round(sk_after, 3)
        else:
            transform_meta[col]["transform"]  = "none"
            transform_meta[col]["skew_after"] = round(sk_before, 3)

    # Outlier flagging (IQR × 3)
    outlier_bounds: dict[str, float] = {}
    for col in ("debit", "credit"):
        nz = df[df[col] > 0][col]
        if len(nz) < 4:
            continue
        q1, q3 = nz.quantile(0.25), nz.quantile(0.75)
        upper = q3 + 3.0 * (q3 - q1)
        df[f"{col}_is_outlier"] = (df[col] > upper).astype(int)
        outlier_bounds[col] = upper

    return df, transform_meta, outlier_bounds


def validate_date_span(df: pd.DataFrame) -> tuple[int, str, str]:
    """Return (months_span, date_min_str, date_max_str)."""
    d_min, d_max = df["date"].min(), df["date"].max()
    months_span = (d_max.year - d_min.year) * 12 + (d_max.month - d_min.month) + 1
    return months_span, str(d_min.date()), str(d_max.date())


# ── Monthly feature engineering ───────────────────────────────────────────────

def monthly_features(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate to monthly level and build lag/rolling features."""
    grp = df.groupby("year_month")

    agg_dict: dict = {
        "total_income":  ("credit", "sum"),
        "total_expense": ("debit",  "sum"),
        "tx_count":      ("debit",  "count"),
    }
    if "balance" in df.columns:
        agg_dict["avg_balance"] = ("balance", "mean")

    monthly = grp.agg(**agg_dict).reset_index()
    monthly["net_cashflow"]  = monthly["total_income"] - monthly["total_expense"]
    monthly["expense_ratio"] = (
        monthly["total_expense"] / monthly["total_income"].replace(0, np.nan)
    ).fillna(0).clip(upper=5.0)

    if "is_recurring" in df.columns:
        rec = df[df["is_recurring"]].groupby("year_month").agg(
            rec_income=("credit", "sum"), rec_expense=("debit", "sum")
        ).reset_index()
        irr = df[~df["is_recurring"]].groupby("year_month").agg(
            irr_income=("credit", "sum"), irr_expense=("debit", "sum")
        ).reset_index()
        monthly = monthly.merge(rec, on="year_month", how="left").fillna(0)
        monthly = monthly.merge(irr, on="year_month", how="left").fillna(0)
        monthly["rec_net"] = monthly["rec_income"] - monthly["rec_expense"]
        monthly["irr_net"] = monthly["irr_income"] - monthly["irr_expense"]

    monthly["month_num"] = monthly["year_month"].apply(lambda p: p.month)
    monthly["month_sin"] = np.sin(2 * np.pi * monthly["month_num"] / 12)
    monthly["month_cos"] = np.cos(2 * np.pi * monthly["month_num"] / 12)

    for col in ("net_cashflow", "total_income", "total_expense", "tx_count"):
        for lag in (1, 2, 3):
            monthly[f"{col}_lag{lag}"] = monthly[col].shift(lag)

    for col in ("net_cashflow", "total_income", "total_expense"):
        monthly[f"{col}_roll3"] = monthly[col].shift(1).rolling(3, min_periods=2).mean()
        monthly[f"{col}_roll6"] = monthly[col].shift(1).rolling(6, min_periods=3).mean()

    if "rec_net" in monthly.columns:
        for lag in (1, 2):
            monthly[f"rec_net_lag{lag}"] = monthly["rec_net"].shift(lag)
            monthly[f"irr_net_lag{lag}"] = monthly["irr_net"].shift(lag)

    monthly["target_next_month"] = monthly["net_cashflow"].shift(-1)

    return monthly


# ── Model training & leaderboard ──────────────────────────────────────────────

def train_leaderboard(monthly: pd.DataFrame) -> tuple[pd.DataFrame, dict, list[str], pd.DataFrame]:
    """
    Train all 17 models via TimeSeriesSplit CV.

    Returns:
        lb             — leaderboard DataFrame (sorted by cv_rmse)
        trained_models — {name: (type, estimator, scaler_or_None)}
        feature_cols   — list of feature column names
        data           — clean monthly data used for training
    """
    exclude = {"year_month", "month_num", "target_next_month"}
    all_feature_cols = [c for c in monthly.columns if c not in exclude]
    data = monthly[all_feature_cols + ["target_next_month"]].dropna().reset_index(drop=True)

    if len(data) < 5:
        raise ValueError(
            f"Only {len(data)} complete months after removing NaN rows. "
            "Need at least 12 months of data."
        )

    X = data[all_feature_cols].values
    y = data["target_next_month"].values

    scaler = RobustScaler()
    X_scaled = scaler.fit_transform(X)

    n_splits = min(5, max(3, len(data) - 3))
    tscv = TimeSeriesSplit(n_splits=n_splits)

    results: list[dict] = []
    trained_models: dict[str, tuple] = {}

    for name, needs_scaling, estimator in _MODELS:
        try:
            X_in = X_scaled if needs_scaling else X
            rmses, maes, r2s = [], [], []

            for tr_idx, te_idx in tscv.split(X_in):
                X_tr, X_te = X_in[tr_idx], X_in[te_idx]
                y_tr, y_te = y[tr_idx], y[te_idx]
                if needs_scaling and not isinstance(estimator, DummyRegressor):
                    sc_fold = StandardScaler()
                    X_tr = sc_fold.fit_transform(X_tr)
                    X_te = sc_fold.transform(X_te)
                estimator.fit(X_tr, y_tr)
                preds = estimator.predict(X_te)
                rmses.append(mean_squared_error(y_te, preds) ** 0.5)
                maes.append(mean_absolute_error(y_te, preds))
                r2s.append(r2_score(y_te, preds))

            if needs_scaling:
                sc_final = StandardScaler()
                X_final = sc_final.fit_transform(X_in)
                estimator.fit(X_final, y)
                trained_models[name] = ("scaled", estimator, sc_final)
            else:
                estimator.fit(X_in, y)
                trained_models[name] = ("unscaled", estimator, None)

            results.append({
                "model":    name,
                "cv_rmse":  float(np.mean(rmses)),
                "cv_mae":   float(np.mean(maes)),
                "cv_r2":    float(np.mean(r2s)),
                "rmse_std": float(np.std(rmses)),
            })

        except Exception:
            pass

    lb = (
        pd.DataFrame(results)
        .sort_values("cv_rmse")
        .reset_index(drop=True)
    )
    lb["rank"] = range(1, len(lb) + 1)
    lb.to_csv(LEADERBOARD_CSV, index=False)

    _save_leaderboard_chart(lb)

    return lb, trained_models, all_feature_cols, data


def _save_leaderboard_chart(lb: pd.DataFrame) -> None:
    top = lb.head(min(17, len(lb))).copy()
    n   = len(top)

    fig, axes = plt.subplots(1, 3, figsize=(20, max(6, n * 0.5 + 2)))
    fig.patch.set_facecolor("#0F1117")
    fig.suptitle(
        "Model Leaderboard — Performance Comparison",
        fontsize=14, fontweight="bold", color="white", y=1.01,
    )

    def _style(ax):
        ax.set_facecolor("#1A1D27")
        ax.tick_params(colors="white", labelsize=9)
        ax.xaxis.label.set_color("white")
        ax.title.set_color("white")
        for sp in ax.spines.values():
            sp.set_edgecolor("#444")

    bar_colors = [
        "#2ECC71" if i == 0 else ("#27AE60" if i < 3 else ("#F39C12" if i < 8 else "#95A5A6"))
        for i in range(n)
    ]

    for ax, col, xlabel, title in (
        (axes[0], "cv_rmse", "RMSE (lower is better)", "RMSE"),
        (axes[1], "cv_mae",  "MAE  (lower is better)", "MAE"),
    ):
        _style(ax)
        bars = ax.barh(top["model"][::-1], top[col][::-1],
                       color=bar_colors[::-1], alpha=0.85, edgecolor="#333")
        ax.set_xlabel(xlabel, color="white")
        ax.set_title(title, fontweight="bold")
        for bar, val in zip(bars, top[col][::-1]):
            ax.text(
                bar.get_width() * 1.01,
                bar.get_y() + bar.get_height() / 2,
                f"${val:,.0f}", va="center", ha="left", color="white", fontsize=7.5,
            )

    _style(axes[2])
    r2_colors = [
        "#2ECC71" if v > 0.3 else ("#F39C12" if v > 0 else "#E74C3C")
        for v in top["cv_r2"][::-1]
    ]
    bars2 = axes[2].barh(
        top["model"][::-1], top["cv_r2"][::-1],
        color=r2_colors, alpha=0.85, edgecolor="#333",
    )
    axes[2].axvline(0, color="#888", linewidth=1, linestyle="--")
    axes[2].set_xlabel("R² (higher is better)", color="white")
    axes[2].set_title("R² Score", fontweight="bold")
    for bar, val in zip(bars2, top["cv_r2"][::-1]):
        axes[2].text(
            bar.get_width() + (0.01 if val >= 0 else -0.01),
            bar.get_y() + bar.get_height() / 2,
            f"{val:.3f}", va="center",
            ha="left" if val >= 0 else "right",
            color="white", fontsize=7.5,
        )

    plt.tight_layout()
    plt.savefig(LEADERBOARD_PLOT, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()


# ── Prediction ────────────────────────────────────────────────────────────────

def predict_next_month(
    chosen: str,
    trained_models: dict,
    feature_cols: list[str],
    monthly: pd.DataFrame,
    data: pd.DataFrame,
) -> dict:
    """
    Run the chosen model against the last available month's features.

    Returns a dict with prediction details, and saves:
      - next_month_prediction.csv
      - next_month_forecast.png
    """
    last_features = data[feature_cols].iloc[-1:].values
    model_type, estimator, sc_fold = trained_models[chosen]

    if model_type == "scaled":
        last_scaled = sc_fold.transform(last_features)
        prediction  = float(estimator.predict(last_scaled)[0])
    else:
        prediction = float(estimator.predict(last_features)[0])

    last_period = monthly["year_month"].iloc[-1]
    next_period = last_period + 1

    avg_expense = monthly["total_expense"].tail(3).mean()
    est_income  = prediction + avg_expense
    est_expense = avg_expense
    avg_3m      = monthly["net_cashflow"].tail(3).mean()

    pred_dict = {
        "prediction_for":         str(next_period),
        "model_used":             chosen,
        "predicted_net_cashflow": prediction,
        "estimated_income":       est_income,
        "estimated_expense":      est_expense,
        "avg_last_3m_net":        avg_3m,
        "generated_at":           datetime.now().isoformat(),
    }
    pd.DataFrame([pred_dict]).to_csv(NEXT_MONTH_CSV, index=False)

    _save_forecast_chart(monthly, next_period, prediction, chosen)

    return pred_dict


def _save_forecast_chart(monthly, next_period, prediction: float, model_name: str) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 7))
    fig.patch.set_facecolor("#0F1117")
    fig.suptitle(
        f"Next Month Forecast — {model_name}",
        fontsize=14, fontweight="bold", color="white",
    )

    for ax in (ax1, ax2):
        ax.set_facecolor("#1A1D27")
        ax.tick_params(colors="white", labelsize=9)
        ax.xaxis.label.set_color("white")
        ax.yaxis.label.set_color("white")
        ax.title.set_color("white")
        for sp in ax.spines.values():
            sp.set_edgecolor("#444")

    periods_str = [str(p) for p in monthly["year_month"]] + [str(next_period)]
    hist_vals   = monthly["net_cashflow"].tolist()
    hist_colors = ["#2ECC71" if v >= 0 else "#E74C3C" for v in hist_vals]

    ax1.bar(range(len(hist_vals)), hist_vals, color=hist_colors, alpha=0.82)
    ax1.bar(len(hist_vals), prediction, color="#3498DB", alpha=0.9,
            hatch="//", label=f"Predicted ({next_period})")
    ax1.axhline(0, color="#888", linestyle="--", linewidth=0.8)
    ax1.axvline(len(hist_vals) - 0.5, color="#888", linestyle=":", linewidth=1.2)
    ax1.set_xticks(range(len(periods_str)))
    ax1.set_xticklabels(periods_str, rotation=45, ha="right", fontsize=8)
    ax1.set_title("Full History + Next Month Prediction", fontweight="bold")
    ax1.set_ylabel("Net Cashflow ($)", color="white")
    ax1.legend(fontsize=9, labelcolor="white", facecolor="#1A1D27", framealpha=0.5)

    recent    = monthly.tail(6)
    r_periods = [str(p) for p in recent["year_month"]] + [str(next_period)]
    r_vals    = list(recent["net_cashflow"]) + [prediction]
    x_hist    = range(len(r_vals) - 1)
    x_pred    = [len(r_vals) - 2, len(r_vals) - 1]

    ax2.plot(list(x_hist), r_vals[:-1], "o-", color="#ECF0F1",
             linewidth=2, markersize=7, label="Historical")
    ax2.plot(x_pred, [r_vals[-2], r_vals[-1]], "o--", color="#3498DB",
             linewidth=2.5, markersize=10, label=f"Predicted ({next_period})")
    ax2.fill_between(list(x_hist), r_vals[:-1], alpha=0.12, color="#ECF0F1")
    ax2.axhline(0, color="#E74C3C", linestyle="--", linewidth=0.8, alpha=0.6)
    ax2.set_xticks(range(len(r_periods)))
    ax2.set_xticklabels(r_periods, rotation=45, ha="right", fontsize=9)
    ax2.set_title("Last 6 Months + Next Month Forecast", fontweight="bold")
    ax2.set_ylabel("Net Cashflow ($)", color="white")
    ax2.legend(fontsize=9, labelcolor="white", facecolor="#1A1D27", framealpha=0.5)
    ax2.grid(True, alpha=0.2, color="#888")
    ax2.annotate(
        f"${prediction:+,.0f}",
        xy=(len(r_vals) - 1, prediction),
        xytext=(len(r_vals) - 1 - 0.5, prediction + (abs(prediction) * 0.15 + 1000)),
        color="#3498DB", fontsize=10, fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#3498DB"),
    )

    plt.tight_layout()
    plt.savefig(NEXT_MONTH_PLOT, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
