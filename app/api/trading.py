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
from app.models.manual_crypto_lot import ManualCryptoLot
from app.models.manual_purchase_lot import ManualPurchaseLot
from app.models.tax_report import TaxReport
from app.models.user import User

router = APIRouter()


# ── Request / Response schemas ────────────────────────────────────────────────

class LotCreate(BaseModel):
    financial_year:   str
    ticker:           str
    purchase_date:    str       # "dd/mm/yyyy" — matches the UI format
    qty:              float
    unit_price:       float
    brokerage:        float = 0.0
    gst:              float = 0.0
    trading_batch_id: Optional[str] = None   # UUID scoping this lot to one upload session


class LotUpdate(BaseModel):
    purchase_date: Optional[str]   = None
    qty:           Optional[float] = None
    unit_price:    Optional[float] = None
    brokerage:     Optional[float] = None
    gst:           Optional[float] = None


class LotResponse(BaseModel):
    id:               int
    financial_year:   str
    ticker:           str
    purchase_date:    str
    qty:              float
    unit_price:       float
    brokerage:        float
    gst:              float
    trading_batch_id: Optional[str]
    created_at:       datetime

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
        user_id          = current_user.id,   # from auth token — never from body
        financial_year   = body.financial_year,
        ticker           = body.ticker,
        purchase_date    = body.purchase_date,
        qty              = body.qty,
        unit_price       = body.unit_price,
        brokerage        = body.brokerage,
        gst              = body.gst,
        trading_batch_id = body.trading_batch_id,
    )
    db.add(lot)
    db.commit()
    db.refresh(lot)
    return lot


@router.get("/lots", response_model=list[LotResponse])
def get_lots(
    financial_year:   str           = Query(...),
    ticker:           Optional[str] = Query(None),
    trading_batch_id: Optional[str] = Query(None),
    current_user:     User          = Depends(get_current_user),
    db:               Session       = Depends(get_db),
):
    q = db.query(ManualPurchaseLot).filter(
        ManualPurchaseLot.user_id        == current_user.id,
        ManualPurchaseLot.financial_year == financial_year,
    )
    if ticker:
        q = q.filter(ManualPurchaseLot.ticker == ticker)
    if trading_batch_id:
        q = q.filter(ManualPurchaseLot.trading_batch_id == trading_batch_id)
    return q.order_by(ManualPurchaseLot.ticker, ManualPurchaseLot.purchase_date).all()


@router.put("/lots/{lot_id}", response_model=LotResponse)
def update_lot(
    lot_id:       int,
    body:         LotUpdate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    # Filter by BOTH id AND user_id so a user cannot edit another user's lot.
    lot = db.query(ManualPurchaseLot).filter(
        ManualPurchaseLot.id      == lot_id,
        ManualPurchaseLot.user_id == current_user.id,
    ).first()
    if not lot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lot not found")
    if body.purchase_date is not None:
        lot.purchase_date = body.purchase_date
    if body.qty is not None:
        lot.qty = body.qty
    if body.unit_price is not None:
        lot.unit_price = body.unit_price
    if body.brokerage is not None:
        lot.brokerage = body.brokerage
    if body.gst is not None:
        lot.gst = body.gst
    db.commit()
    db.refresh(lot)
    return lot


@router.delete("/lots", status_code=status.HTTP_204_NO_CONTENT)
def delete_lots_batch(
    financial_year:   str = Query(...),
    trading_batch_id: str = Query(...),
    current_user:     User    = Depends(get_current_user),
    db:               Session = Depends(get_db),
):
    """Delete all lots for the current user + FY + trading_batch_id (clear upload batch)."""
    db.query(ManualPurchaseLot).filter(
        ManualPurchaseLot.user_id          == current_user.id,
        ManualPurchaseLot.financial_year   == financial_year,
        ManualPurchaseLot.trading_batch_id == trading_batch_id,
    ).delete(synchronize_session=False)
    db.commit()


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
            # Exclude crypto reports from the stocks endpoint
            TaxReport.report_type    != "crypto",
        )
        .order_by(TaxReport.created_at.desc())
        .all()
    )


# ── Crypto Tax report routes ──────────────────────────────────────────────────
# Stored in the same tax_reports table but namespaced with report_type="crypto".
# Never mixed with stock reports — each endpoint filters strictly by report_type.

