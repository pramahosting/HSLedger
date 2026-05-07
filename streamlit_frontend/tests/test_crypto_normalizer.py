"""
test_crypto_normalizer.py
Tests for the generic crypto ledger normalizer and FIFO CGT engine.

Covers three real-world CSV header styles plus edge cases:
  Style A — CoinSpot (event_time_utc / event_type / asset / quantity / aud_total / fee_aud_inc_gst)
  Style B — Binance-style (timestamp / side / pair / amount / value_quote / fee_amount)
  Style C — Generic ledger (date / type / coin / units / fiat_value / fee)
  Style D — price_aud fallback (date / action / symbol / qty / price_aud / commission)
  Style E — Missing required field → clear ValueError
  Style F — Staking / income events
  Style G — CoinSpot TRANSFER_OUT (non-CGT, should not appear in disposals)
"""
import sys
import os

import numpy as np
import pandas as pd
import pytest

# ── Add backend/trading to path ────────────────────────────────────────────────
_THIS = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.join(os.path.dirname(_THIS), "backend", "trading")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from crypto_normalizer import normalize_ledger, NormalizationReport, FIELD_ALIASES
from crypto_cgt_engine import compute_cgt_fifo


# ── Helpers ────────────────────────────────────────────────────────────────────

def _csv_to_df(text: str) -> pd.DataFrame:
    """Parse an inline CSV string into a DataFrame."""
    from io import StringIO
    return pd.read_csv(StringIO(text.strip()))


# ── Style A — CoinSpot format ──────────────────────────────────────────────────

COINSPOT_CSV = """
event_time_utc,event_type,asset,quantity,aud_total,fee_aud_inc_gst
2022-07-01T10:00:00+00:00,TRADE_BUY,BTC,0.5,15000.00,10.00
2022-07-15T12:00:00+00:00,TRADE_BUY,ETH,2.0,4000.00,5.00
2023-02-01T09:00:00+00:00,TRADE_SELL,BTC,-0.25,9000.00,8.00
2023-06-10T14:00:00+00:00,STAKING_REWARD,ETH,0.01,20.00,0.00
"""

class TestStyleACoinSpot:
    def setup_method(self):
        df = _csv_to_df(COINSPOT_CSV)
        self.norm_df, self.report = normalize_ledger(df)

    def test_all_required_fields_resolved(self):
        assert self.report.unresolved == []

    def test_event_types_mapped_correctly(self):
        events = self.norm_df["_event"].tolist()
        assert events[0] == "BUY"
        assert events[1] == "BUY"
        assert events[2] == "SELL"
        assert events[3] == "STAKING"

    def test_qty_is_absolute(self):
        # CoinSpot SELL has negative quantity — normalizer must abs() it
        sell_row = self.norm_df[self.norm_df["_event"] == "SELL"].iloc[0]
        assert sell_row["_qty"] == pytest.approx(0.25)

    def test_asset_uppercased(self):
        assert set(self.norm_df["_asset"].tolist()) == {"BTC", "ETH"}

    def test_fee_resolved(self):
        buy_row = self.norm_df.iloc[0]
        assert buy_row["_fee_aud"] == pytest.approx(10.00)

    def test_datetime_utc(self):
        dt = self.norm_df.iloc[0]["_dt"]
        assert dt is not None
        assert dt.tzinfo is not None

    def test_cgt_engine_produces_disposal(self):
        disp_df, _, _ = compute_cgt_fifo(self.norm_df)
        assert len(disp_df) == 1
        assert disp_df.iloc[0]["asset"] == "BTC"

    def test_mapping_report_has_correct_column_names(self):
        assert self.report.mapping["datetime"]   == "event_time_utc"
        assert self.report.mapping["event_type"] == "event_type"
        assert self.report.mapping["asset"]      == "asset"
        assert self.report.mapping["quantity"]   == "quantity"
        assert self.report.mapping["amount_aud"] == "aud_total"
        assert self.report.mapping["fee_aud"]    == "fee_aud_inc_gst"


