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

def init_db():
    print("Initializing database...")
    Base.metadata.create_all(bind=engine)
    print("Database initialized.")

    db = SessionLocal()

    try:

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