@router.post("/crypto/reports", response_model=ReportResponse, status_code=status.HTTP_201_CREATED)
def save_crypto_report(
    body:         ReportCreate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    report = TaxReport(
        user_id              = current_user.id,
        financial_year       = body.financial_year,
        net_taxable_gain     = body.net_taxable_gain,
        gross_capital_gains  = body.gross_capital_gains,
        gross_capital_losses = body.gross_capital_losses,
        cgt_discount_applied = body.cgt_discount_applied,
        report_json          = body.report_json,
        report_type          = "crypto",
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


@router.get("/crypto/reports", response_model=list[ReportResponse])
def list_crypto_reports(
    financial_year: str     = Query(...),
    current_user:   User    = Depends(get_current_user),
    db:             Session = Depends(get_db),
):
    return (
        db.query(TaxReport)
        .filter(
            TaxReport.user_id        == current_user.id,
            TaxReport.financial_year == financial_year,
            TaxReport.report_type    == "crypto",
        )
        .order_by(TaxReport.created_at.desc())
        .all()
    )


# ── Crypto manual lot routes ──────────────────────────────────────────────────
# Stored in manual_crypto_lots — never mixed with shares lots.

class CryptoLotCreate(BaseModel):
    financial_year:   str
    asset:            str
    acquisition_date: str           # "dd/mm/yyyy"
    qty:              float
    total_cost_aud:   float
    fee_aud:          float = 0.0
    crypto_batch_id:  Optional[str] = None


class CryptoLotUpdate(BaseModel):
    acquisition_date: Optional[str]   = None
    qty:              Optional[float] = None
    total_cost_aud:   Optional[float] = None
    fee_aud:          Optional[float] = None


class CryptoLotResponse(BaseModel):
    id:               int
    financial_year:   str
    asset:            str
    acquisition_date: str
    qty:              float
    total_cost_aud:   float
    fee_aud:          float
    crypto_batch_id:  Optional[str]
    created_at:       datetime

    class Config:
        from_attributes = True


@router.post("/crypto/lots", response_model=CryptoLotResponse, status_code=status.HTTP_201_CREATED)
def save_crypto_lot(
    body:         CryptoLotCreate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    lot = ManualCryptoLot(
        user_id          = current_user.id,
        financial_year   = body.financial_year,
        asset            = body.asset.upper().strip(),
        acquisition_date = body.acquisition_date,
        qty              = body.qty,
        total_cost_aud   = body.total_cost_aud,
        fee_aud          = body.fee_aud,
        crypto_batch_id  = body.crypto_batch_id,
    )
    db.add(lot)
    db.commit()
    db.refresh(lot)
    return lot


@router.get("/crypto/lots", response_model=list[CryptoLotResponse])
def get_crypto_lots(
    financial_year:  str           = Query(...),
    asset:           Optional[str] = Query(None),
    crypto_batch_id: Optional[str] = Query(None),
    current_user:    User          = Depends(get_current_user),
    db:              Session       = Depends(get_db),
):
    q = db.query(ManualCryptoLot).filter(
        ManualCryptoLot.user_id        == current_user.id,
        ManualCryptoLot.financial_year == financial_year,
    )
    if asset:
        q = q.filter(ManualCryptoLot.asset == asset.upper().strip())
    if crypto_batch_id:
        q = q.filter(ManualCryptoLot.crypto_batch_id == crypto_batch_id)
    return q.order_by(ManualCryptoLot.asset, ManualCryptoLot.acquisition_date).all()


@router.put("/crypto/lots/{lot_id}", response_model=CryptoLotResponse)
def update_crypto_lot(
    lot_id:       int,
    body:         CryptoLotUpdate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    lot = db.query(ManualCryptoLot).filter(
        ManualCryptoLot.id      == lot_id,
        ManualCryptoLot.user_id == current_user.id,
    ).first()
    if not lot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crypto lot not found")
    if body.acquisition_date is not None: lot.acquisition_date = body.acquisition_date
    if body.qty              is not None: lot.qty              = body.qty
    if body.total_cost_aud   is not None: lot.total_cost_aud   = body.total_cost_aud
    if body.fee_aud          is not None: lot.fee_aud          = body.fee_aud
    db.commit()
    db.refresh(lot)
    return lot


@router.delete("/crypto/lots", status_code=status.HTTP_204_NO_CONTENT)
def delete_crypto_lots_batch(
    financial_year:  str = Query(...),
    crypto_batch_id: str = Query(...),
    current_user:    User    = Depends(get_current_user),
    db:              Session = Depends(get_db),
):
    """Delete all crypto lots for the current user + FY + crypto_batch_id."""
    db.query(ManualCryptoLot).filter(
        ManualCryptoLot.user_id         == current_user.id,
        ManualCryptoLot.financial_year  == financial_year,
        ManualCryptoLot.crypto_batch_id == crypto_batch_id,
    ).delete(synchronize_session=False)
    db.commit()


@router.delete("/crypto/lots/{lot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_crypto_lot(
    lot_id:       int,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    lot = db.query(ManualCryptoLot).filter(
        ManualCryptoLot.id      == lot_id,
        ManualCryptoLot.user_id == current_user.id,
    ).first()
    if not lot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crypto lot not found")
    db.delete(lot)
    db.commit()
