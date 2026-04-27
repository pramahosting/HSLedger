"""
test_equity_pipeline.py — HSLedger Trading Module
Unit + integration tests covering all 7 pipeline priorities.

Run from project root:
    cd trading
    python -m pytest tests/test_equity_pipeline.py -v

Or with Claude Code:
    python tests/test_equity_pipeline.py
"""

from __future__ import annotations
import os, sys, tempfile, csv
from datetime import date
from collections import deque

import pandas as pd
import pytest

# Ensure trading module root is on path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.normaliser import normalise, BROKER_COL_MAPS
from shared.deduplicator import deduplicate
from shared.cost_base_loader import load_cost_base_history
from equity.equity_engine import (
    compute_cgt, disposals_to_df, income_to_df, missing_to_df, _fy, Lot
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_commsec_df() -> pd.DataFrame:
    """Minimal CommSec-format DataFrame."""
    return pd.DataFrame({
        "Trade Date":        ["01/07/2023", "15/07/2023", "01/07/2024", "01/08/2024", "15/08/2024"],
        "Settlement Date":   ["03/07/2023", "17/07/2023", "03/07/2024", "05/08/2024", "19/08/2024"],
        "Contract Note No.": ["CN001",      "CN002",      "CN003",      "CN004",      "CN005"],
        "Exchange":          ["ASX"] * 5,
        "Stock Code":        ["CBA",  "CBA",  "CBA",  "XRO",  "XRO"],
        "Stock Name":        ["Commonwealth Bank"] * 3 + ["Xero"] * 2,
        "Transaction":       ["B",    "B",    "S",    "B",    "S"],
        "Quantity":          [100,    50,     80,     200,    200],
        "Price ($)":         [95.00,  97.00,  110.00, 120.00, 130.00],
        "Brokerage ($)":     [19.95,  19.95,  19.95,  19.95,  19.95],
        "GST ($)":           [1.995,  1.995,  1.995,  1.995,  1.995],
        "Contract Value ($)":[9500,   4850,   8800,   24000,  26000],
        "Net Proceeds ($)":  [0,      0,      8778.055, 0,   25978.055],
        "Description":       ["Buy",  "Buy",  "Sell", "Buy",  "Sell"],
    })


def _make_canonical_df(rows: list[dict]) -> pd.DataFrame:
    """Build a canonical DataFrame directly (bypass normaliser)."""
    defaults = {
        "settlement_date": None, "broker": "test", "asset_class": "equity",
        "name": "", "direction": "buy", "brokerage": 19.95, "gst": 1.995,
        "contract_value": 0.0, "net_proceeds": 0.0, "description": "",
        "reference": "", "source_file": "test.xlsx",
        "fingerprint": "000000000000",
    }
    full_rows = [{**defaults, **r} for r in rows]
    return pd.DataFrame(full_rows)


# ── Priority 1: Multi-file merge ──────────────────────────────────────────────

class TestMultiFileMerge:

    def test_normalise_commsec(self):
        df = _make_commsec_df()
        canon = normalise(df, broker="commsec", source_file="commsec.xlsx")
        assert len(canon) == 5
        assert set(canon["transaction"].unique()) <= {"BUY", "SELL"}
        assert (canon["qty"] > 0).all(), "All qty should be positive"
        assert "settlement_date" in canon.columns
        assert "fingerprint" in canon.columns

    def test_settlement_date_preserved(self):
        df = _make_commsec_df()
        canon = normalise(df, broker="commsec")
        assert canon.loc[0, "settlement_date"] == date(2023, 7, 3)

    def test_stake_settlement_computed(self):
        """Stake has no settlement date — should be computed as T+2."""
        stake_df = pd.DataFrame({
            "Trade Date": ["15/07/2024"],
            "Ticker":     ["CBA"],
            "Company":    ["Commonwealth Bank"],
            "Order Type": ["TXN"],
            "Units":      [100],
            "Price (AUD)":     [95.0],
            "Brokerage (AUD)": [3.0],
            "GST (AUD)":       [0.30],
            "Trade Value (AUD)": [9500],
            "Total (AUD)":       [9503.30],
            "Description":       ["Buy CBA"],
            "Market":            ["ASX"],
        })
        canon = normalise(stake_df, broker="stake")
        assert canon.loc[0, "settlement_date"] == date(2024, 7, 17)   # T+2 from Mon 15 Jul

    def test_txn_direction_from_qty_sign(self):
        """TXN with negative qty should normalise to SELL."""
        df = pd.DataFrame({
            "Trade Date":      ["01/07/2024"],
            "Settlement Date": ["03/07/2024"],
            "ASX Code":        ["CBA"],
            "Description":     ["Commonwealth Bank"],
            "Transaction Type":["TXN"],
            "Quantity":        [-100],
            "Unit Price ($)":  [95.0],
            "Brokerage (inc GST) ($)": [19.95],
            "GST on Brokerage ($)":    [1.995],
            "Contract Value ($)":      [9500],
            "Net Amount ($)":          [9478.055],
            "Notes":                   ["Sell CBA"],
            "Reference Number":        ["REF001"],
        })
        canon = normalise(df, broker="nabtrade")
        assert canon.loc[0, "transaction"] == "SELL"
        assert canon.loc[0, "qty"] == 100   # always positive


# ── Priority 2: Historical cost base ──────────────────────────────────────────

class TestHistoricalCostBase:

    def test_load_history_csv(self, tmp_path):
        csv_path = tmp_path / "history.csv"
        csv_path.write_text(
            "stock_code,purchase_date,quantity,unit_price,brokerage,gst,source\n"
            "CBA,01/07/2022,200,88.00,19.95,1.995,commsec\n"
            "XRO,15/09/2022,100,110.00,19.95,1.995,commsec\n"
        )
        hist = load_cost_base_history(str(csv_path))
        assert len(hist) == 2
        assert set(hist["code"].unique()) == {"CBA", "XRO"}
        assert hist.loc[0, "transaction"] == "BUY"
        assert hist.loc[0, "source_file"] == "cost_base_history"

    def test_history_missing_file_returns_empty(self):
        hist = load_cost_base_history("/nonexistent/path.csv")
        assert hist.empty

    def test_history_preloaded_applies_cgt_discount(self, tmp_path):
        """Shares bought in 2022 and sold in 2024 should get 50% CGT discount."""
        csv_path = tmp_path / "history.csv"
        csv_path.write_text(
            "stock_code,purchase_date,quantity,unit_price,brokerage,gst,source\n"
            "CBA,01/07/2022,100,88.00,19.95,1.995,commsec\n"
        )
        hist = load_cost_base_history(str(csv_path))

        # Sell those shares in 2024
        sell_rows = _make_canonical_df([{
            "trade_date": date(2024, 8, 1),
            "code": "CBA", "name": "Commonwealth Bank",
            "transaction": "SELL", "direction": "sell",
            "qty": 100, "price": 110.0,
            "net_proceeds": 10980.055,
        }])

        all_df = pd.concat([hist, sell_rows], ignore_index=True)
        disposals, _, summaries, _, missing, _opts = compute_cgt(all_df)

        assert len(disposals) == 1
        assert disposals[0].discount_eligible is True
        assert disposals[0].discounted_gain == pytest.approx(
            disposals[0].gross_gain * 0.5, rel=1e-3
        )
        assert not missing


# ── Priority 3: Multi-year ─────────────────────────────────────────────────────

class TestMultiYear:

    def test_fy_split(self):
        from shared.multi_file_merger import split_by_fy
        df = _make_canonical_df([
            {"trade_date": date(2023, 8, 1), "code": "CBA", "transaction": "BUY", "qty": 100, "price": 90},
            {"trade_date": date(2024, 8, 1), "code": "CBA", "transaction": "SELL", "qty": 100, "price": 110,
             "net_proceeds": 10980},
        ])
        splits = split_by_fy(df)
        assert "2023-24" in splits
        assert "2024-25" in splits

    def test_carry_forward_loss(self):
        """Loss in FY2023-24 should reduce taxable gain in FY2024-25."""
        rows = _make_canonical_df([
            # FY2023-24 buy
            {"trade_date": date(2023, 7, 1), "code": "BHP", "transaction": "BUY",
             "qty": 100, "price": 50.0, "brokerage": 19.95, "gst": 1.995},
            # FY2023-24 sell at a LOSS
            {"trade_date": date(2024, 3, 1), "code": "BHP", "transaction": "SELL",
             "qty": 100, "price": 40.0, "direction": "sell", "net_proceeds": 3980.055},
            # FY2024-25 buy + sell at a GAIN
            {"trade_date": date(2024, 7, 1), "code": "CBA", "transaction": "BUY",
             "qty": 100, "price": 80.0, "brokerage": 19.95, "gst": 1.995},
            {"trade_date": date(2025, 3, 1), "code": "CBA", "transaction": "SELL",
             "qty": 100, "price": 100.0, "direction": "sell", "net_proceeds": 9980.055},
        ])
        _, _, summaries, _, _, _ = compute_cgt(rows)

        fy2324 = summaries.get("2023-24")
        fy2425 = summaries.get("2024-25")
        assert fy2324 is not None
        assert fy2425 is not None
        assert fy2324.gross_losses > 0
        assert fy2425.carry_forward_in > 0
        assert fy2425.net_after_carryforward < fy2425.net_capital_gain_pre


# ── Priority 4: Duplicate detection ──────────────────────────────────────────

class TestDuplicateDetection:

    def test_exact_duplicate_removed(self):
        rows = _make_canonical_df([
            {"trade_date": date(2024, 7, 1), "code": "CBA", "transaction": "BUY",
             "qty": 100, "price": 90.0, "source_file": "file_a.xlsx",
             "fingerprint": "abc123"},
            # exact same fingerprint = duplicate
            {"trade_date": date(2024, 7, 1), "code": "CBA", "transaction": "BUY",
             "qty": 100, "price": 90.0, "source_file": "file_b.xlsx",
             "fingerprint": "abc123"},
        ])
        clean, dups = deduplicate(rows)
        assert len(clean) == 1
        assert len(dups) == 1

    def test_unique_rows_not_removed(self):
        rows = _make_canonical_df([
            {"trade_date": date(2024, 7, 1), "code": "CBA", "transaction": "BUY",
             "qty": 100, "price": 90.0, "fingerprint": "aaa111"},
            {"trade_date": date(2024, 7, 2), "code": "CBA", "transaction": "BUY",
             "qty": 50,  "price": 91.0, "fingerprint": "bbb222"},
        ])
        clean, dups = deduplicate(rows)
        assert len(clean) == 2
        assert dups.empty


# ── Priority 5: CGT discount + missing buy ────────────────────────────────────

class TestCGTRules:

    def test_50pct_discount_applied(self):
        rows = _make_canonical_df([
            {"trade_date": date(2023, 1, 1), "code": "WES", "transaction": "BUY",
             "qty": 100, "price": 50.0, "brokerage": 19.95, "gst": 1.995},
            {"trade_date": date(2024, 3, 1), "code": "WES", "transaction": "SELL",
             "qty": 100, "price": 70.0, "direction": "sell", "net_proceeds": 6980.055},
        ])
        disposals, _, _, _, _, _ = compute_cgt(rows)
        assert disposals[0].discount_eligible is True
        assert disposals[0].discounted_gain == pytest.approx(
            disposals[0].gross_gain * 0.5, rel=1e-3
        )

    def test_no_discount_short_term(self):
        rows = _make_canonical_df([
            {"trade_date": date(2024, 7, 1),  "code": "ANZ", "transaction": "BUY",
             "qty": 100, "price": 30.0},
            {"trade_date": date(2024, 10, 1), "code": "ANZ", "transaction": "SELL",
             "qty": 100, "price": 35.0, "direction": "sell", "net_proceeds": 3480.055},
        ])
        disposals, _, _, _, _, _ = compute_cgt(rows)
        assert disposals[0].discount_eligible is False
        assert disposals[0].discounted_gain == pytest.approx(disposals[0].gross_gain, rel=1e-3)

    def test_missing_buy_flagged(self):
        """Sell with no matching buy should produce a MissingBuyFlag."""
        rows = _make_canonical_df([
            {"trade_date": date(2024, 8, 1), "code": "BHP", "transaction": "SELL",
             "qty": 100, "price": 45.0, "direction": "sell", "net_proceeds": 4480.055},
        ])
        _, _, _, _, missing, _ = compute_cgt(rows)
        assert len(missing) == 1
        assert missing[0].code == "BHP"
        assert missing[0].qty_unmatched == pytest.approx(100.0)

    def test_income_not_cgt_event(self):
        rows = _make_canonical_df([
            {"trade_date": date(2024, 9, 1), "code": "CBA", "transaction": "DIV",
             "direction": "income", "qty": 0, "price": 0, "net_proceeds": 500.0},
        ])
        disposals, income, _, _, _, _ = compute_cgt(rows)
        assert len(disposals) == 0
        assert len(income) == 1
        assert income[0].amount == pytest.approx(500.0)


# ── Priority 6: Unsupported broker fallback ────────────────────────────────────

class TestBrokerDetection:

    def test_commsec_detected(self, tmp_path):
        from shared.detect_file_type import detect
        df = _make_commsec_df()
        p = tmp_path / "commsec.xlsx"
        df.to_excel(str(p), index=False)
        result = detect(str(p))
        assert result["broker"] == "commsec"
        assert result["confidence"] >= 0.5

    def test_unknown_broker_low_confidence(self, tmp_path):
        from shared.detect_file_type import detect, CONFIDENCE_THRESHOLD
        df = pd.DataFrame({"col_a": [1, 2], "col_b": ["x", "y"]})
        p = tmp_path / "mystery.xlsx"
        df.to_excel(str(p), index=False)
        result = detect(str(p))
        assert result["confidence"] < CONFIDENCE_THRESHOLD
        assert result["broker"] == "unknown"
        assert result["fallback_suggestion"] is not None


# ── Priority 7: Local cost base database ──────────────────────────────────────

class TestLocalCostBaseDB:

    def _db_path(self, tmp_path) -> str:
        return str(tmp_path / "test_local_db.json")

    # 1. JSON DB creation
    def test_db_creation(self, tmp_path):
        from shared.local_cost_base_db import ensure_local_db, _load_db
        path = self._db_path(tmp_path)
        assert not os.path.exists(path)
        ensure_local_db(path)
        assert os.path.exists(path)
        db = _load_db(path)
        assert db["version"] == "1.0"
        assert db["historical_lots"] == []
        assert db["resolution_log"] == []

    # 2. Add historical lot
    def test_add_historical_lot(self, tmp_path):
        from shared.local_cost_base_db import ensure_local_db, add_historical_lot, _load_db
        path = self._db_path(tmp_path)
        ensure_local_db(path)
        lot_data = {
            "code": "CBA", "trade_date": "01/07/2022",
            "qty": 100.0, "unit_price": 88.00,
            "brokerage": 19.95, "gst": 1.995,
            "broker": "commsec", "notes": "Test lot",
        }
        added = add_historical_lot(path, lot_data)
        assert added["code"] == "CBA"
        assert added["qty_original"] == 100.0
        assert added["unit_price"] == 88.00
        assert added["cost_per_unit"] == pytest.approx(88.00 + (19.95 + 1.995) / 100.0, rel=1e-6)
        db = _load_db(path)
        assert len(db["historical_lots"]) == 1

    # 3. Duplicate prevention
    def test_duplicate_lot_rejected(self, tmp_path):
        from shared.local_cost_base_db import ensure_local_db, add_historical_lot
        path = self._db_path(tmp_path)
        ensure_local_db(path)
        lot_data = {
            "code": "CBA", "trade_date": "01/07/2022",
            "qty": 100.0, "unit_price": 88.00,
            "brokerage": 19.95, "gst": 1.995, "broker": "commsec",
        }
        add_historical_lot(path, lot_data)
        with pytest.raises(ValueError, match="Duplicate"):
            add_historical_lot(path, lot_data)   # identical → must fail

    # 4. Load JSON lot into canonical BUY DataFrame
    def test_load_local_lots_canonical(self, tmp_path):
        from shared.local_cost_base_db import ensure_local_db, add_historical_lot, load_local_lots
        path = self._db_path(tmp_path)
        ensure_local_db(path)
        add_historical_lot(path, {
            "code": "XRO", "trade_date": "15/09/2022",
            "qty": 50.0, "unit_price": 110.00,
            "brokerage": 19.95, "gst": 1.995, "broker": "commsec",
        })
        df = load_local_lots(path)
        assert len(df) == 1
        assert df.loc[0, "code"] == "XRO"
        assert df.loc[0, "transaction"] == "BUY"
        assert df.loc[0, "direction"] == "buy"
        assert df.loc[0, "source_file"] == "local_cost_base_db"
        assert df.loc[0, "qty"] == 50.0
        required_cols = {
            "trade_date", "settlement_date", "broker", "asset_class", "code", "name",
            "transaction", "qty", "direction", "price", "brokerage", "gst",
            "contract_value", "net_proceeds", "description", "reference",
            "source_file", "fingerprint",
        }
        assert required_cols.issubset(set(df.columns))

    # 5. Missing buy before/after reduction
    def test_missing_buy_quantity_reduces(self, tmp_path):
        from shared.local_cost_base_db import ensure_local_db, add_historical_lot, load_local_lots
        from main import _build_initial_queues
        path = self._db_path(tmp_path)
        ensure_local_db(path)

        # Sell 100 BHP with no matching buy
        sell_rows = _make_canonical_df([{
            "trade_date": date(2024, 8, 1), "code": "BHP",
            "transaction": "SELL", "direction": "sell",
            "qty": 100, "price": 45.0, "net_proceeds": 4480.055,
        }])
        _, _, _, _, missing_before, _ = compute_cgt(sell_rows)
        assert len(missing_before) == 1
        assert missing_before[0].qty_unmatched == pytest.approx(100.0)

        # Add 60 shares to local DB
        add_historical_lot(path, {
            "code": "BHP", "trade_date": "01/07/2022",
            "qty": 60.0, "unit_price": 30.0,
            "brokerage": 19.95, "gst": 1.995, "broker": "commsec",
        })
        local_df = load_local_lots(path)
        initial_queues = _build_initial_queues(local_df)
        _, _, _, _, missing_after, _ = compute_cgt(
            sell_rows, initial_queues=initial_queues
        )
        assert len(missing_after) == 1
        assert missing_after[0].qty_unmatched == pytest.approx(40.0)

    # 6. Partial resolution
    def test_partial_resolution(self, tmp_path):
        from shared.local_cost_base_db import ensure_local_db, add_historical_lot, load_local_lots
        from main import _build_initial_queues
        path = self._db_path(tmp_path)
        ensure_local_db(path)

        sell_rows = _make_canonical_df([{
            "trade_date": date(2024, 8, 1), "code": "WES",
            "transaction": "SELL", "direction": "sell",
            "qty": 200, "price": 60.0, "net_proceeds": 11980.055,
        }])
        add_historical_lot(path, {
            "code": "WES", "trade_date": "01/07/2022",
            "qty": 100.0, "unit_price": 40.0, "broker": "commsec",
        })
        local_df = load_local_lots(path)
        initial_queues = _build_initial_queues(local_df)
        _, _, _, _, missing, _ = compute_cgt(sell_rows, initial_queues=initial_queues)
        assert len(missing) == 1
        assert missing[0].qty_unmatched == pytest.approx(100.0)   # 200 - 100 = 100 still unmatched

    # 7. Full resolution
    def test_full_resolution(self, tmp_path):
        from shared.local_cost_base_db import ensure_local_db, add_historical_lot, load_local_lots
        from main import _build_initial_queues
        path = self._db_path(tmp_path)
        ensure_local_db(path)

        sell_rows = _make_canonical_df([{
            "trade_date": date(2024, 8, 1), "code": "ANZ",
            "transaction": "SELL", "direction": "sell",
            "qty": 100, "price": 30.0, "net_proceeds": 2980.055,
        }])
        add_historical_lot(path, {
            "code": "ANZ", "trade_date": "01/07/2022",
            "qty": 100.0, "unit_price": 22.0, "broker": "commsec",
        })
        local_df = load_local_lots(path)
        initial_queues = _build_initial_queues(local_df)
        _, _, _, _, missing, _ = compute_cgt(sell_rows, initial_queues=initial_queues)
        assert len(missing) == 0

    # 8. Extra quantity becomes open position
    def test_extra_qty_becomes_open_position(self, tmp_path):
        from shared.local_cost_base_db import ensure_local_db, add_historical_lot, load_local_lots
        from main import _build_initial_queues
        path = self._db_path(tmp_path)
        ensure_local_db(path)

        sell_rows = _make_canonical_df([{
            "trade_date": date(2024, 8, 1), "code": "NAB",
            "transaction": "SELL", "direction": "sell",
            "qty": 50, "price": 35.0, "net_proceeds": 1730.055,
        }])
        # Add MORE than the sell qty — 120 vs 50 needed
        add_historical_lot(path, {
            "code": "NAB", "trade_date": "01/07/2022",
            "qty": 120.0, "unit_price": 25.0, "broker": "commsec",
        })
        local_df = load_local_lots(path)
        initial_queues = _build_initial_queues(local_df)
        _, _, _, open_queues, missing, _ = compute_cgt(sell_rows, initial_queues=initial_queues)
        assert len(missing) == 0
        assert "NAB" in open_queues
        remaining_qty = sum(lot.qty for lot in open_queues["NAB"])
        assert remaining_qty == pytest.approx(70.0)   # 120 - 50 = 70 open

    # 9. Invalid date rejected
    def test_invalid_date_rejected(self, tmp_path):
        from shared.local_cost_base_db import ensure_local_db, add_historical_lot
        path = self._db_path(tmp_path)
        ensure_local_db(path)
        with pytest.raises(ValueError, match="trade_date"):
            add_historical_lot(path, {
                "code": "CBA", "trade_date": "not-a-date",
                "qty": 100.0, "unit_price": 88.00,
            })

    # 9b. Purchase date after disposal date rejected
    def test_purchase_after_disposal_rejected(self, tmp_path):
        from shared.local_cost_base_db import ensure_local_db, add_historical_lot
        path = self._db_path(tmp_path)
        ensure_local_db(path)
        with pytest.raises(ValueError, match="after disposal"):
            add_historical_lot(
                path,
                {"code": "CBA", "trade_date": "01/08/2025", "qty": 100.0, "unit_price": 88.00},
                disposal_date=date(2024, 8, 1),
            )

    # 10. Invalid negative quantity rejected
    def test_negative_qty_rejected(self, tmp_path):
        from shared.local_cost_base_db import ensure_local_db, add_historical_lot
        path = self._db_path(tmp_path)
        ensure_local_db(path)
        with pytest.raises(ValueError, match="positive"):
            add_historical_lot(path, {
                "code": "CBA", "trade_date": "01/07/2022",
                "qty": -10.0, "unit_price": 88.00,
            })

    # 10b. Negative brokerage rejected
    def test_negative_brokerage_rejected(self, tmp_path):
        from shared.local_cost_base_db import ensure_local_db, add_historical_lot
        path = self._db_path(tmp_path)
        ensure_local_db(path)
        with pytest.raises(ValueError, match="negative"):
            add_historical_lot(path, {
                "code": "CBA", "trade_date": "01/07/2022",
                "qty": 100.0, "unit_price": 88.00, "brokerage": -5.0,
            })


# ── Standalone runner (no pytest needed) ──────────────────────────────────────

if __name__ == "__main__":
    import traceback
    test_classes = [
        TestMultiFileMerge, TestHistoricalCostBase, TestMultiYear,
        TestDuplicateDetection, TestCGTRules, TestBrokerDetection,
        TestLocalCostBaseDB,
    ]
    passed = failed = 0
    for cls in test_classes:
        inst = cls()
        for name in dir(cls):
            if not name.startswith("test_"):
                continue
            try:
                method = getattr(inst, name)
                import inspect
                sig = inspect.signature(method)
                if "tmp_path" in sig.parameters:
                    import pathlib
                    with tempfile.TemporaryDirectory() as td:
                        method(pathlib.Path(td))
                else:
                    method()
                print(f"  ✅ {cls.__name__}.{name}")
                passed += 1
            except Exception as e:
                print(f"  ❌ {cls.__name__}.{name}: {e}")
                traceback.print_exc()
                failed += 1

    print(f"\n{'─'*50}")
    print(f"  Results: {passed} passed, {failed} failed")
