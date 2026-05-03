from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from datetime import datetime
from .base import Base


class TaxReport(Base):
    __tablename__ = "tax_reports"

    id                   = Column(Integer, primary_key=True, autoincrement=True)
    user_id              = Column(Integer, nullable=False, index=True)
    financial_year       = Column(String(10), nullable=False)   # e.g. "2024-25"
    net_taxable_gain     = Column(Float, default=0.0)
    gross_capital_gains  = Column(Float, default=0.0)
    gross_capital_losses = Column(Float, default=0.0)
    cgt_discount_applied = Column(Float, default=0.0)
    report_json          = Column(Text, nullable=True)          # full serialised report
    created_at           = Column(DateTime, default=datetime.utcnow)
