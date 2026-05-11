from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api import auth
from app.api import invoice
from app.api import transactions
from app.api import trading


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import SessionLocal, engine
    from app.models.base import Base
    from app.init_db import _migrate_add_trading_batch_id, _migrate_add_report_type, _migrate_create_manual_crypto_lots
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        _migrate_add_trading_batch_id(db)
        _migrate_add_report_type(db)
        _migrate_create_manual_crypto_lots(db)
    finally:
        db.close()
    yield


app = FastAPI(lifespan=lifespan)

app.include_router(auth.router,         prefix="/auth",         tags=["auth"])
app.include_router(transactions.router, prefix="/transactions", tags=["transactions"])
app.include_router(invoice.router,      prefix="/invoice",      tags=["invoice"])
app.include_router(trading.router,      prefix="/trading",      tags=["trading"])