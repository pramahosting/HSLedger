# backend/trading/capital_gains.py
import pandas as pd
from collections import deque
from datetime import timedelta


def calculate(trades_df: pd.DataFrame):
    """
    Compute realized gains using FIFO per symbol.
    Returns:
      - per_symbol_df: rows of realized lots with Date (sell date), Symbol, Quantity, Proceeds, Cost, Realized Gain, HoldingDays, Long Term (bool)
      - totals_df: DataFrame with net_taxable_gain (sum of realized gains)
    """
    if trades_df is None or trades_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    df = trades_df.sort_values("Date").reset_index(drop=True)

    lots = {}
    realized_rows = []

    for idx, r in df.iterrows():
        sym = r.get("Symbol")
        side = r.get("Side")
        qty = float(r.get("Quantity", 0.0))
        price = float(r.get("Price", 0.0))
        fee = float(r.get("Fee", 0.0))
        sell_date = r.get("Date")

        if sym not in lots:
            lots[sym] = deque()

        try:
            if side == "BUY":
                lot_cost_total = qty * price + fee
                lots[sym].append({
                    "qty": qty,
                    "unit_cost": (lot_cost_total / qty) if qty > 0 else 0.0,
                    "date": sell_date
                })
            elif side == "SELL":
                proceeds_total = qty * price - fee
                remaining = qty

                while remaining > 1e-12 and lots[sym]:
                    lot = lots[sym][0]
                    consume = min(remaining, lot["qty"])
                    cost_basis = consume * lot["unit_cost"]
                    realized_gain = (consume * price) - cost_basis - (fee * (consume / qty) if qty > 0 else 0.0)
                    holding_days = (sell_date - lot["date"]).days if pd.notnull(lot["date"]) else None
                    long_term = (holding_days is not None and holding_days > 365)

                    realized_rows.append({
                        "Symbol": sym,
                        "Sell Date": sell_date,
                        "Buy Date": lot["date"],
                        "Quantity": consume,
                        "Proceeds": round(consume * price, 2),
                        "Cost": round(cost_basis, 2),
                        "Realized Gain": round(realized_gain, 2),
                        "Holding Days": holding_days,
                        "Long Term": long_term
                    })

                    lot["qty"] -= consume
                    remaining -= consume
                    if lot["qty"] <= 1e-12:
                        lots[sym].popleft()

                if remaining > 1e-12:
                    realized_rows.append({
                        "Symbol": sym,
                        "Sell Date": sell_date,
                        "Buy Date": pd.NaT,
                        "Quantity": remaining,
                        "Proceeds": round(remaining * price, 2),
                        "Cost": 0.0,
                        "Realized Gain": round(remaining * price, 2),
                        "Holding Days": None,
                        "Long Term": False
                    })
        except Exception:
            # skip this trade but continue
            continue

    per_symbol_df = pd.DataFrame(realized_rows)
    if per_symbol_df.empty:
        totals_df = pd.DataFrame([{"net_realized_gain": 0.0}])
    else:
        totals_df = pd.DataFrame([{"net_realized_gain": per_symbol_df["Realized Gain"].sum()}])

    return per_symbol_df, totals_df
