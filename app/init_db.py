import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bcrypt
from app.database import engine, SessionLocal
from app.models.base import Base
from app.models.user import User
from app.models.role import Role
from app.models.transaction import Transaction
from app.models.permission import Permission
from app.models.invoice import BusinessDetail, Customer, Invoice, InvoiceItem
from app.models.session_token import SessionToken
from app.models.manual_purchase_lot import ManualPurchaseLot
from app.models.manual_crypto_lot import ManualCryptoLot
from app.models.tax_report import TaxReport
from app.models import association

PERMISSIONS = [
    # Reconciliation
    ("view_reconciliation",  "Can view the reconciliation page"),
    ("run_reconciliation",   "Can upload files and run reconciliation"),
    ("export_results",       "Can export reconciliation results to CSV/Excel"),
    ("edit_gst",             "Can edit GST categories on transactions"),
    ("view_trading",         "Can view the trading / capital gains page"),
    ("export_trading",       "Can export trading reports"),
    ("view_open_banking",    "Can view the open banking page"),
    ("manage_users",         "Can create, edit, and delete users"),
    ("manage_roles",         "Can assign roles to users"),
]

ROLE_PERMISSIONS = {
    "user": [
        "view_reconciliation",
        "run_reconciliation",
        "export_results",
        "edit_gst",
        "view_trading",
        "export_trading",
        "view_open_banking",
    ],
    "admin": [
        "view_reconciliation",
        "run_reconciliation",
        "export_results",
        "edit_gst",
        "view_trading",
        "export_trading",
        "view_open_banking",
        "manage_users",
        "manage_roles",
    ],
}

def _migrate_add_trading_batch_id(db):
    """Add trading_batch_id column to manual_purchase_lots if it doesn't exist yet."""
    from sqlalchemy import inspect, text
    cols = [c["name"] for c in inspect(engine).get_columns("manual_purchase_lots")]
    if "trading_batch_id" not in cols:
        db.execute(text(
            "ALTER TABLE manual_purchase_lots ADD COLUMN trading_batch_id TEXT"
        ))
        db.commit()
        print("Migration: added trading_batch_id column to manual_purchase_lots.")


def _migrate_create_manual_crypto_lots(db):
    """Create manual_crypto_lots table if it doesn't exist (added in Crypto Module)."""
    from sqlalchemy import inspect, text
    if "manual_crypto_lots" not in inspect(engine).get_table_names():
        db.execute(text("""
            CREATE TABLE manual_crypto_lots (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          INTEGER NOT NULL,
                financial_year   TEXT NOT NULL,
                asset            TEXT NOT NULL,
                acquisition_date TEXT NOT NULL,
                qty              REAL NOT NULL,
                total_cost_aud   REAL NOT NULL,
                fee_aud          REAL DEFAULT 0.0,
                crypto_batch_id  TEXT,
                created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_manual_crypto_lots_user_id ON manual_crypto_lots (user_id)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_manual_crypto_lots_crypto_batch_id ON manual_crypto_lots (crypto_batch_id)"))
        db.commit()
        print("Migration: created manual_crypto_lots table.")


def _migrate_add_report_type(db):
    """Add report_type column to tax_reports to separate stocks vs crypto reports."""
    from sqlalchemy import inspect, text
    cols = [c["name"] for c in inspect(engine).get_columns("tax_reports")]
    if "report_type" not in cols:
        db.execute(text(
            "ALTER TABLE tax_reports ADD COLUMN report_type TEXT DEFAULT 'stocks'"
        ))
        db.commit()
        print("Migration: added report_type column to tax_reports.")


def init_db():
    print("Initializing database...")
    Base.metadata.create_all(bind=engine)
    print("Database initialized.")

    db = SessionLocal()

    try:
        _migrate_add_trading_batch_id(db)
        _migrate_add_report_type(db)

        if db.query(User).count() > 0:
            print("Users already exist. Skipping user creation.")
            return
        
        print("Creating permissions...")

        permission_map = {}

        for name, description in PERMISSIONS:
            p = Permission(name=name, description=description)
            db.add(p)
            permission_map[name] = p
        
        db.flush()

        print("Creating roles...")

        role_map = {}
        for role_name, perms in ROLE_PERMISSIONS.items():
            role = Role(name=role_name, description='Administator with full access' if role_name == 'admin' else 'Regular user with limited access')

            for perm_name in perms:
                role.permissions.append(permission_map[perm_name])
            db.add(role)
            role_map[role_name] = role
        
        db.flush()

        print("Creating admin user...")
        
        raw_password = "1"
        hashed_password = bcrypt.hashpw(raw_password.encode(), bcrypt.gensalt()).decode()

        admin_user = User(
            username="admin",
            full_name="Administrator",
            email="admin@ex.com",
            password=hashed_password,
        )
        admin_user.roles.append(role_map["admin"])
        db.add(admin_user)

        db.commit()
        print("Admin user created with username 'admin', email 'admin@ex.com', and password '1'.")

    except Exception as e:
        db.rollback()
        print(f"Error initializing database: {e}")
        raise
    
    finally:
        db.close()

if __name__ == "__main__":
    init_db()