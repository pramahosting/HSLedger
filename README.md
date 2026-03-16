To start the Fullstack

Install python dependency
pip install -r full_requirements.txt

To setup db
SQLite db is used for this project
run python file in app/init_db.py
    python app/init_db.py

1. Open a new terminal
2. To start fastapi backend server:
    uvicorn main:app --reload
3.  Open Second terminal
4.  cd streamlit_frontend
5.  Once inside streamlit_frontend, run
    streamlit run app.py
