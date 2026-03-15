# backend/open_banking/csv_exporter.py
import csv

def export_transactions_csv(transactions, file_path):
    with open(file_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Description", "Amount", "Balance"])

        for tx in transactions:
            writer.writerow([
                tx.get("transactionDate"),
                tx.get("description"),
                tx.get("amount"),
                tx.get("balance")
            ])
