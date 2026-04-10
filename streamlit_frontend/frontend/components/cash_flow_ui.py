import io
import streamlit as st
import pandas as pd

from backend.cash_flow.pipeline import (
    auto_detect_columns,
    preprocess,
    validate_date_span,
    monthly_features,
    train_leaderboard,
    predict_next_month,
    LEADERBOARD_PLOT,
    NEXT_MONTH_PLOT,
    LEADERBOARD_CSV,
    NEXT_MONTH_CSV,
)


def render():
    st.markdown(
        "<h3 style='margin-top:0rem;margin-bottom:0rem'>💰 Cash Flow Forecast</h3>",
        unsafe_allow_html=True,
    )

    # ── Session state init ────────────────────────────────────────────────────
    for key in (
        "cf_col_map", "cf_df_raw", "cf_df_proc", "cf_monthly",
        "cf_lb", "cf_trained_models", "cf_feature_cols", "cf_data",
        "cf_pred_result",
    ):
        if key not in st.session_state:
            st.session_state[key] = None

    # ── Step 1: Upload ────────────────────────────────────────────────────────
    st.markdown("#### 1. Upload transaction CSV(s)")
    uploaded_files = st.file_uploader(
        "Upload one or more CSV files (minimum 12 months of data recommended)",
        type=["csv"],
        accept_multiple_files=True,
        key="cf_uploader",
    )

    if not uploaded_files:
        st.info("Upload transaction CSV file(s) to begin.")
        return

    # Load and combine
    dfs = []
    for f in uploaded_files:
        try:
            df = pd.read_csv(f)
            df["_source_file"] = f.name
            dfs.append(df)
        except Exception as e:
            st.error(f"Could not read {f.name}: {e}")

    if not dfs:
        return

    df_raw = pd.concat(dfs, ignore_index=True)
    st.caption(f"Loaded {len(df_raw):,} rows from {len(dfs)} file(s).")

    # ── Step 2: Column mapping ────────────────────────────────────────────────
    st.markdown("#### 2. Column mapping")
    detected = auto_detect_columns(df_raw)
    all_cols = [c for c in df_raw.columns if not c.startswith("_")]
    col_options = ["(none)"] + all_cols

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        date_col = st.selectbox(
            "Date *", col_options,
            index=col_options.index(detected["date"]) if detected["date"] in col_options else 0,
        )
    with col2:
        debit_col = st.selectbox(
            "Debit *", col_options,
            index=col_options.index(detected["debit"]) if detected["debit"] in col_options else 0,
        )
    with col3:
        credit_col = st.selectbox(
            "Credit *", col_options,
            index=col_options.index(detected["credit"]) if detected["credit"] in col_options else 0,
        )
    with col4:
        balance_col = st.selectbox(
            "Balance", col_options,
            index=col_options.index(detected["balance"]) if detected["balance"] in col_options else 0,
        )
    with col5:
        desc_col = st.selectbox(
            "Description", col_options,
            index=col_options.index(detected["desc"]) if detected["desc"] in col_options else 0,
        )

    col_map = {
        "date":    date_col    if date_col    != "(none)" else None,
        "debit":   debit_col   if debit_col   != "(none)" else None,
        "credit":  credit_col  if credit_col  != "(none)" else None,
        "balance": balance_col if balance_col != "(none)" else None,
        "desc":    desc_col    if desc_col    != "(none)" else None,
    }

    missing = [r for r in ("date", "debit", "credit") if not col_map[r]]
    if missing:
        st.warning(f"Required columns not mapped: {', '.join(missing)}")

    # ── Step 3: Run pipeline ──────────────────────────────────────────────────
    st.markdown("#### 3. Train models")
    run_btn = st.button("▶ Run Pipeline", disabled=bool(missing), type="primary")

    if run_btn:
        with st.spinner("Preprocessing data..."):
            try:
                df_proc, transform_meta, outlier_bounds = preprocess(df_raw, col_map)
            except Exception as e:
                st.error(f"Preprocessing failed: {e}")
                return

        months_span, d_min, d_max = validate_date_span(df_proc)
        if months_span < 12:
            st.warning(
                f"Data spans only {months_span} month(s) ({d_min} → {d_max}). "
                "Minimum 12 months recommended — continuing anyway."
            )
        else:
            st.success(f"Date range: {d_min} → {d_max} ({months_span} months)")

        with st.spinner("Building monthly features..."):
            monthly = monthly_features(df_proc)

        with st.spinner("Training 17 models (this may take a minute)..."):
            try:
                lb, trained_models, feature_cols, data = train_leaderboard(monthly)
            except Exception as e:
                st.error(f"Training failed: {e}")
                return

        st.session_state.cf_df_raw        = df_raw
        st.session_state.cf_df_proc       = df_proc
        st.session_state.cf_monthly       = monthly
        st.session_state.cf_lb            = lb
        st.session_state.cf_trained_models = trained_models
        st.session_state.cf_feature_cols  = feature_cols
        st.session_state.cf_data          = data
        st.session_state.cf_pred_result   = None
        st.rerun()

    # ── Show leaderboard plot + model selector ────────────────────────────────
    if st.session_state.cf_lb is not None:
        st.markdown("#### 4. Model leaderboard")

        if LEADERBOARD_PLOT.exists():
            st.image(str(LEADERBOARD_PLOT), use_container_width=True)

        lb = st.session_state.cf_lb
        with open(LEADERBOARD_CSV, "rb") as f:
            st.download_button(
                "⬇ Download leaderboard.csv",
                data=f.read(),
                file_name="leaderboard.csv",
                mime="text/csv",
            )

        st.markdown("#### 5. Choose model & predict")
        model_names = lb["model"].tolist()
        best_model  = model_names[0]

        chosen = st.selectbox(
            "Select model for next-month prediction",
            model_names,
            index=0,
            help=f"Top recommendation by CV RMSE: {best_model}",
        )

        predict_btn = st.button("📈 Predict Next Month", type="primary")

        if predict_btn:
            with st.spinner(f"Running prediction with {chosen}..."):
                try:
                    pred = predict_next_month(
                        chosen,
                        st.session_state.cf_trained_models,
                        st.session_state.cf_feature_cols,
                        st.session_state.cf_monthly,
                        st.session_state.cf_data,
                    )
                    st.session_state.cf_pred_result = pred
                except Exception as e:
                    st.error(f"Prediction failed: {e}")
            st.rerun()

    # ── Show forecast ─────────────────────────────────────────────────────────
    if st.session_state.cf_pred_result is not None:
        pred = st.session_state.cf_pred_result
        net  = pred["predicted_net_cashflow"]

        st.markdown("#### 6. Forecast")

        # Summary metric cards
        m1, m2, m3 = st.columns(3)
        with m1:
            st.metric("Predicted Net Cashflow", f"${net:+,.2f}")
        with m2:
            st.metric("Estimated Income", f"${pred['estimated_income']:,.2f}")
        with m3:
            st.metric("Estimated Expense", f"${pred['estimated_expense']:,.2f}")

        trend = pred["avg_last_3m_net"]
        if net > trend * 1.1:
            st.success("Above recent trend — stronger month ahead.")
        elif net < trend * 0.9:
            st.warning("Below recent trend — softer month ahead.")
        else:
            st.info("In line with recent trend.")

        if NEXT_MONTH_PLOT.exists():
            st.image(str(NEXT_MONTH_PLOT), use_container_width=True)

        with open(NEXT_MONTH_CSV, "rb") as f:
            st.download_button(
                "⬇ Download next_month_prediction.csv",
                data=f.read(),
                file_name="next_month_prediction.csv",
                mime="text/csv",
            )
