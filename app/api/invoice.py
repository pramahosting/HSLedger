from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.invoice import BusinessDetail, Invoice
from app.services.invoice_service import (
    BusinessService,
    InvoiceService,
    generate_invoice_number,
    init_invoice_tables,
)

router = APIRouter()


class BusinessCreateRequest(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    tax_id: str | None = None
    website: str | None = None
    logo_url: str | None = None


class BusinessUpdateRequest(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    tax_id: str | None = None
    website: str | None = None
    logo_url: str | None = None


class InvoiceItemRequest(BaseModel):
    description: str
    quantity: float
    unit_price: float


class InvoiceCreateRequest(BaseModel):
    invoice_number: str | None = None
    business_id: int
    customer_id: int | None = None
    invoice_date: datetime
    due_date: datetime | None = None
    items: List[InvoiceItemRequest] = []
    notes: str | None = None
    payment_terms: str | None = None
    tax_percent: float = 0.0
    bill_to_name: str | None = None
    bill_to_address: str | None = None
    bill_to_phone: str | None = None
    bill_to_email: str | None = None


class InvoiceStatusUpdateRequest(BaseModel):
    status: str


@router.on_event("startup")
def startup_init_invoice_tables():
    init_invoice_tables()


@router.get("/next-number")
def get_next_invoice_number():
    return {"invoice_number": generate_invoice_number()}


@router.post("/business")
def create_business(request: BusinessCreateRequest):
    business = BusinessService.create_business(**request.dict())
    return {"id": business["id"], "name": business["name"]}


@router.get("/business")
def list_businesses(db: Session = Depends(get_db)):
    businesses = db.query(BusinessDetail).order_by(BusinessDetail.name).all()
    return [
        {
            "id": business.id,
            "name": business.name,
            "email": business.email,
            "phone": business.phone,
            "address": business.address,
            "city": business.city,
            "state": business.state,
            "postal_code": business.postal_code,
            "country": business.country,
            "tax_id": business.tax_id,
            "website": business.website,
            "logo_url": business.logo_url,
            "created_at": business.created_at,
            "updated_at": business.updated_at,
        }
        for business in businesses
    ]


@router.get("/business/{business_id}")
def get_business(business_id: int, db: Session = Depends(get_db)):
    business = db.query(BusinessDetail).filter(BusinessDetail.id == business_id).first()
    if not business:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business not found")

    return {
        "id": business.id,
        "name": business.name,
        "email": business.email,
        "phone": business.phone,
        "address": business.address,
        "city": business.city,
        "state": business.state,
        "postal_code": business.postal_code,
        "country": business.country,
        "tax_id": business.tax_id,
        "website": business.website,
        "logo_url": business.logo_url,
        "created_at": business.created_at,
        "updated_at": business.updated_at,
    }


@router.patch("/business/{business_id}")
def update_business(business_id: int, request: BusinessUpdateRequest):
    payload = request.dict(exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields provided")

    updated = BusinessService.update_business(business_id, **payload)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business not found")

    return updated


@router.delete("/business/{business_id}")
def delete_business(business_id: int):
    try:
        result = BusinessService.delete_business(business_id)
        if not result.get("deleted"):
            reason = result.get("reason")
            if reason == "not_found":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business not found")
            if reason == "has_invoices":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Cannot delete business with existing invoices.",
                )
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to delete business")
        return {"deleted": True}
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Business delete failed: {exc.__class__.__name__}",
        ) from exc


@router.post("")
def create_invoice(request: InvoiceCreateRequest):
    invoice_number = request.invoice_number or generate_invoice_number()
    try:
        invoice = InvoiceService.create_invoice(
            invoice_number=invoice_number,
            business_id=request.business_id,
            customer_id=request.customer_id,
            invoice_date=request.invoice_date,
            due_date=request.due_date,
            items=[item.dict() for item in request.items],
            notes=request.notes,
            payment_terms=request.payment_terms,
            tax_percent=request.tax_percent,
            bill_to_name=request.bill_to_name,
            bill_to_address=request.bill_to_address,
            bill_to_phone=request.bill_to_phone,
            bill_to_email=request.bill_to_email,
        )
        return invoice
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Invoice save failed due to a duplicate or invalid record.",
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Invoice save failed: {exc.__class__.__name__}",
        ) from exc


@router.get("")
def list_invoices(db: Session = Depends(get_db)):
    invoices = db.query(Invoice).order_by(Invoice.created_at.desc()).all()
    business_map = {
        business.id: business.name
        for business in db.query(BusinessDetail).all()
    }

    return [
        {
            "id": invoice.id,
            "invoice_number": invoice.invoice_number,
            "business_id": invoice.business_id,
            "business_name": business_map.get(invoice.business_id, "N/A"),
            "status": invoice.status,
            "invoice_date": invoice.invoice_date,
            "due_date": invoice.due_date,
            "subtotal": invoice.subtotal,
            "tax_amount": invoice.tax_amount,
            "discount_amount": invoice.discount_amount,
            "total_amount": invoice.total_amount,
            "bill_to_name": invoice.bill_to_name,
            "bill_to_email": invoice.bill_to_email,
            "bill_to_phone": invoice.bill_to_phone,
            "bill_to_address": invoice.bill_to_address,
            "notes": invoice.notes,
            "items": [
                {
                    "description": item.description,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "line_total": item.line_total,
                }
                for item in invoice.items
            ],
        }
        for invoice in invoices
    ]


@router.patch("/{invoice_id}/status")
def update_invoice_status(invoice_id: int, request: InvoiceStatusUpdateRequest):
    try:
        updated = InvoiceService.update_invoice_status(invoice_id, request.status)
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
        # Avoid detached-instance attribute access after session close.
        return {"id": invoice_id, "status": request.status}
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Invoice status update failed: {exc.__class__.__name__}",
        ) from exc


@router.delete("/{invoice_id}")
def delete_invoice(invoice_id: int):
    deleted = InvoiceService.delete_invoice(invoice_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    return {"deleted": True}
