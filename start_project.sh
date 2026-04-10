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

gnome-terminal --title="HSLedger Backend" -- bash -c "cd '$(pwd)' && python -m uvicorn main:app --reload; exec bash" &
gnome-terminal --title="HSLedger Frontend" -- bash -c "cd '$(pwd)/streamlit_frontend' && streamlit run app.py; exec bash" &

echo "HSLedger backend and frontend are starting in separate windows."
