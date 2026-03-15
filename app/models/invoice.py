from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, inspect, text
from sqlalchemy.orm import relationship

from app.database import engine
from app.models.base import Base


class BusinessDetail(Base):
    __tablename__ = "business_details"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True)
    phone = Column(String(20), nullable=True)
    address = Column(Text, nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    postal_code = Column(String(20), nullable=True)
    country = Column(String(100), nullable=True)
    tax_id = Column(String(50), nullable=True)
    website = Column(String(255), nullable=True)
    logo_url = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    invoices = relationship("Invoice", back_populates="business")


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True)
    phone = Column(String(20), nullable=True)
    address = Column(Text, nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    postal_code = Column(String(20), nullable=True)
    country = Column(String(100), nullable=True)
    tax_id = Column(String(50), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    invoices = relationship("Invoice", back_populates="customer")


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String(50), unique=True, nullable=False, index=True)
    business_id = Column(Integer, ForeignKey("business_details.id"), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    bill_to_name = Column(String(255), nullable=True)
    bill_to_address = Column(Text, nullable=True)
    bill_to_phone = Column(String(20), nullable=True)
    bill_to_email = Column(String(255), nullable=True)

    invoice_date = Column(DateTime, nullable=False)
    due_date = Column(DateTime, nullable=True)

    subtotal = Column(Float, default=0.0)
    tax_amount = Column(Float, default=0.0)
    tax_percent = Column(Float, default=0.0)
    discount_amount = Column(Float, default=0.0)
    total_amount = Column(Float, default=0.0)

    notes = Column(Text, nullable=True)
    payment_terms = Column(String(255), nullable=True)
    status = Column(String(50), default="draft")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    business = relationship("BusinessDetail", back_populates="invoices")
    customer = relationship("Customer", back_populates="invoices")
    items = relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)

    description = Column(String(500), nullable=False)
    quantity = Column(Float, nullable=False)
    unit_price = Column(Float, nullable=False)
    line_total = Column(Float, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    invoice = relationship("Invoice", back_populates="items")


def sync_invoice_schema():
    inspector = inspect(engine)
    if "invoices" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("invoices")}
    missing_column_sql = {
        "bill_to_name": "ALTER TABLE invoices ADD COLUMN bill_to_name VARCHAR(255)",
        "bill_to_address": "ALTER TABLE invoices ADD COLUMN bill_to_address TEXT",
        "bill_to_phone": "ALTER TABLE invoices ADD COLUMN bill_to_phone VARCHAR(20)",
        "bill_to_email": "ALTER TABLE invoices ADD COLUMN bill_to_email VARCHAR(255)",
    }

    with engine.begin() as connection:
        for column_name, sql in missing_column_sql.items():
            if column_name not in existing_columns:
                connection.execute(text(sql))
