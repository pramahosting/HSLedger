from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.transaction import Transaction
from app.models.user import User
from pydantic import BaseModel
from typing import List, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

class TransactionIn(BaseModel):

    date: Any = None
    bank: str | None = None
    account: str | None = None
    description: str | None = None
    debit: float | int | str | None = 0
    credit: float | int | str | None = 0
    classification: str | None = None
    pair_id: str | None = None
    gl_account: str | None = None
    gst: float | int | str | None = None
    gst_category: str | None = None
    who: str | None = None

class SaveRequest(BaseModel):
    user_id: int
    transactions: List[TransactionIn]

class SaveResponse(BaseModel):
    saved: int
    skipped: int
    total: int


def _to_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value

    text = str(value).strip()
    if not text:
        return None

    # Normalize trailing Z to UTC offset for fromisoformat.
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    return None


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace(",", "")
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default

@router.post("/save", response_model=SaveResponse)
def save_transactions(
    request: SaveRequest,
    db: Session = Depends(get_db),
):
    logger.info("/transactions/save called: user_id=%s, rows=%s", request.user_id, len(request.transactions))
    user = db.query(User).filter(User.id == request.user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    saved = 0
    skipped = 0
    seen_in_request: set[tuple] = set()

    for tx in request.transactions:
        tx_date = _to_datetime(tx.date)
        bank = (tx.bank or "").strip()
        account = (tx.account or "").strip()
        description = (tx.description or "").strip()
        debit = _to_float(tx.debit, 0.0)
        credit = _to_float(tx.credit, 0.0)

        # Skip malformed rows instead of failing entire request with 422.
        if not tx_date or not bank or not account or not description:
            skipped += 1
            continue

        # Avoid duplicate inserts from repeated rows in the same payload.
        tx_key = (
            request.user_id,
            tx_date,
            bank,
            account,
            description,
            debit,
            credit,
        )
        if tx_key in seen_in_request:
            skipped += 1
            continue
        seen_in_request.add(tx_key)

        exists = db.query(Transaction).filter(
            Transaction.user_id == request.user_id,
            Transaction.date == tx_date,
            Transaction.bank == bank,
            Transaction.account == account,
            Transaction.description == description,
            Transaction.debit == debit,
            Transaction.credit == credit
        ).first()

        if exists:
            skipped += 1
            continue

        db.add(Transaction(
            user_id=request.user_id,
            date=tx_date,
            bank=bank,
            account=account,
            description=description,
            debit=debit,
            credit=credit,
            classification=tx.classification,
            pair_id=tx.pair_id,
            gl_account=tx.gl_account,
            gst=_to_float(tx.gst, 0.0),
            gst_category=tx.gst_category,
            who=tx.who
        ))
        saved += 1

    db.commit()
    logger.info("/transactions/save completed: saved=%s skipped=%s total=%s", saved, skipped, len(request.transactions))
    return SaveResponse(saved=saved, skipped=skipped, total=len(request.transactions))