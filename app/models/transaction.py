from sqlalchemy import Column, Integer, String, Float, ForeignKey, TIMESTAMP, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from .base import Base

class Transaction(Base):
    __tablename__ = 'transactions'

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)

    date = Column(TIMESTAMP, nullable=False)
    bank = Column(String(100), nullable=False)
    account = Column(String(100), nullable=False)
    description = Column(String(255), nullable=False)
    debit = Column(Float, default=0.0)
    credit = Column(Float, default=0.0)
    classification = Column(String(100), nullable=True)
    pair_id = Column(String(100), nullable=True)
    gl_account = Column(String(100), nullable=True)
    gst = Column(Float, default=0.0)
    gst_category = Column(String(100), nullable=True)
    who = Column(String(100), nullable=True)

    uploaded_at = Column(TIMESTAMP, default=datetime.now)

    user = relationship("User", back_populates="transactions")

    __table_args__ = (
        UniqueConstraint('user_id', 'date', 'bank', 'account', 'description', 'debit', 'credit', name='uq_transaction'),
    )