"""
trading.py — per-user Shares Trading Taxation API routes.

Security rule enforced on every route:
  user_id is derived exclusively from the validated auth token via get_current_user.
  It is NEVER read from the request body, path parameters, or query string.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.manual_purchase_lot import ManualPurchaseLot
from app.models.tax_report import TaxReport
from app.models.user import User

router = APIRouter()


# ── Request / Response schemas ────────────────────────────────────────────────

class LotCreate(BaseModel):
    financial_year: str
    ticker:         str
    purchase_date:  str       # "dd/mm/yyyy" — matches the UI format
    qty:            float
    unit_price:     float
    brokerage:      float = 0.0
    gst:            float = 0.0


class LotResponse(BaseModel):
    id:             int
    financial_year: str
    ticker:         str
    purchase_date:  str
    qty:            float
    unit_price:     float
    brokerage:      float
    gst:            float
    created_at:     datetime

    class Config:
        from_attributes = True


class ReportCreate(BaseModel):
    financial_year:       str
    net_taxable_gain:     float
    gross_capital_gains:  float
    gross_capital_losses: float
    cgt_discount_applied: float
    report_json:          Optional[str] = None


class ReportResponse(BaseModel):
    id:                   int
    financial_year:       str
    net_taxable_gain:     float
    gross_capital_gains:  float
    gross_capital_losses: float
    cgt_discount_applied: float
    created_at:           datetime

    class Config:
        from_attributes = True


# ── Purchase lot routes ───────────────────────────────────────────────────────

@router.post("/lots", response_model=LotResponse, status_code=status.HTTP_201_CREATED)
def save_lot(
    body:         LotCreate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    lot = ManualPurchaseLot(
        user_id        = current_user.id,   # from auth token — never from body
        financial_year = body.financial_year,
        ticker         = body.ticker,
        purchase_date  = body.purchase_date,
        qty            = body.qty,
        unit_price     = body.unit_price,
        brokerage      = body.brokerage,
        gst            = body.gst,
    )
    db.add(lot)
    db.commit()
    db.refresh(lot)
    return lot


@router.get("/lots", response_model=list[LotResponse])
def get_lots(
    financial_year: str           = Query(...),
    ticker:         Optional[str] = Query(None),
    current_user:   User          = Depends(get_current_user),
    db:             Session       = Depends(get_db),
):
    q = db.query(ManualPurchaseLot).filter(
        ManualPurchaseLot.user_id        == current_user.id,
        ManualPurchaseLot.financial_year == financial_year,
    )
    if ticker:
        q = q.filter(ManualPurchaseLot.ticker == ticker)
    return q.order_by(ManualPurchaseLot.ticker, ManualPurchaseLot.purchase_date).all()


@router.delete("/lots/{lot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lot(
    lot_id:       int,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    # Filter by BOTH id AND user_id so a user cannot delete another user's lot
    # by guessing a lot_id, even if they send a valid token.
    lot = db.query(ManualPurchaseLot).filter(
        ManualPurchaseLot.id      == lot_id,
        ManualPurchaseLot.user_id == current_user.id,
    ).first()
    if not lot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lot not found")
    db.delete(lot)
    db.commit()


# ── Tax report routes ─────────────────────────────────────────────────────────

@router.post("/reports", response_model=ReportResponse, status_code=status.HTTP_201_CREATED)
def save_report(
    body:         ReportCreate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    report = TaxReport(
        user_id              = current_user.id,   # from auth token — never from body
        financial_year       = body.financial_year,
        net_taxable_gain     = body.net_taxable_gain,
        gross_capital_gains  = body.gross_capital_gains,
        gross_capital_losses = body.gross_capital_losses,
        cgt_discount_applied = body.cgt_discount_applied,
        report_json          = body.report_json,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


@router.get("/reports", response_model=list[ReportResponse])
def list_reports(
    financial_year: str     = Query(...),
    current_user:   User    = Depends(get_current_user),
    db:             Session = Depends(get_db),
):
    return (
        db.query(TaxReport)
        .filter(
            TaxReport.user_id        == current_user.id,
            TaxReport.financial_year == financial_year,
        )
        .order_by(TaxReport.created_at.desc())
        .all()
    )