# ── Style B — Binance-style format ────────────────────────────────────────────

BINANCE_CSV = """
timestamp,side,pair,amount,value_quote,fee_amount
2022-07-01T10:00:00+00:00,buy,BTC/AUD,0.5,15000.00,10.00
2022-07-15T12:00:00+00:00,buy,ETH/AUD,2.0,4000.00,5.00
2023-02-01T09:00:00+00:00,sell,BTC/AUD,0.25,9000.00,8.00
"""

class TestStyleBBinance:
    def setup_method(self):
        df = _csv_to_df(BINANCE_CSV)
        self.norm_df, self.report = normalize_ledger(df)

    def test_all_required_fields_resolved(self):
        assert self.report.unresolved == []

    def test_event_types_from_side_column(self):
        assert self.norm_df["_event"].tolist() == ["BUY", "BUY", "SELL"]

    def test_asset_extracted_from_pair(self):
        # "BTC/AUD" → "BTC"
        assert self.norm_df.iloc[0]["_asset"] == "BTC"
        assert self.norm_df.iloc[1]["_asset"] == "ETH"
        assert self.report.asset_from_pair is True

    def test_qty_from_amount_column(self):
        assert self.norm_df.iloc[0]["_qty"] == pytest.approx(0.5)

    def test_amount_aud_from_value_quote(self):
        assert self.norm_df.iloc[0]["_amount_aud"] == pytest.approx(15000.0)

    def test_fee_from_fee_amount(self):
        assert self.norm_df.iloc[0]["_fee_aud"] == pytest.approx(10.0)

    def test_mapping_shows_pair_column(self):
        assert self.report.mapping["asset"] == "pair"

    def test_cgt_engine_produces_disposal(self):
        disp_df, _, _ = compute_cgt_fifo(self.norm_df)
        assert len(disp_df) == 1
        assert disp_df.iloc[0]["asset"] == "BTC"

    def test_capital_gain_correct(self):
        disp_df, _, _ = compute_cgt_fifo(self.norm_df)
        row = disp_df.iloc[0]
        # proceeds = 9000 - 8 = 8992
        # cost_base = (15000 + 10) * (0.25 / 0.5) = 7505
        assert row["proceeds_aud"]  == pytest.approx(8992.0)
        assert row["cost_base_aud"] == pytest.approx(7505.0)
        assert row["capital_gain_aud"] == pytest.approx(1487.0)


# ── Style C — Generic ledger format ───────────────────────────────────────────

GENERIC_CSV = """
date,type,coin,units,fiat_value,fee
2022-08-10T08:00:00Z,purchase,BTC,0.2,6000.00,6.00
2022-11-05T15:30:00Z,disposal,BTC,0.1,4500.00,4.50
2023-04-01T09:00:00Z,staking,ETH,0.05,90.00,0.00
2023-05-01T11:00:00Z,airdrop,ADA,100.0,50.00,0.00
"""

