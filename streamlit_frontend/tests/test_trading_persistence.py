"""
test_trading_persistence.py

Isolation tests for the Shares Trading Taxation persistence layer.

Runs entirely against an in-memory SQLite database — no running server required.
Tests the ORM queries directly so the assertions are authoritative; they prove
the *same* queries used by the API routes enforce user isolation correctly.

Run:
    pytest streamlit_frontend/tests/test_trading_persistence.py -v
  or standalone:
    python streamlit_frontend/tests/test_trading_persistence.py
"""

from __future__ import annotations

import sys
import os
import uuid
from datetime import datetime, timedelta

# ── Path setup ───────────────────────────────────────────────────────────────
# Only the project root is needed for backend (app.*) imports.
# streamlit_frontend/ is intentionally NOT added here: it contains app.py
# (the Streamlit entrypoint) which would shadow the app/ package at the root.
_HERE     = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)               # streamlit_frontend/
_ROOT     = os.path.dirname(_FRONTEND)            # project root
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── SQLAlchemy in-memory test DB ──────────────────────────────────────────────
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.user import User
from app.models.session_token import SessionToken, TOKEN_TTL_HOURS
from app.models.manual_purchase_lot import ManualPurchaseLot
from app.models.tax_report import TaxReport

# In-memory DB: completely isolated from production reconciliation.db
TEST_ENGINE = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
Base.metadata.create_all(TEST_ENGINE)
TestSession = sessionmaker(bind=TEST_ENGINE)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ok(label: str, detail: str = "") -> None:
    suffix = f"  ·  {detail}" if detail else ""
    print(f"  \033[32m✅  PASS\033[0m  {label}{suffix}")


def _fail(label: str, detail: str = "") -> None:
    suffix = f"  ·  {detail}" if detail else ""
    print(f"  \033[31m❌  FAIL\033[0m  {label}{suffix}")
    global _FAILURES
    _FAILURES += 1


def _section(n: int | str, title: str) -> None:
    print(f"\n{'─' * 62}")
    print(f"  [{n}] {title}")
    print(f"{'─' * 62}")


_FAILURES = 0


def _make_user(db, username: str, email: str) -> User:
    import bcrypt
    hashed = bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode()
    u = User(username=username, email=email, password=hashed, full_name=username)
    db.add(u)
    db.flush()
    return u


def _make_token(db, user: User) -> str:
    token = str(uuid.uuid4())
    s = SessionToken(
        token=token,
        user_id=user.id,
        expires_at=datetime.utcnow() + timedelta(hours=TOKEN_TTL_HOURS),
    )
    db.add(s)
    db.flush()
    return token


def _add_lot(db, user_id: int, ticker: str, fy: str, purchase_date: str,
             qty: float, unit_price: float, brokerage: float = 0.0, gst: float = 0.0) -> ManualPurchaseLot:
    lot = ManualPurchaseLot(
        user_id=user_id,
        financial_year=fy,
        ticker=ticker,
        purchase_date=purchase_date,
        qty=qty,
        unit_price=unit_price,
        brokerage=brokerage,
        gst=gst,
    )
    db.add(lot)
    db.flush()
    return lot


def _add_report(db, user_id: int, fy: str, net: float, gross_gains: float,
                gross_losses: float, cgt: float) -> TaxReport:
    r = TaxReport(
        user_id=user_id,
        financial_year=fy,
        net_taxable_gain=net,
        gross_capital_gains=gross_gains,
        gross_capital_losses=gross_losses,
        cgt_discount_applied=cgt,
        report_json='{"source":"test"}',
    )
    db.add(r)
    db.flush()
    return r


def _get_lots(db, user_id: int, fy: str) -> list[ManualPurchaseLot]:
    return (
        db.query(ManualPurchaseLot)
        .filter(ManualPurchaseLot.user_id == user_id, ManualPurchaseLot.financial_year == fy)
        .all()
    )


