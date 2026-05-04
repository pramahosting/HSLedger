from sqlalchemy import Column, Integer, String, Float, DateTime
from datetime import datetime
from .base import Base


class ManualPurchaseLot(Base):
    __tablename__ = "manual_purchase_lots"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    user_id          = Column(Integer, nullable=False, index=True)
    financial_year   = Column(String(10), nullable=False)   # e.g. "2024-25"
    ticker           = Column(String(20), nullable=False)
    purchase_date    = Column(String(10), nullable=False)   # "dd/mm/yyyy" — matches UI format
    qty              = Column(Float, nullable=False)
    unit_price       = Column(Float, nullable=False)
    brokerage        = Column(Float, default=0.0)
    gst              = Column(Float, default=0.0)
    trading_batch_id = Column(String(36), nullable=True, index=True)  # UUID per upload session
    created_at       = Column(DateTime, default=datetime.utcnow)
