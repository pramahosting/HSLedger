# backend/reconciliation/open_banking_connector.py

import requests
import base64
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json
from urllib.parse import urlencode, parse_qs, urlparse
import pandas as pd
import io


class OpenBankingConnector:
    """
    Connector for Australian Open Banking (CDR) API integration.
    Supports ANZ, CommBank, NAB, Westpac via CDR standards.
    """
    
    # Bank configurations (Sandbox endpoints for testing)
    BANK_CONFIGS = {
        "ANZ": {
            "name": "ANZ Bank",
            "auth_url": "https://sandbox.api.anz.com/oauth/authorize",
            "token_url": "https://sandbox.api.anz.com/oauth/token",
            "api_base": "https://sandbox.api.anz.com/cds-au/v1",
            "client_id": "",  # Set via environment or config
            "client_secret": "",
            "redirect_uri": "http://localhost:8501/callback",
            "scopes": ["bank:accounts.basic:read", "bank:transactions:read"]
        },
        "CommBank": {
            "name": "Commonwealth Bank",
            "auth_url": "https://api.cdr-sandbox.commbank.com.au/authorize",
            "token_url": "https://api.cdr-sandbox.commbank.com.au/token",
            "api_base": "https://api.cdr-sandbox.commbank.com.au/cds-au/v1",
            "client_id": "",
            "client_secret": "",
            "redirect_uri": "http://localhost:8501/callback",
            "scopes": ["bank:accounts.basic:read", "bank:transactions:read"]
        },
        "NAB": {
            "name": "National Australia Bank",
            "auth_url": "https://openbank.api.nab.com.au/sandbox/authorize",
            "token_url": "https://openbank.api.nab.com.au/sandbox/token",
            "api_base": "https://openbank.api.nab.com.au/sandbox/cds-au/v1",
            "client_id": "",
            "client_secret": "",
            "redirect_uri": "http://localhost:8501/callback",
            "scopes": ["bank:accounts.basic:read", "bank:transactions:read"]
        },
        "Westpac": {
            "name": "Westpac Banking Corporation",
            "auth_url": "https://digital-api.westpac.com.au/sandbox/authorize",
            "token_url": "https://digital-api.westpac.com.au/sandbox/token",
            "api_base": "https://digital-api.westpac.com.au/sandbox/cds-au/v1",
            "client_id": "",
            "client_secret": "",
            "redirect_uri": "http://localhost:8501/callback",
            "scopes": ["bank:accounts.basic:read", "bank:transactions:read"]
        }
    }
    
    def __init__(self, bank_name: str, client_id: str = None, client_secret: str = None):
        """
        Initialize the Open Banking connector.
        
        Args:
            bank_name: Name of the bank (ANZ, CommBank, NAB, Westpac)
            client_id: OAuth2 client ID (optional, can be set in config)
            client_secret: OAuth2 client secret (optional)
        """
        if bank_name not in self.BANK_CONFIGS:
            raise ValueError(f"Bank {bank_name} not supported. Choose from: {list(self.BANK_CONFIGS.keys())}")
        
        self.bank_name = bank_name
        self.config = self.BANK_CONFIGS[bank_name].copy()
        
        if client_id:
            self.config['client_id'] = client_id
        if client_secret:
            self.config['client_secret'] = client_secret
            
        self.access_token = None
        self.refresh_token = None
        self.token_expiry = None
        
    def generate_pkce_codes(self) -> tuple:
        """Generate PKCE code verifier and challenge for OAuth2."""
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8').rstrip('=')
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode('utf-8')).digest()
        ).decode('utf-8').rstrip('=')
        return code_verifier, code_challenge
    
    def get_authorization_url(self, state: str = None) -> tuple:
        """
        Generate authorization URL for user consent.
        
        Returns:
            tuple: (authorization_url, state, code_verifier)
        """
        if not state:
            state = secrets.token_urlsafe(32)
        
        code_verifier, code_challenge = self.generate_pkce_codes()
        
        params = {
            'response_type': 'code',
            'client_id': self.config['client_id'],
            'redirect_uri': self.config['redirect_uri'],
            'scope': ' '.join(self.config['scopes']),
            'state': state,
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256'
        }
        
        auth_url = f"{self.config['auth_url']}?{urlencode(params)}"
        return auth_url, state, code_verifier
    
    def exchange_code_for_token(self, authorization_code: str, code_verifier: str) -> Dict:
        """
        Exchange authorization code for access token.
        
        Args:
            authorization_code: Code received from authorization callback
            code_verifier: PKCE code verifier
            
        Returns:
            Dict containing access_token, refresh_token, etc.
        """
        data = {
            'grant_type': 'authorization_code',
            'code': authorization_code,
            'redirect_uri': self.config['redirect_uri'],
            'client_id': self.config['client_id'],
            'code_verifier': code_verifier
        }
        
        # Add client authentication
        auth_header = base64.b64encode(
            f"{self.config['client_id']}:{self.config['client_secret']}".encode()
        ).decode()
        
        headers = {
            'Authorization': f'Basic {auth_header}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        response = requests.post(
            self.config['token_url'],
            data=data,
            headers=headers
        )
        response.raise_for_status()
        
        token_data = response.json()
        self.access_token = token_data['access_token']
        self.refresh_token = token_data.get('refresh_token')
        
        # Calculate token expiry
        expires_in = token_data.get('expires_in', 3600)
        self.token_expiry = datetime.now() + timedelta(seconds=expires_in)
        
        return token_data
    
    def refresh_access_token(self) -> Dict:
        """Refresh the access token using refresh token."""
        if not self.refresh_token:
            raise ValueError("No refresh token available")
        
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': self.refresh_token,
            'client_id': self.config['client_id']
        }
        
        auth_header = base64.b64encode(
            f"{self.config['client_id']}:{self.config['client_secret']}".encode()
        ).decode()
        
        headers = {
            'Authorization': f'Basic {auth_header}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        response = requests.post(
            self.config['token_url'],
            data=data,
            headers=headers
        )
        response.raise_for_status()
        
        token_data = response.json()
        self.access_token = token_data['access_token']
        
        expires_in = token_data.get('expires_in', 3600)
        self.token_expiry = datetime.now() + timedelta(seconds=expires_in)
        
        return token_data
    
    def _ensure_valid_token(self):
        """Ensure access token is valid, refresh if needed."""
        if not self.access_token:
            raise ValueError("No access token. Please authenticate first.")
        
        if self.token_expiry and datetime.now() >= self.token_expiry:
            self.refresh_access_token()
    
    def _make_api_request(self, endpoint: str, params: Dict = None) -> Dict:
        """Make an authenticated API request."""
        self._ensure_valid_token()
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'x-v': '1',  # CDR API version
            'x-min-v': '1'
        }
        
        url = f"{self.config['api_base']}/{endpoint}"
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        
        return response.json()
    
    def get_accounts(self) -> List[Dict]:
        """
        Retrieve list of accounts.
        
        Returns:
            List of account dictionaries
        """
        try:
            data = self._make_api_request('banking/accounts')
            accounts = data.get('data', {}).get('accounts', [])
            
            # Format accounts for display
            formatted_accounts = []
            for acc in accounts:
                formatted_accounts.append({
                    'account_id': acc.get('accountId'),
                    'account_number': acc.get('accountNumber', 'N/A'),
                    'display_name': acc.get('displayName', acc.get('nickname', 'Unknown')),
                    'account_type': acc.get('productCategory', 'Unknown'),
                    'bsb': acc.get('bsb'),
                    'balance': acc.get('balance')
                })
            
            return formatted_accounts
        except Exception as e:
            print(f"Error fetching accounts: {e}")
            return []
    
    def get_transactions(self, account_id: str, from_date: datetime = None, 
                        to_date: datetime = None, page_size: int = 100) -> pd.DataFrame:
        """
        Retrieve transactions for a specific account.
        
        Args:
            account_id: Account ID to fetch transactions for
            from_date: Start date for transactions
            to_date: End date for transactions
            page_size: Number of transactions per page
            
        Returns:
            DataFrame with transactions
        """
        if not from_date:
            from_date = datetime.now() - timedelta(days=90)
        if not to_date:
            to_date = datetime.now()
        
        params = {
            'oldest-time': from_date.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'newest-time': to_date.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'page-size': page_size
        }
        
        all_transactions = []
        page = 1
        
        try:
            while True:
                params['page'] = page
                endpoint = f'banking/accounts/{account_id}/transactions'
                data = self._make_api_request(endpoint, params)
                
                transactions = data.get('data', {}).get('transactions', [])
                if not transactions:
                    break
                
                all_transactions.extend(transactions)
                
                # Check if there are more pages
                meta = data.get('meta', {})
                if page >= meta.get('totalPages', 1):
                    break
                
                page += 1
            
            # Convert to DataFrame
            if all_transactions:
                return self._format_transactions_to_dataframe(all_transactions, account_id)
            else:
                return pd.DataFrame()
                
        except Exception as e:
            print(f"Error fetching transactions: {e}")
            return pd.DataFrame()
    
    def _format_transactions_to_dataframe(self, transactions: List[Dict], account_id: str) -> pd.DataFrame:
        """Format CDR transactions to match HSLedger format."""
        formatted_transactions = []
        
        for txn in transactions:
            # Extract transaction date
            txn_date = txn.get('executionDateTime') or txn.get('postingDateTime')
            if txn_date:
                txn_date = pd.to_datetime(txn_date).strftime('%Y-%m-%d')
            
            # Determine debit/credit
            amount = float(txn.get('amount', 0))
            is_credit = txn.get('status') == 'POSTED' and amount > 0
            
            formatted_transactions.append({
                'date': txn_date,
                'description': txn.get('description', ''),
                'debit': abs(amount) if amount < 0 else '',
                'credit': amount if amount > 0 else '',
                'balance': txn.get('balance'),
                'transaction_id': txn.get('transactionId'),
                'reference': txn.get('reference', ''),
                'account_id': account_id
            })
        
        df = pd.DataFrame(formatted_transactions)
        
        # Sort by date
        if not df.empty and 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date')
        
        return df
    
    def export_transactions_to_csv(self, account_id: str, from_date: datetime = None,
                                   to_date: datetime = None) -> bytes:
        """
        Export transactions to CSV format.
        
        Returns:
            CSV content as bytes
        """
        df = self.get_transactions(account_id, from_date, to_date)
        
        if df.empty:
            return b''
        
        # Convert to CSV
        csv_buffer = io.BytesIO()
        df.to_csv(csv_buffer, index=False)
        csv_buffer.seek(0)
        
        return csv_buffer.getvalue()


