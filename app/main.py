from fastapi import FastAPI
from app.api import auth
from app.api import invoice
from app.api import transactions

app = FastAPI()

# AUTH
app.include_router(auth.router, prefix="/auth", tags=["auth"])

app.include_router(transactions.router, prefix="/transactions", tags=["transactions"])
app.include_router(invoice.router, prefix="/invoice", tags=["invoice"])