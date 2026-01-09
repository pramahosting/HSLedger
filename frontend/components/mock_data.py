# --------------------------------------------------------------------
# MOCK BANK CLIENT (for testing when Basiq is not configured)
# --------------------------------------------------------------------
import random
import string
from datetime import datetime, timedelta

class MockBankClient:
    def __init__(self):
        self.user_id = None
        self.accounts_cache = []

    def create_user(self, email: str, mobile: str = None) -> bool:
        self.user_id = f"mock_user_{email.split('@')[0]}"
        return True

    def list_connections(self):
        if not self.user_id:
            return []
        return [{"id": f"conn_{self.user_id}", "status": "active", "institution": {"shortName": "Mock Bank"}}]

    def list_accounts(self, bank_name: str = "Mock Bank", customer_number: str = "0000", password: str = "pass"):
        """Generate mock accounts with same structure as real Basiq accounts"""
        self.accounts_cache = []

        for i in range(3):  # Generate 3 mock accounts
            acc_number = ''.join(random.choices(string.digits, k=10))
            acc_id = f"mock_acc_{i}_{acc_number}"
            acc_type = random.choice(["Checking", "Savings", "Credit Card"])
            display_name = f"{acc_type} (Account)"
            balance = round(random.uniform(1000, 10000), 2)

            self.accounts_cache.append({
                "account_id": acc_id,
                "account_number": acc_number,
                "display_name": display_name,
                "institution": bank_name,
                "balance": balance,
                "connection_id": f"conn_{self.user_id}"
            })

        return self.accounts_cache

    def get_transactions_csv(self, account_id: str, from_date=None, to_date=None):
        """Generate dynamic mock transactions with realistic CSV structure"""
        from_date = from_date or (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        to_date = to_date or datetime.now().strftime("%Y-%m-%d")

        df = generate_mock_transactions(account_id)  # Keep your existing generator
        csv_buffer = io.BytesIO()
        df.to_csv(csv_buffer, index=False)
        csv_buffer.seek(0)
        csv_buffer.name = f"mock_account_{account_id}.csv"
        return csv_buffer
