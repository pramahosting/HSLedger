"""
Invoice Extractor UI
Streamlit module for processing PDFs and images into structured
bank-statement, invoice, and receipt data.
"""

import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

# Load .env from the streamlit_frontend directory
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from backend.invoice_extractor.core import process_files, get_dependency_status, IMG_EXTS, PDF_EXTS

ACCEPTED_EXTENSIONS = list(IMG_EXTS | PDF_EXTS)
# Strip leading dots for Streamlit's file_uploader
ACCEPTED_TYPES = [ext.lstrip(".") for ext in ACCEPTED_EXTENSIONS]


def _settings_expander():
    """Return (tesseract_cmd, poppler_bin) from an inline expander."""
    with st.expander("OCR Settings", expanded=False):
        st.caption(
            "Required only for scanned PDFs / images. "
            "Leave blank if Tesseract/Poppler are on your system PATH."
        )
        default_tess = os.environ.get(
            "TESSERACT_CMD",
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        )
        default_pop = os.environ.get("POPPLER_BIN", "")
        tesseract_cmd = st.text_input(
            "Tesseract executable path",
            value=default_tess,
            key="ie_tesseract_cmd",
        )
        poppler_bin = st.text_input(
            "Poppler bin directory",
            value=default_pop,
            key="ie_poppler_bin",
        )
    return tesseract_cmd or None, poppler_bin or None


def _render_bank_results(bank_results, bank_excel: bytes):
    st.subheader("Bank Statements")
    if not bank_results:
        st.info("No bank statements detected.")
        return

    all_txns = []
    for result, source_file in bank_results:
        meta = result["meta"]
        for txn in result["transactions"]:
            all_txns.append({
                "Bank": meta["bank"].upper() if meta["bank"] != "unknown" else "UNKNOWN",
                "Account Type": meta["account_type"].replace("_", " ").title(),
                "Period": (
                    f"{meta.get('period_start', '')} – {meta.get('period_end', '')}"
                    if meta.get("period_start") else ""
                ),
                "Date": txn.get("date", ""),
                "Description": txn.get("description", ""),
                "Reference": txn.get("reference", ""),
                "Debit": txn.get("debit"),
                "Credit": txn.get("credit"),
                "Balance": txn.get("balance"),
                "Source File": source_file,
            })

    import pandas as pd

    df = pd.DataFrame(all_txns)
    st.metric("Total transactions", len(df))

    banks = sorted(df["Bank"].unique().tolist())
    selected_bank = st.selectbox("Filter by bank", ["All"] + banks, key="ie_bank_filter")
    if selected_bank != "All":
        df = df[df["Bank"] == selected_bank]

    st.dataframe(df, use_container_width=True, height=400)

    if bank_excel:
        st.download_button(
            label="Download Bank Statements (Excel)",
            data=bank_excel,
            file_name="bank_statements_output.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="ie_bank_dl",
        )


def _render_inv_rec_results(inv_rec_df, inv_rec_excel: bytes, inv_rec_csv: str):
    st.subheader("Invoices & Receipts")
    if inv_rec_df is None or inv_rec_df.empty:
        st.info("No invoices or receipts detected.")
        return

    df = inv_rec_df

    col1, col2, col3 = st.columns(3)
    col1.metric("Total rows", len(df))
    col2.metric("Invoices", int((df["Document Type"] == "Invoice").sum()))
    col3.metric("Receipts", int((df["Document Type"] == "Receipt").sum()))

    doc_types = sorted(df["Document Type"].unique().tolist())
    selected_type = st.selectbox(
        "Filter by document type", ["All"] + doc_types, key="ie_doctype_filter"
    )
    if selected_type != "All":
        df = df[df["Document Type"] == selected_type]

    st.dataframe(df, use_container_width=True, height=400)

    dl_col1, dl_col2 = st.columns(2)
    if inv_rec_excel:
        dl_col1.download_button(
            label="Download Excel",
            data=inv_rec_excel,
            file_name="invoices_receipts_output.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="ie_inv_excel_dl",
        )
    if inv_rec_csv:
        dl_col2.download_button(
            label="Download CSV",
            data=inv_rec_csv,
            file_name="invoices_receipts_output.csv",
            mime="text/csv",
            key="ie_inv_csv_dl",
        )


