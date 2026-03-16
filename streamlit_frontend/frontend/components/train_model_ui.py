import pandas as pd
import streamlit as st

from backend.transaction_classifier.train_model import DEFAULT_MODEL_DIR, train_from_df

_REQUIRED_COLUMNS = {"category", "gst_category"}
_TEXT_COLUMNS = {"description", "transaction_description"}
_SAMPLE_CSV = """date,description,amount,category,gst_category
15/09/2025,BUNNINGS,65.38,Expense,GST on Expenses
16/08/2025,CLIENT PAYMENT ABC PTY,339.55,Revenue,GST on Income
19/08/2025,AMAZON,97.98,Expense,GST on Expenses
1/10/2025,SUPPLIER DIRECT COST,566.31,Direct Costs,GST on Expenses
"""


def _validate_columns(df: pd.DataFrame) -> list[str]:
    errors = []
    if not _TEXT_COLUMNS.intersection(df.columns):
        errors.append("Missing text column: need 'description' or 'transaction_description'")
    for col in _REQUIRED_COLUMNS:
        if col not in df.columns:
            errors.append(f"Missing required column: '{col}'")
    return errors


def render():
    st.header("Train Classification Models")
    st.markdown(
        "Upload a labelled CSV to retrain the **GL Account** and **GST Category** classifiers. "
        "The trained models are saved to `classifier_model/` and used immediately by the "
        "**Classify GL/GST** button in the Reconciliation view."
    )

    # --- Required columns info ---
    with st.expander("Required CSV columns", expanded=False):
        st.markdown(
            "| Column | Required | Notes |\n"
            "|---|---|---|\n"
            "| `description` **or** `transaction_description` | ✅ | Transaction description text |\n"
            "| `category` | ✅ | GL account / category label |\n"
            "| `gst_category` | ✅ | GST category label |\n"
            "| `date` | optional | Transaction date (ignored during training) |\n"
            "| `amount` | optional | Transaction amount (ignored during training) |\n"
        )
        st.download_button(
            "Download sample CSV",
            data=_SAMPLE_CSV,
            file_name="sample_training_data.csv",
            mime="text/csv",
        )

    # --- File upload ---
    uploaded_file = st.file_uploader("Upload training CSV", type=["csv"], key="train_model_upload")

    if uploaded_file is None:
        st.info("Upload a CSV file to get started.")
        return

    # --- Load & validate ---
    try:
        df = pd.read_csv(uploaded_file)
    except Exception as e:
        st.error(f"Could not read CSV: {e}")
        return

    df.columns = [c.strip().lower() for c in df.columns]
    errors = _validate_columns(df)

    st.subheader("Preview")
    st.dataframe(df.head(10), use_container_width=True)
    st.caption(f"{len(df):,} rows × {len(df.columns)} columns")

    if errors:
        for err in errors:
            st.error(err)
        return

    # --- Column summary ---
    text_col = "description" if "description" in df.columns else "transaction_description"
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total rows", f"{len(df):,}")
    with col2:
        n_cat = df["category"].nunique()
        st.metric("Unique GL categories", n_cat)
    with col3:
        n_gst = df["gst_category"].nunique()
        st.metric("Unique GST categories", n_gst)

    # Label distribution
    with st.expander("Label distribution", expanded=False):
        dist_col1, dist_col2 = st.columns(2)
        with dist_col1:
            st.markdown("**GL Account (category)**")
            st.dataframe(
                df["category"].value_counts().rename_axis("Label").reset_index(name="Count"),
                use_container_width=True,
                hide_index=True,
            )
        with dist_col2:
            st.markdown("**GST Category**")
            st.dataframe(
                df["gst_category"].value_counts().rename_axis("Label").reset_index(name="Count"),
                use_container_width=True,
                hide_index=True,
            )

    st.divider()

    # --- Train button ---
    if st.button("🚀 Start Training", type="primary", use_container_width=True, key="train_model_btn"):
        with st.spinner("Training models — this may take a moment..."):
            try:
                metrics = train_from_df(df, model_dir=DEFAULT_MODEL_DIR)
                st.session_state["train_model_metrics"] = metrics
            except Exception as e:
                st.session_state.pop("train_model_metrics", None)
                st.error(f"Training failed: {e}")

    # --- Results (persist across reruns) ---
    metrics = st.session_state.get("train_model_metrics")
    if metrics:
        st.success("✅ Models trained and saved successfully!")

        res_col1, res_col2 = st.columns(2)
        with res_col1:
            st.metric("Category Accuracy", f"{metrics['category_accuracy']:.2%}")
        with res_col2:
            st.metric("GST Accuracy", f"{metrics['gst_accuracy']:.2%}")

        st.caption(
            f"Trained on {metrics['train_rows']:,} rows · "
            f"Evaluated on {metrics['test_rows']:,} rows"
        )

        with st.expander("Category classification report"):
            st.code(metrics["category_report"], language=None)

        with st.expander("GST classification report"):
            st.code(metrics["gst_report"], language=None)

        st.markdown(
            f"**Saved to:**\n"
            f"- `{metrics['cat_path']}`\n"
            f"- `{metrics['gst_path']}`"
        )
