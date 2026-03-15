import re
from datetime import datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from app.database import SessionLocal, engine
from app.models.base import Base
from app.models.invoice import BusinessDetail, Customer, Invoice, InvoiceItem, sync_invoice_schema


def init_invoice_tables():
    Base.metadata.create_all(bind=engine)
    sync_invoice_schema()


class InvoiceService:
    @staticmethod
    def list_invoice_numbers() -> list[str]:
        db = SessionLocal()
        try:
            return [invoice_number for (invoice_number,) in db.query(Invoice.invoice_number).all()]
        finally:
            db.close()

    @staticmethod
    def create_invoice(
        invoice_number: str,
        business_id: int,
        customer_id: int | None,
        invoice_date: datetime,
        due_date: datetime | None = None,
        items: list | None = None,
        notes: str | None = None,
        payment_terms: str | None = None,
        tax_percent: float = 0.0,
        bill_to_name: str | None = None,
        bill_to_address: str | None = None,
        bill_to_phone: str | None = None,
        bill_to_email: str | None = None,
    ) -> dict:
        db = SessionLocal()
        try:
            resolved_customer_id = customer_id or CustomerService.get_or_create_default_customer_id(db=db)
            subtotal = sum(item["quantity"] * item["unit_price"] for item in items or [])
            tax_amount = subtotal * (tax_percent / 100)
            total_amount = subtotal + tax_amount

            invoice = Invoice(
                invoice_number=invoice_number,
                business_id=business_id,
                customer_id=resolved_customer_id,
                bill_to_name=bill_to_name,
                bill_to_address=bill_to_address,
                bill_to_phone=bill_to_phone,
                bill_to_email=bill_to_email,
                invoice_date=invoice_date,
                due_date=due_date,
                subtotal=subtotal,
                tax_amount=tax_amount,
                tax_percent=tax_percent,
                total_amount=total_amount,
                notes=notes,
                payment_terms=payment_terms,
                status="draft",
            )
            db.add(invoice)
            db.flush()

            if items:
                for item in items:
                    line_total = item["quantity"] * item["unit_price"]
                    db.add(
                        InvoiceItem(
                            invoice_id=invoice.id,
                            description=item["description"],
                            quantity=item["quantity"],
                            unit_price=item["unit_price"],
                            line_total=line_total,
                        )
                    )

            db.commit()
            db.refresh(invoice)
            return {"id": invoice.id, "invoice_number": invoice.invoice_number}
        except SQLAlchemyError:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def list_invoices(business_id: int | None = None, status: str | None = None) -> list[Invoice]:
        db = SessionLocal()
        try:
            query = db.query(Invoice).options(selectinload(Invoice.items))
            if business_id:
                query = query.filter(Invoice.business_id == business_id)
            if status:
                query = query.filter(Invoice.status == status)
            return query.order_by(Invoice.created_at.desc()).all()
        finally:
            db.close()

    @staticmethod
    def update_invoice_status(invoice_id: int, status: str) -> Invoice | None:
        db = SessionLocal()
        try:
            invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
            if invoice:
                invoice.status = status
                invoice.updated_at = datetime.utcnow()
                db.commit()
            return invoice
        finally:
            db.close()

    @staticmethod
    def delete_invoice(invoice_id: int) -> bool:
        db = SessionLocal()
        try:
            invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
            if not invoice:
                return False
            db.delete(invoice)
            db.commit()
            return True
        finally:
            db.close()


class CustomerService:
    @staticmethod
    def get_or_create_default_customer_id(db=None) -> int:
        owned_session = db is None
        session = db or SessionLocal()
        try:
            customer = session.query(Customer).filter(Customer.name == "Walk-in Customer").first()
            if customer:
                return customer.id

            customer = Customer(name="Walk-in Customer", is_active=False)
            session.add(customer)
            session.commit()
            session.refresh(customer)
            return customer.id
        finally:
            if owned_session:
                session.close()


class BusinessService:
    @staticmethod
    def create_business(
        name: str,
        email: str | None = None,
        phone: str | None = None,
        address: str | None = None,
        city: str | None = None,
        state: str | None = None,
        postal_code: str | None = None,
        country: str | None = None,
        tax_id: str | None = None,
        website: str | None = None,
        logo_url: str | None = None,
    ) -> dict:
        db = SessionLocal()
        try:
            business = BusinessDetail(
                name=name,
                email=email,
                phone=phone,
                address=address,
                city=city,
                state=state,
                postal_code=postal_code,
                country=country,
                tax_id=tax_id,
                website=website,
                logo_url=logo_url,
            )
            db.add(business)
            db.commit()
            db.refresh(business)
            return {
                "id": business.id,
                "name": business.name,
                "updated_at": business.updated_at,
            }
        finally:
            db.close()

    @staticmethod
    def get_business(business_id: int) -> BusinessDetail | None:
        db = SessionLocal()
        try:
            return db.query(BusinessDetail).filter(BusinessDetail.id == business_id).first()
        finally:
            db.close()

    @staticmethod
    def list_businesses() -> list[BusinessDetail]:
        db = SessionLocal()
        try:
            return db.query(BusinessDetail).order_by(BusinessDetail.name).all()
        finally:
            db.close()

    @staticmethod
    def update_business(business_id: int, **kwargs) -> dict | None:
        db = SessionLocal()
        try:
            business = db.query(BusinessDetail).filter(BusinessDetail.id == business_id).first()
            if business:
                for key, value in kwargs.items():
                    if hasattr(business, key):
                        setattr(business, key, value)
                business.updated_at = datetime.utcnow()
                db.commit()
                db.refresh(business)
                return {
                    "id": business.id,
                    "name": business.name,
                    "updated_at": business.updated_at,
                }
            return None
        finally:
            db.close()

    @staticmethod
    def delete_business(business_id: int) -> dict:
        db = SessionLocal()
        try:
            business = db.query(BusinessDetail).filter(BusinessDetail.id == business_id).first()
            if not business:
                return {"deleted": False, "reason": "not_found"}

            has_invoices = db.query(Invoice.id).filter(Invoice.business_id == business_id).first() is not None
            if has_invoices:
                return {"deleted": False, "reason": "has_invoices"}

            db.delete(business)
            db.commit()
            return {"deleted": True}
        except SQLAlchemyError:
            db.rollback()
            raise
        finally:
            db.close()


def generate_invoice_number() -> str:
    invoice_numbers = InvoiceService.list_invoice_numbers()
    if not invoice_numbers:
        return "INV-0001"

    numeric_values: list[tuple[int, int, str]] = []
    for invoice_number in invoice_numbers:
        match = re.search(r"(\d+)$", invoice_number)
        if match:
            numeric_values.append((int(match.group(1)), len(match.group(1)), invoice_number))

    if not numeric_values:
        return "INV-0001"

    max_value, width, source_invoice_number = max(numeric_values, key=lambda item: item[0])
    prefix = source_invoice_number[:-width]
    return f"{prefix}{max_value + 1:0{width}d}"
