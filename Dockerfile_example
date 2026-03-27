FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

EXPOSE 8000 8501

CMD ["sh", "-c", "if [ \"${APP_MODE:-backend}\" = \"frontend\" ]; then cd streamlit_frontend && streamlit run app.py --server.address=0.0.0.0 --server.port=8501; else if [ ! -f reconciliation.db ]; then python app/init_db.py; fi; uvicorn main:app --host 0.0.0.0 --port=8000; fi"]