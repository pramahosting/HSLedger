import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta

# Settings
n_rows = 1000
exchanges = ["Binance", "Coinbase Pro", "Kraken", "Bitstamp", "Gemini"]
pairs = ["BTC/USDT","ETH/USDT","SOL/USDT","ADA/USDT","XRP/USDT","LTC/USDT","DOT/USDT","BNB/USDT"]
sides = ["buy","sell"]
order_types = ["market","limit"]

# Generate data
np.random.seed(42)
timestamps = [datetime.now() - timedelta(days=random.randint(0,365), minutes=random.randint(0,1440)) for _ in range(n_rows)]
df = pd.DataFrame({
    "trade_id": [f"t{i+1:08d}" for i in range(n_rows)],
    "order_id": [f"o{i+1000000000:09d}" for i in range(n_rows)],
    "timestamp": timestamps,
    "exchange": np.random.choice(exchanges, n_rows),
    "user_id": [f"user_{random.randint(1,5000):05d}" for _ in range(n_rows)],
    "pair": np.random.choice(pairs, n_rows),
    "side": np.random.choice(sides, n_rows),
    "order_type": np.random.choice(order_types, n_rows),
    "maker_taker": np.random.choice(["maker","taker"], n_rows),
    "price": np.round(np.random.uniform(0.5, 50000, n_rows), 4),
    "amount": np.round(np.random.uniform(0.01, 1000, n_rows), 8),
})
df["value_quote"] = np.round(df["price"] * df["amount"],8)
df["fee_rate"] = np.round(np.where(df["maker_taker"]=="maker", np.random.uniform(0.0002,0.001,n_rows), np.random.uniform(0.0007,0.0025,n_rows)),8)
df["fee_amount"] = np.round(df["value_quote"] * df["fee_rate"],8)
df["fee_currency"] = df["pair"].str.split("/").str[1]
df["filled_percent"] = 100
df["is_margin"] = False
df["wallet_before_quote"] = np.round(np.random.uniform(1000,10000,n_rows),2)
df["wallet_after_quote"] = np.round(df["wallet_before_quote"] - df["value_quote"] - df["fee_amount"],2)
df["note"] = ""

# Save CSV
df.to_csv("crypto_trades_sample_1000.csv", index=False)
print("Saved 1,000-row sample CSV as 'crypto_trades_sample_1000.csv'")