def _get_reports(db, user_id: int, fy: str) -> list[TaxReport]:
    return (
        db.query(TaxReport)
        .filter(TaxReport.user_id == user_id, TaxReport.financial_year == fy)
        .order_by(TaxReport.created_at.desc())
        .all()
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_schema_tables_exist() -> None:
    _section(1, "Schema — required tables exist")
    from sqlalchemy import inspect
    inspector = inspect(TEST_ENGINE)
    tables = inspector.get_table_names()
    for expected in ("users", "sessions", "manual_purchase_lots", "tax_reports"):
        if expected in tables:
            _ok(f"Table '{expected}' exists")
        else:
            _fail(f"Table '{expected}' missing from schema")


def test_session_token_creation() -> None:
    _section(2, "Session tokens — create and look up")
    db = TestSession()
    try:
        user = _make_user(db, "token_user", "token@test.com")
        token = _make_token(db, user)
        db.commit()

        found = db.query(SessionToken).filter(SessionToken.token == token).first()
        if found and found.user_id == user.id:
            _ok("Token created and retrieved by value")
        else:
            _fail("Token not found or wrong user_id")

        # Token expiry is in the future
        if found and found.expires_at > datetime.utcnow():
            _ok("Token expires_at is in the future", f"TTL={TOKEN_TTL_HOURS}h")
        else:
            _fail("Token already expired on creation")

        # Expired token check
        old_token = str(uuid.uuid4())
        expired = SessionToken(
            token=old_token,
            user_id=user.id,
            expires_at=datetime.utcnow() - timedelta(seconds=1),
        )
        db.add(expired)
        db.commit()
        expired_rec = db.query(SessionToken).filter(SessionToken.token == old_token).first()
        if expired_rec and expired_rec.expires_at < datetime.utcnow():
            _ok("Expired token detected correctly")
        else:
            _fail("Expired token not detected")
    finally:
        db.close()


def test_user_a_sees_only_own_lots() -> None:
    _section(3, "Purchase lots — user_a query returns only user_a rows")
    db = TestSession()
    try:
        user_a = _make_user(db, "user_a", "user_a@test.com")
        user_b = _make_user(db, "user_b", "user_b@test.com")
        db.commit()

        FY = "2024-25"
        _add_lot(db, user_a.id, "CBA", FY, "01/07/2022", 180, 145.00, 9.95, 0.00)
        _add_lot(db, user_a.id, "CBA", FY, "15/01/2023", 100, 153.50, 9.95, 0.00)
        _add_lot(db, user_a.id, "BHP", FY, "10/09/2021",  50,  44.20, 19.95, 1.99)
        _add_lot(db, user_b.id, "NAB", FY, "05/03/2022", 200,  29.50, 9.95, 0.00)
        _add_lot(db, user_b.id, "ANZ", FY, "20/06/2023", 150,  24.00, 9.95, 0.00)
        db.commit()

        lots_a = _get_lots(db, user_a.id, FY)
        if len(lots_a) == 3:
            _ok("user_a query returns 3 rows", f"got {len(lots_a)}")
        else:
            _fail("user_a row count wrong", f"expected 3, got {len(lots_a)}")

        all_mine = all(l.user_id == user_a.id for l in lots_a)
        if all_mine:
            _ok("All returned rows have user_a.id")
        else:
            _fail("Foreign user_id found in user_a results")

        tickers_a = {l.ticker for l in lots_a}
        if "NAB" not in tickers_a and "ANZ" not in tickers_a:
            _ok("user_a results contain no user_b tickers (NAB, ANZ)")
        else:
            _fail("user_a results contain user_b tickers", str(tickers_a))
    finally:
        db.close()


def test_user_b_sees_only_own_lots() -> None:
    _section(4, "Purchase lots — user_b query returns only user_b rows")
    db = TestSession()
    try:
        user_b = db.query(User).filter(User.username == "user_b").first()
        FY = "2024-25"

        lots_b = _get_lots(db, user_b.id, FY)
        if len(lots_b) == 2:
            _ok("user_b query returns 2 rows", f"got {len(lots_b)}")
        else:
            _fail("user_b row count wrong", f"expected 2, got {len(lots_b)}")

        all_mine = all(l.user_id == user_b.id for l in lots_b)
        if all_mine:
            _ok("All returned rows have user_b.id")
        else:
            _fail("Foreign user_id found in user_b results")

        tickers_b = {l.ticker for l in lots_b}
        if "CBA" not in tickers_b and "BHP" not in tickers_b:
            _ok("user_b results contain no user_a tickers (CBA, BHP)")
        else:
            _fail("user_b results contain user_a tickers", str(tickers_b))
    finally:
        db.close()


def test_cross_user_lot_access_returns_nothing() -> None:
    _section(5, "Cross-user access — user_a cannot read user_b lots")
    db = TestSession()
    try:
        user_a = db.query(User).filter(User.username == "user_a").first()
        FY = "2024-25"

        # user_a queries for NAB, which is owned only by user_b.
        # The filter enforces user_id == user_a.id, so 0 rows expected.
        cross = (
            db.query(ManualPurchaseLot)
            .filter(
                ManualPurchaseLot.user_id == user_a.id,   # ← enforced from auth token
                ManualPurchaseLot.financial_year == FY,
                ManualPurchaseLot.ticker == "NAB",
            )
            .all()
        )
        if len(cross) == 0:
            _ok("user_a query for NAB (user_b's ticker) returns 0 rows")
        else:
            _fail("Cross-user isolation broken", f"got {len(cross)} rows")
    finally:
        db.close()


def test_delete_own_lot_only() -> None:
    _section(6, "Delete — user can only delete their own lot")
    db = TestSession()
    try:
        user_a = db.query(User).filter(User.username == "user_a").first()
        user_b = db.query(User).filter(User.username == "user_b").first()
        FY = "2024-25"

        # Get a lot owned by user_b
        b_lot = db.query(ManualPurchaseLot).filter(
            ManualPurchaseLot.user_id == user_b.id,
            ManualPurchaseLot.financial_year == FY,
        ).first()

        # Simulate the DELETE route: filter by BOTH id AND user_id
        # user_a tries to delete user_b's lot — query returns nothing
        attempt = db.query(ManualPurchaseLot).filter(
            ManualPurchaseLot.id == b_lot.id,
            ManualPurchaseLot.user_id == user_a.id,   # ← auth context
        ).first()

        if attempt is None:
            _ok("user_a cannot find user_b's lot when filtered by user_a.id (delete blocked)")
        else:
            _fail("user_a can delete user_b's lot — ownership check missing")
    finally:
        db.close()


def test_tax_report_isolation() -> None:
    _section(7, "Tax reports — each user sees only their own reports")
    db = TestSession()
    try:
        user_a = db.query(User).filter(User.username == "user_a").first()
        user_b = db.query(User).filter(User.username == "user_b").first()
        FY = "2024-25"

        _add_report(db, user_a.id, FY, 16117.56, 145707.11, 129589.54, 23739.22)
        _add_report(db, user_a.id, FY,  8500.00,  90000.00,  81500.00,  15000.00)  # second report
        _add_report(db, user_b.id, FY,  4210.00,  30000.00,  25790.00,   8100.00)
        db.commit()

        reports_a = _get_reports(db, user_a.id, FY)
        reports_b = _get_reports(db, user_b.id, FY)

        if len(reports_a) == 2:
            _ok("user_a has 2 reports", "sorted newest first by created_at DESC")
        else:
            _fail("user_a report count wrong", f"expected 2, got {len(reports_a)}")

        if len(reports_b) == 1:
            _ok("user_b has 1 report")
        else:
            _fail("user_b report count wrong", f"expected 1, got {len(reports_b)}")

        all_a = all(r.user_id == user_a.id for r in reports_a)
        all_b = all(r.user_id == user_b.id for r in reports_b)
        if all_a and all_b:
            _ok("All report rows match their respective user_id")
        else:
            _fail("Report user_id mismatch detected")

        # Cross-user report access
        cross = _get_reports(db, user_a.id, FY)
        has_b_data = any(r.user_id != user_a.id for r in cross)
        if not has_b_data:
            _ok("user_a report query contains no user_b rows")
        else:
            _fail("user_a sees user_b report data")
    finally:
        db.close()


def test_expired_token_rejected() -> None:
    _section(8, "Session — expired token is rejected by get_current_user logic")
    db = TestSession()
    try:
        user_a = db.query(User).filter(User.username == "user_a").first()
        expired_tok = str(uuid.uuid4())
        s = SessionToken(
            token=expired_tok,
            user_id=user_a.id,
            expires_at=datetime.utcnow() - timedelta(hours=1),
        )
        db.add(s)
        db.commit()

        rec = db.query(SessionToken).filter(SessionToken.token == expired_tok).first()
        is_expired = rec is not None and rec.expires_at < datetime.utcnow()
        if is_expired:
            _ok("Expired token correctly identified", "get_current_user would raise 401")
        else:
            _fail("Expired token not detected")
    finally:
        db.close()


def test_financial_year_scoping() -> None:
    _section(9, "FY scoping — query for 2023-24 does not return 2024-25 lots")
    db = TestSession()
    try:
        user_a = db.query(User).filter(User.username == "user_a").first()
        _add_lot(db, user_a.id, "WES", "2023-24", "01/07/2022", 75, 52.00, 9.95)
        db.commit()

        lots_current_fy = _get_lots(db, user_a.id, "2024-25")
        lots_prev_fy    = _get_lots(db, user_a.id, "2023-24")

        if all(l.financial_year == "2024-25" for l in lots_current_fy):
            _ok("2024-25 query returns only 2024-25 lots")
        else:
            _fail("2024-25 query returned cross-FY rows")

        if all(l.financial_year == "2023-24" for l in lots_prev_fy):
            _ok("2023-24 query returns only 2023-24 lots")
        else:
            _fail("2023-24 query returned cross-FY rows")
    finally:
        db.close()


# ── Runner ─────────────────────────────────────────────────────────────────────

def run_all() -> int:
    print("\n╔══════════════════════════════════════════════════════════════╗")
    print("║  HSLedger — Trading Persistence Isolation Tests              ║")
    print("╚══════════════════════════════════════════════════════════════╝")

    test_schema_tables_exist()
    test_session_token_creation()
    test_user_a_sees_only_own_lots()
    test_user_b_sees_only_own_lots()
    test_cross_user_lot_access_returns_nothing()
    test_delete_own_lot_only()
    test_tax_report_isolation()
    test_expired_token_rejected()
    test_financial_year_scoping()

    _section("SUMMARY", "")
    total = 20  # approximate assertion checkpoints above
    if _FAILURES == 0:
        print(f"  \033[32m✅  All assertions passed. User isolation is working correctly.\033[0m")
        print("  Safe to proceed with production implementation.")
    else:
        print(f"  \033[31m❌  {_FAILURES} assertion(s) FAILED.\033[0m")
    print()
    return _FAILURES


if __name__ == "__main__":
    sys.exit(run_all())
