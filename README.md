# HSLedger — Fullstack Setup Guide

## Prerequisites

Install all Python dependencies:

```bash
pip install -r requirements.txt
```

## Database Setup

This project uses **SQLite**.

Initialize the database by running:

```bash
python app/init_db.py
```

Once the database is set up, a default admin account will be created:

| Field             | Value                 |
| ----------------- | --------------------- |
| Email or Username | admin@ex.com or admin |
| Password          | 1                     |

Use these credentials to log in.

## Running the Application

### One-command start (Windows)

From the project root, run:

```bash
start_project.cmd
```

This opens two terminal windows automatically:

- FastAPI backend
- Streamlit frontend

**Step 1 — Start the FastAPI backend** (Terminal 1):

```bash
uvicorn main:app --reload
```

**Step 2 — Start the Streamlit frontend** (Terminal 2):

```bash
cd streamlit_frontend
streamlit run app.py
```
