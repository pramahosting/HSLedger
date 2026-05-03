#!/bin/bash

cd "$(dirname "$0")"

if [ -f "venv/bin/activate" ]; then
    source "venv/bin/activate"
fi

if [ ! -f "reconciliation.db" ]; then
    echo "reconciliation.db not found. Running first-time database initialization..."
    python app/init_db.py
    if [ $? -ne 0 ]; then
        echo "Database initialization failed. Project startup aborted."
        exit 1
    fi
fi

SCRIPT_DIR="$(pwd)"

echo "Starting HSLedger Backend..."
python -m uvicorn main:app --reload > "$SCRIPT_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
echo "Backend PID: $BACKEND_PID (logs: backend.log)"

echo "Starting HSLedger Frontend..."
cd "$SCRIPT_DIR/streamlit_frontend" && streamlit run app.py > "$SCRIPT_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
echo "Frontend PID: $FRONTEND_PID (logs: frontend.log)"

echo ""
echo "HSLedger backend and frontend are running in the background."
echo "  Stop backend:  kill $BACKEND_PID"
echo "  Stop frontend: kill $FRONTEND_PID"
echo "  View logs:     tail -f backend.log  |  tail -f frontend.log"
