# HSLedger - Bank Reconciliation & Trading Analysis

A modular financial analysis tool for Headstart Financial.

## Features

- **Bank Reconciliation**: Match internal transfers, classify transactions
- **Trading & Crypto Analysis**: Capital gains calculations with Australian tax rules
- **Professional Web UI**: Clean, responsive interface
- **Modular Architecture**: Separated backend/frontend with comprehensive testing

## Quick Start

1. **Setup Environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r deployment/requirements.txt
   ```

2. **Generate Project Structure**
   ```bash
   python scripts/scaffold_generator.py
   ```

3. **Run Application**
   ```bash
   streamlit run frontend/app.py
   ```

## Architecture

- `backend/` - Core business logic
- `frontend/` - Streamlit UI components  
- `tests/` - Unit and integration tests
- `deployment/` - Docker and configuration files

## Usage

### Bank Reconciliation
1. Upload CSV files for each bank account
2. Review matched internal transfers
3. Confirm doubtful transactions
4. Download Excel report

### Trading Analysis
1. Upload trading history (CSV/JSON)
2. Review profit/loss summary
3. Apply Australian capital gains rules
4. Export results

## Development

Run tests:
```bash
pytest tests/
```

Build Docker image:
```bash
docker build -f deployment/Dockerfile -t hsledger .
```
