from sqlalchemy import Column, Integer, String, Float, DateTime
from datetime import datetime
from .base import Base


class ManualCryptoLot(Base):
    __tablename__ = "manual_crypto_lots"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    user_id          = Column(Integer, nullable=False, index=True)
    financial_year   = Column(String(10), nullable=False)   # e.g. "2024-25"
    asset            = Column(String(20), nullable=False)   # e.g. "BTC"
    acquisition_date = Column(String(10), nullable=False)   # "dd/mm/yyyy"
    qty              = Column(Float, nullable=False)
    total_cost_aud   = Column(Float, nullable=False)
    fee_aud          = Column(Float, default=0.0)
    crypto_batch_id  = Column(String(36), nullable=True, index=True)  # UUID per upload session
    created_at       = Column(DateTime, default=datetime.utcnow)
