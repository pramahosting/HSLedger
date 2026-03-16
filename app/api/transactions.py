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
    updated: int
    skipped: int
    total: int


class UpdateRequest(BaseModel):
    user_id: int
    transaction: TransactionIn


class TransactionOut(BaseModel):
    id: int
    date: datetime | None = None
    bank: str | None = None
    account: str | None = None
    description: str | None = None
    debit: float = 0.0
    credit: float = 0.0
    classification: str | None = None
    pair_id: str | None = None
    gl_account: str | None = None
    gst: float = 0.0
    gst_category: str | None = None
    who: str | None = None


class DeleteResponse(BaseModel):
    deleted: bool
    id: int


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
    updated = 0
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
            # Upsert: update non-key fields so GL Account, GST, etc. reflect latest UI edits.
            needs_update = (
                exists.classification != tx.classification
                or exists.pair_id != tx.pair_id
                or exists.gl_account != tx.gl_account
                or exists.gst != _to_float(tx.gst, 0.0)
                or exists.gst_category != tx.gst_category
                or exists.who != tx.who
            )
            if needs_update:
                exists.classification = tx.classification
                exists.pair_id = tx.pair_id
                exists.gl_account = tx.gl_account
                exists.gst = _to_float(tx.gst, 0.0)
                exists.gst_category = tx.gst_category
                exists.who = tx.who
                updated += 1
            else:
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
    logger.info("/transactions/save completed: saved=%s updated=%s skipped=%s total=%s", saved, updated, skipped, len(request.transactions))
    return SaveResponse(saved=saved, updated=updated, skipped=skipped, total=len(request.transactions))


@router.post("", response_model=TransactionOut)
def create_transaction(
    request: UpdateRequest,
    db: Session = Depends(get_db),
):
    logger.info("/transactions POST called: user_id=%s", request.user_id)

    user = db.query(User).filter(User.id == request.user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    tx = request.transaction
    tx_date = _to_datetime(tx.date)
    bank = (tx.bank or "").strip()
    account = (tx.account or "").strip()
    description = (tx.description or "").strip()
    debit = _to_float(tx.debit, 0.0)
    credit = _to_float(tx.credit, 0.0)

    if not tx_date or not bank or not account or not description:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date, bank, account, description are required",
        )

    exists = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == request.user_id,
            Transaction.date == tx_date,
            Transaction.bank == bank,
            Transaction.account == account,
            Transaction.description == description,
            Transaction.debit == debit,
            Transaction.credit == credit,
        )
        .first()
    )
    if exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Duplicate transaction already exists",
        )

    row = Transaction(
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
        who=tx.who,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return TransactionOut(
        id=row.id,
        date=row.date,
        bank=row.bank,
        account=row.account,
        description=row.description,
        debit=float(row.debit or 0.0),
        credit=float(row.credit or 0.0),
        classification=row.classification,
        pair_id=row.pair_id,
        gl_account=row.gl_account,
        gst=float(row.gst or 0.0),
        gst_category=row.gst_category,
        who=row.who,
    )


@router.get("/user/{user_id}", response_model=List[TransactionOut])
def get_user_transactions(
    user_id: int,
    db: Session = Depends(get_db),
):
    logger.info("/transactions/user/%s called", user_id)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    rows = (
        db.query(Transaction)
        .filter(Transaction.user_id == user_id)
        .order_by(Transaction.date.asc(), Transaction.id.asc())
        .all()
    )

    return [
        TransactionOut(
            id=row.id,
            date=row.date,
            bank=row.bank,
            account=row.account,
            description=row.description,
            debit=float(row.debit or 0.0),
            credit=float(row.credit or 0.0),
            classification=row.classification,
            pair_id=row.pair_id,
            gl_account=row.gl_account,
            gst=float(row.gst or 0.0),
            gst_category=row.gst_category,
            who=row.who,
        )
        for row in rows
    ]


@router.put("/{transaction_id}", response_model=TransactionOut)
def update_transaction(
    transaction_id: int,
    request: UpdateRequest,
    db: Session = Depends(get_db),
):
    logger.info("/transactions/%s PUT called: user_id=%s", transaction_id, request.user_id)

    user = db.query(User).filter(User.id == request.user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    row = (
        db.query(Transaction)
        .filter(Transaction.id == transaction_id, Transaction.user_id == request.user_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")

    tx = request.transaction
    tx_date = _to_datetime(tx.date)
    bank = (tx.bank or "").strip()
    account = (tx.account or "").strip()
    description = (tx.description or "").strip()
    debit = _to_float(tx.debit, 0.0)
    credit = _to_float(tx.credit, 0.0)

    if not tx_date or not bank or not account or not description:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date, bank, account, description are required",
        )

    # Prevent creating duplicate unique-key records when updating.
    duplicate = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == request.user_id,
            Transaction.id != transaction_id,
            Transaction.date == tx_date,
            Transaction.bank == bank,
            Transaction.account == account,
            Transaction.description == description,
            Transaction.debit == debit,
            Transaction.credit == credit,
        )
        .first()
    )
    if duplicate:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Update would create a duplicate transaction",
        )

    row.date = tx_date
    row.bank = bank
    row.account = account
    row.description = description
    row.debit = debit
    row.credit = credit
    row.classification = tx.classification
    row.pair_id = tx.pair_id
    row.gl_account = tx.gl_account
    row.gst = _to_float(tx.gst, 0.0)
    row.gst_category = tx.gst_category
    row.who = tx.who

    db.commit()
    db.refresh(row)

    return TransactionOut(
        id=row.id,
        date=row.date,
        bank=row.bank,
        account=row.account,
        description=row.description,
        debit=float(row.debit or 0.0),
        credit=float(row.credit or 0.0),
        classification=row.classification,
        pair_id=row.pair_id,
        gl_account=row.gl_account,
        gst=float(row.gst or 0.0),
        gst_category=row.gst_category,
        who=row.who,
    )


@router.delete("/{transaction_id}", response_model=DeleteResponse)
def delete_transaction(
    transaction_id: int,
    user_id: int,
    db: Session = Depends(get_db),
):
    logger.info("/transactions/%s DELETE called: user_id=%s", transaction_id, user_id)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    row = (
        db.query(Transaction)
        .filter(Transaction.id == transaction_id, Transaction.user_id == user_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")

    db.delete(row)
    db.commit()

    return DeleteResponse(deleted=True, id=transaction_id)