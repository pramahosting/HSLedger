@echo off
setlocal

cd /d "%~dp0"

if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
)

if not exist "reconciliation.db" (
    echo reconciliation.db not found. Running first-time database initialization...
    python app\init_db.py
    if errorlevel 1 (
        echo Database initialization failed. Project startup aborted.
        endlocal
        exit /b 1
    )
)
start "HSLedger Backend" cmd /k "cd /d ""%~dp0"" && python -m uvicorn main:app --reload"
start "HSLedger Frontend" cmd /k "cd /d ""%~dp0streamlit_frontend"" && streamlit run app.py"

echo HSLedger backend and frontend are starting in separate windows.
endlocal