class TestStyleCGeneric:
    def setup_method(self):
        df = _csv_to_df(GENERIC_CSV)
        self.norm_df, self.report = normalize_ledger(df)

    def test_all_required_fields_resolved(self):
        assert self.report.unresolved == []

    def test_purchase_maps_to_buy(self):
        assert self.norm_df.iloc[0]["_event"] == "BUY"

    def test_disposal_maps_to_sell(self):
        assert self.norm_df.iloc[1]["_event"] == "SELL"

    def test_staking_event(self):
        assert self.norm_df.iloc[2]["_event"] == "STAKING"

    def test_airdrop_event(self):
        assert self.norm_df.iloc[3]["_event"] == "AIRDROP"

    def test_column_mapping_reports_correct_originals(self):
        assert self.report.mapping["datetime"]   == "date"
        assert self.report.mapping["event_type"] == "type"
        assert self.report.mapping["asset"]      == "coin"
        assert self.report.mapping["quantity"]   == "units"
        assert self.report.mapping["amount_aud"] == "fiat_value"
        assert self.report.mapping["fee_aud"]    == "fee"

    def test_engine_processes_disposal(self):
        disp_df, _, _ = compute_cgt_fifo(self.norm_df)
        assert len(disp_df) == 1
        row = disp_df.iloc[0]
        # proceeds = 4500 - 4.5 = 4495.5
        # cost_base = (6000 + 6) * (0.1 / 0.2) = 3003
        assert row["proceeds_aud"]  == pytest.approx(4495.5)
        assert row["cost_base_aud"] == pytest.approx(3003.0)

    def test_staking_and_airdrop_are_acquisitions(self):
        # After processing, ETH staking and ADA airdrop should be in inventory
        disp_df, _, _ = compute_cgt_fifo(self.norm_df)
        # No SELL for ETH or ADA, so nothing in disposals for them
        assert "ETH" not in disp_df["asset"].tolist() if not disp_df.empty else True
        assert "ADA" not in disp_df["asset"].tolist() if not disp_df.empty else True


# ── Style D — price_aud fallback ──────────────────────────────────────────────

PRICE_FALLBACK_CSV = """
date,action,symbol,qty,price_aud,commission
2022-09-01T10:00:00Z,buy,BTC,0.3,20000.00,15.00
2023-03-15T14:00:00Z,sell,BTC,0.15,25000.00,12.50
"""

class TestStyleDPriceAudFallback:
    def setup_method(self):
        df = _csv_to_df(PRICE_FALLBACK_CSV)
        self.norm_df, self.report = normalize_ledger(df)

    def test_aud_fallback_flag_set(self):
        assert self.report.aud_fallback_used is True

    def test_amount_aud_calculated_correctly(self):
        # Row 0: 0.3 × 20000 = 6000
        assert self.norm_df.iloc[0]["_amount_aud"] == pytest.approx(6000.0)
        # Row 1: 0.15 × 25000 = 3750
        assert self.norm_df.iloc[1]["_amount_aud"] == pytest.approx(3750.0)

    def test_event_types(self):
        assert self.norm_df.iloc[0]["_event"] == "BUY"
        assert self.norm_df.iloc[1]["_event"] == "SELL"

    def test_asset_from_symbol_column(self):
        assert self.norm_df.iloc[0]["_asset"] == "BTC"

    def test_engine_produces_disposal_with_correct_gain(self):
        disp_df, _, _ = compute_cgt_fifo(self.norm_df)
        assert len(disp_df) == 1
        row = disp_df.iloc[0]
        # proceeds = 3750 - 12.5 = 3737.5
        # cost_base = (6000 + 15) * (0.15 / 0.3) = 3007.5
        assert row["proceeds_aud"]  == pytest.approx(3737.5)
        assert row["cost_base_aud"] == pytest.approx(3007.5)
        assert row["capital_gain_aud"] == pytest.approx(730.0)


# ── Style E — Missing required field → clear ValueError ───────────────────────

