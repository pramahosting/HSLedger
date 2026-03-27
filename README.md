# HSLedger

Full-stack bookkeeping and reconciliation app with:

- FastAPI backend
- Streamlit frontend
- SQLite database

## Prerequisites

Install dependencies from the project root:

```bash
pip install -r requirements.txt
```

## Quick Start (Windows)

Run this from the project root:

```bash
start_project.cmd
```

What this script does:

1. Activates local virtual environment (if available)
2. Checks for `reconciliation.db`
3. Runs `python app/init_db.py` automatically if DB is missing (first run)
4. Starts backend and frontend in separate terminal windows

## Default Login (First Run)

When the database is initialized for the first time, a default admin account is created:

| Field             | Value                 |
| ----------------- | --------------------- |
| Email or Username | admin@ex.com or admin |
| Password          | 1                     |

## Role-Based Access

- `ML_Classifier` is visible only to admin users.
- `Business Settings` inside the Invoice module is visible only to admin users.
- Non-admin users cannot see these sections in the sidebar/tabs.

## Manual Run (Without Script)

### 1. Initialize database (only if needed)

```bash
python app/init_db.py
```

### 2. Start backend (Terminal 1)

```bash
uvicorn main:app --reload
```

### 3. Start frontend (Terminal 2)

```bash
cd streamlit_frontend
streamlit run app.py
```

## Notes

- The app uses SQLite file `reconciliation.db` in the project root.
- If you delete the DB file, running `start_project.cmd` will recreate it.