class MockOpenBankingConnector(OpenBankingConnector):
    """
    Mock connector for testing without real bank credentials.
    Generates sample transaction data.
    """
    
    def __init__(self, bank_name: str = "ANZ"):
        super().__init__(bank_name, client_id="mock", client_secret="mock")
        self.access_token = "mock_token"
        self.token_expiry = datetime.now() + timedelta(hours=1)
    
    def get_authorization_url(self, state: str = None):
        """Mock authorization - no real OAuth needed."""
        return "http://mock-auth-url", "mock_state", "mock_verifier"
    
    def exchange_code_for_token(self, authorization_code: str, code_verifier: str):
        """Mock token exchange."""
        self.access_token = "mock_access_token"
        return {"access_token": self.access_token}
    
    def get_accounts(self) -> List[Dict]:
        """Return mock accounts."""
        return [
            {
                'account_id': 'acc_001',
                'account_number': '123456789',
                'display_name': 'Everyday Account',
                'account_type': 'TRANS_AND_SAVINGS_ACCOUNTS',
                'bsb': '012-003',
                'balance': 5420.50
            },
            {
                'account_id': 'acc_002',
                'account_number': '987654321',
                'display_name': 'Business Account',
                'account_type': 'BUSINESS_ACCOUNTS',
                'bsb': '012-003',
                'balance': 15780.25
            },
            {
                'account_id': 'acc_003',
                'account_number': '555666777',
                'display_name': 'Savings Account',
                'account_type': 'TRANS_AND_SAVINGS_ACCOUNTS',
                'bsb': '012-003',
                'balance': 25000.00
            }
        ]
    
    def get_transactions(self, account_id: str, from_date: datetime = None,
                        to_date: datetime = None, page_size: int = 100) -> pd.DataFrame:
        """Generate mock transactions."""
        if not from_date:
            from_date = datetime.now() - timedelta(days=90)
        if not to_date:
            to_date = datetime.now()
        
        # Generate sample transactions
        import random
        
        vendors = [
            'Woolworths', 'Coles', 'BP Service Station', 'Officeworks',
            'Telstra', 'AGL Energy', 'Amazon AU', 'Bunnings',
            'JB Hi-Fi', 'Harvey Norman', 'Kmart', 'Target',
            'Westfield Shopping', 'Myer', 'David Jones'
        ]
        
        transactions = []
        current_date = from_date
        
        while current_date <= to_date:
            # Generate 0-3 transactions per day
            num_txns = random.randint(0, 3)
            
            for _ in range(num_txns):
                is_credit = random.random() > 0.7  # 30% chance of credit
                amount = round(random.uniform(10, 500), 2)
                
                transactions.append({
                    'date': current_date.strftime('%Y-%m-%d'),
                    'description': random.choice(vendors) if not is_credit else 'Salary Payment',
                    'debit': amount if not is_credit else '',
                    'credit': amount if is_credit else '',
                    'balance': '',
                    'transaction_id': f'txn_{random.randint(10000, 99999)}',
                    'reference': f'REF{random.randint(1000, 9999)}',
                    'account_id': account_id
                })
            
            current_date += timedelta(days=1)
        
        df = pd.DataFrame(transactions)
        return df