class TestMissingRequiredField:
    def test_missing_event_type_raises(self):
        df = _csv_to_df("""
date,coin,units,fiat_value
2022-01-01,BTC,0.1,5000.00
""")
        with pytest.raises(ValueError) as exc_info:
            normalize_ledger(df)
        msg = str(exc_info.value)
        assert "Missing required fields" in msg
        assert "event_type" in msg
        assert "Detected columns were" in msg

    def test_missing_datetime_raises(self):
        df = _csv_to_df("""
type,coin,units,fiat_value
buy,BTC,0.1,5000.00
""")
        with pytest.raises(ValueError) as exc_info:
            normalize_ledger(df)
        assert "datetime" in str(exc_info.value)

    def test_missing_amount_aud_no_price_fallback_raises(self):
        df = _csv_to_df("""
date,type,coin,units
2022-01-01,buy,BTC,0.1
""")
        with pytest.raises(ValueError) as exc_info:
            normalize_ledger(df)
        assert "amount_aud" in str(exc_info.value)

    def test_error_lists_detected_columns(self):
        df = _csv_to_df("""
weird_col_1,weird_col_2,weird_col_3
a,b,c
""")
        with pytest.raises(ValueError) as exc_info:
            normalize_ledger(df)
        msg = str(exc_info.value)
        # Should name the unresolved fields AND the actual columns found
        assert "Detected columns were:" in msg
        assert "weird_col_1" in msg

    def test_multiple_missing_fields_listed_together(self):
        df = _csv_to_df("""
datetime,asset,quantity
2022-01-01,BTC,0.1
""")
        with pytest.raises(ValueError) as exc_info:
            normalize_ledger(df)
        msg = str(exc_info.value)
        # Both event_type and amount_aud are missing
        assert "event_type" in msg
        assert "amount_aud" in msg


# ── Style F — Transfer out (non-CGT, must not appear as disposal) ─────────────

class TestTransferOutIgnored:
    def test_transfer_out_not_in_disposals(self):
        df = _csv_to_df("""
event_time_utc,event_type,asset,quantity,aud_total,fee_aud_inc_gst
2022-07-01T10:00:00+00:00,TRADE_BUY,BTC,1.0,30000.00,10.00
2022-09-01T12:00:00+00:00,TRANSFER_OUT,BTC,-0.5,0.00,0.00
""")
        norm_df, report = normalize_ledger(df)
        disp_df, _, _ = compute_cgt_fifo(norm_df)
        assert disp_df.empty, "TRANSFER_OUT must not produce a CGT disposal"

    def test_transfer_out_event_maps_correctly(self):
        df = _csv_to_df("""
date,type,coin,units,fiat_value,fee
2022-07-01,withdrawal,BTC,0.5,0,0
2022-07-02,deposit,ETH,1.0,1500,0
""")
        norm_df, _ = normalize_ledger(df)
        assert norm_df.iloc[0]["_event"] == "TRANSFER_OUT"
        assert norm_df.iloc[1]["_event"] == "TRANSFER_IN"


# ── Style G — CGT discount (held > 12 months) ─────────────────────────────────

class TestCGTDiscount:
    def test_discount_applied_for_long_hold(self):
        df = _csv_to_df("""
date,type,coin,units,fiat_value,fee
2020-01-01T00:00:00Z,buy,BTC,1.0,10000.00,10.00
2021-06-01T00:00:00Z,sell,BTC,1.0,20000.00,10.00
""")
        norm_df, _ = normalize_ledger(df)
        disp_df, _, _ = compute_cgt_fifo(norm_df)
        row = disp_df.iloc[0]
        assert bool(row["discount_eligible"]) is True
        # gross gain ≈ (20000 - 10) - (10000 + 10) = 9980
        # discounted = 9980 * 0.5 = 4990
        assert row["discounted_gain_aud"] == pytest.approx(row["capital_gain_aud"] * 0.5, abs=1.0)

    def test_no_discount_for_short_hold(self):
        df = _csv_to_df("""
date,type,coin,units,fiat_value,fee
2023-01-01T00:00:00Z,buy,ETH,1.0,2000.00,5.00
2023-06-01T00:00:00Z,sell,ETH,1.0,3000.00,5.00
""")
        norm_df, _ = normalize_ledger(df)
        disp_df, _, _ = compute_cgt_fifo(norm_df)
        row = disp_df.iloc[0]
        assert bool(row["discount_eligible"]) is False
        assert row["discounted_gain_aud"] == row["capital_gain_aud"]


# ── Column alias coverage ──────────────────────────────────────────────────────

