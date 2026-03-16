# HSLedger — Fullstack Setup Guide

## Prerequisites

Install all Python dependencies:

```bash
pip install -r full_requirements.txt
```

## Database Setup

This project uses **SQLite**.

Initialize the database by running:

```bash
python app/init_db.py
```

Once the database is set up, a default admin account will be created:

| Field    | Value        |
| -------- | ------------ |
| Email    | admin@ex.com |
| Password | 1            |

Use these credentials to log in.

## Running the Application

**Step 1 — Start the FastAPI backend** (Terminal 1):

```bash
uvicorn main:app --reload
```

**Step 2 — Start the Streamlit frontend** (Terminal 2):

```bash
cd streamlit_frontend
streamlit run app.py
```