def _show_dependency_warnings():
    deps = get_dependency_status()
    broken = [(name, info["error"]) for name, info in deps.items() if not info["available"]]
    if broken:
        with st.expander("Dependency warnings", expanded=True):
            for name, err in broken:
                st.warning(f"**{name}** failed to import: `{err}`")
            st.caption(
                "Scanned PDFs and images require cv2, pdf2image, and pytesseract. "
                "Digital PDFs work without them. Excel export requires openpyxl."
            )


def render():
    st.title("Invoice Extractor")
    st.caption(
        "Upload bank statements, invoices, and receipts (PDF or image). "
        "The extractor auto-classifies each document and outputs structured data."
    )

    _show_dependency_warnings()

    tesseract_cmd, poppler_bin = _settings_expander()

    # Session state: file store and results
    if "ie_files" not in st.session_state:
        st.session_state["ie_files"] = {}  # name -> {size, data}
    if "ie_uploader_key" not in st.session_state:
        st.session_state["ie_uploader_key"] = 0
    if "ie_results" not in st.session_state:
        st.session_state["ie_results"] = None

    uploader_key = f"ie_uploader_{st.session_state['ie_uploader_key']}"

    uploaded = st.file_uploader(
        "Upload files (PDF, JPG, PNG, TIFF…)",
        type=ACCEPTED_TYPES,
        accept_multiple_files=True,
        key=uploader_key,
    )

    # Merge new uploads into session state, then reset the uploader widget
    if uploaded:
        for f in uploaded:
            if f.name not in st.session_state["ie_files"]:
                st.session_state["ie_files"][f.name] = {"size": f.size, "data": f.read()}
        st.session_state["ie_uploader_key"] += 1
        st.rerun()

    files = st.session_state["ie_files"]

    if not files:
        st.info("Upload one or more files to get started.")
        return

    # Uploaded files list with per-file delete
    st.write(f"**Uploaded Files ({len(files)}):**")
    for name, info in list(files.items()):
        col_name, col_size, col_del = st.columns([5, 2, 1])
        col_name.write(name)
        col_size.caption(f"{info['size']:,} bytes")
        if col_del.button("✕", key=f"ie_del_{name}", help=f"Remove {name}"):
            del st.session_state["ie_files"][name]
            st.session_state["ie_results"] = None
            st.rerun()

    if st.button("Process Files", type="primary", key="ie_process_btn"):
        file_list = [(name, info["data"]) for name, info in files.items()]

        progress_bar = st.progress(0, text="Starting…")

        def on_progress(idx, total, name):
            pct = int((idx / total) * 100) if total else 0
            progress_bar.progress(pct, text=f"Processing {name}…")

        with st.spinner("Extracting data…"):
            result = process_files(
                file_list,
                tesseract_cmd=tesseract_cmd,
                poppler_bin=poppler_bin,
                progress_callback=on_progress,
            )

        progress_bar.progress(100, text="Done")
        st.session_state["ie_results"] = result

    # Persist results across reruns
    if st.session_state["ie_results"] is not None:
        result = st.session_state["ie_results"]
        summary = result["summary"]

        st.success(
            f"Processed {summary['total']} file(s): "
            f"{summary['invoices']} invoices, "
            f"{summary['receipts']} receipts, "
            f"{summary['bank_txns']} bank transactions"
            + (f", {summary['errors']} error(s)" if summary["errors"] else "")
        )

        tab_bank, tab_inv = st.tabs(["Bank Statements", "Invoices & Receipts"])
        with tab_bank:
            _render_bank_results(result["bank_results"], result["bank_excel"])
        with tab_inv:
            _render_inv_rec_results(
                result["inv_rec_df"], result["inv_rec_excel"], result["inv_rec_csv"]
            )

    st.divider()
    if st.button("Delete All", key="ie_clear_btn"):
        st.session_state["ie_files"] = {}
        st.session_state["ie_results"] = None
        st.session_state["ie_uploader_key"] += 1
        st.rerun()
