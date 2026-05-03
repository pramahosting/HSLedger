from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime, timedelta
from .base import Base

# Token lifetime — one week; adjust as needed.
TOKEN_TTL_HOURS = 24 * 7


class SessionToken(Base):
    __tablename__ = "sessions"

    token      = Column(String(36), primary_key=True)
    user_id    = Column(Integer, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)

    @staticmethod
    def make_expiry() -> datetime:
        return datetime.utcnow() + timedelta(hours=TOKEN_TTL_HOURS)