class TestAliasResolution:
    """Spot-check that each logical field resolves from at least a few of its aliases."""

    @pytest.mark.parametrize("col_name", [
        "datetime_utc", "timestamp", "created_at", "trade_date",
    ])
    def test_datetime_aliases(self, col_name):
        df = pd.DataFrame({
            col_name:   ["2023-01-01T00:00:00Z"],
            "type":     ["buy"],
            "coin":     ["BTC"],
            "units":    [0.1],
            "fiat_value": [5000.0],
        })
        norm_df, report = normalize_ledger(df)
        assert report.mapping["datetime"] == col_name

    @pytest.mark.parametrize("col_name", [
        "event_type", "type", "transaction_type", "action", "side",
    ])
    def test_event_type_aliases(self, col_name):
        df = pd.DataFrame({
            "date":     ["2023-01-01T00:00:00Z"],
            col_name:   ["buy"],
            "coin":     ["BTC"],
            "units":    [0.1],
            "fiat_value": [5000.0],
        })
        norm_df, report = normalize_ledger(df)
        assert report.mapping["event_type"] == col_name

    @pytest.mark.parametrize("col_name", [
        "asset", "coin", "symbol", "base_asset",
    ])
    def test_asset_aliases(self, col_name):
        df = pd.DataFrame({
            "date":       ["2023-01-01T00:00:00Z"],
            "type":       ["buy"],
            col_name:     ["BTC"],
            "units":      [0.1],
            "fiat_value": [5000.0],
        })
        norm_df, report = normalize_ledger(df)
        assert report.mapping["asset"] == col_name

    @pytest.mark.parametrize("col_name", [
        "quantity", "qty", "units", "volume", "filled",
    ])
    def test_quantity_aliases(self, col_name):
        df = pd.DataFrame({
            "date":       ["2023-01-01T00:00:00Z"],
            "type":       ["buy"],
            "coin":       ["BTC"],
            col_name:     [0.1],
            "fiat_value": [5000.0],
        })
        norm_df, report = normalize_ledger(df)
        assert report.mapping["quantity"] == col_name

    @pytest.mark.parametrize("col_name", [
        "amount_aud", "aud_total", "fiat_value", "total_aud",
        "value_quote", "fiat_amount",
    ])
    def test_amount_aud_aliases(self, col_name):
        df = pd.DataFrame({
            "date":   ["2023-01-01T00:00:00Z"],
            "type":   ["buy"],
            "coin":   ["BTC"],
            "qty":    [0.1],
            col_name: [5000.0],
        })
        norm_df, report = normalize_ledger(df)
        assert report.mapping["amount_aud"] == col_name

    @pytest.mark.parametrize("col_name", [
        "fee_aud", "fee", "fee_amount", "commission",
    ])
    def test_fee_aud_aliases(self, col_name):
        df = pd.DataFrame({
            "date":       ["2023-01-01T00:00:00Z"],
            "type":       ["buy"],
            "coin":       ["BTC"],
            "qty":        [0.1],
            "fiat_value": [5000.0],
            col_name:     [5.0],
        })
        norm_df, report = normalize_ledger(df)
        assert report.mapping["fee_aud"] == col_name


# ── Normalisation report display ───────────────────────────────────────────────

class TestNormalizationReport:
    def test_to_display_text_contains_all_fields(self):
        df = _csv_to_df(COINSPOT_CSV)
        _, report = normalize_ledger(df)
        text = report.to_display_text()
        for field in ["datetime", "event_type", "asset", "quantity", "amount_aud", "fee_aud"]:
            assert field in text

    def test_to_display_text_contains_event_counts(self):
        df = _csv_to_df(COINSPOT_CSV)
        _, report = normalize_ledger(df)
        text = report.to_display_text()
        assert "BUY" in text
        assert "SELL" in text
        assert "STAKING" in text

    def test_aud_fallback_annotation_in_display(self):
        df = _csv_to_df(PRICE_FALLBACK_CSV)
        _, report = normalize_ledger(df)
        text = report.to_display_text()
        assert "price_aud" in text  # fallback column shown
