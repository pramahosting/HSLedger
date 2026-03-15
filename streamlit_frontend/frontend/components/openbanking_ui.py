import os
from collections import defaultdict
from datetime import datetime, timedelta
import streamlit as st
import time
from urllib.parse import quote
from backend.open_banking.auth import get_access_token, get_client_access_token
from backend.open_banking.user import create_basiq_user_object, get_user
from backend.open_banking.auth_service import get_consents
from backend.open_banking.job_service import (
    get_job_status,
    get_accounts,
    get_account_details,
    get_transactions,
    get_transaction,
    create_statement,
)
from backend.open_banking.events_service import (
    list_events,
    get_event,
    list_event_types,
    get_event_type,
)
from backend.open_banking.csv_exporter import export_transactions_csv
from backend.utils.logger import logger


def render():
    st.subheader("Bank Reconciliation")

    if "account_details" not in st.session_state:
        st.session_state["account_details"] = {}
    if "post_consent_consents" not in st.session_state:
        st.session_state["post_consent_consents"] = None

    st.markdown(
        """
        <style>
        .pfm-card {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 12px;
            padding: 16px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.04);
        }
        .pfm-title {
            font-size: 18px;
            font-weight: 600;
            margin: 0 0 8px 0;
        }
        .pfm-muted {
            color: #6b7280;
            font-size: 12px;
        }
        .pfm-metric {
            font-size: 24px;
            font-weight: 700;
        }
        .pfm-pill {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 999px;
            background: #eef2ff;
            color: #4338ca;
            font-size: 12px;
            font-weight: 600;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    def format_money(value, currency):
        if value is None:
            return "N/A"
        try:
            amount = float(value)
        except (TypeError, ValueError):
            return str(value)
        return f"{amount:,.2f} {currency}" if currency else f"{amount:,.2f}"

    def parse_date(date_value):
        if not date_value:
            return None
        if isinstance(date_value, datetime):
            return date_value
        if isinstance(date_value, str):
            try:
                return datetime.fromisoformat(date_value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    def get_tx_category(tx):
        enrich = tx.get("enrich", {}) if isinstance(tx, dict) else {}
        anzsic = enrich.get("category", {}).get("anzsic", {}) if enrich else {}
        return (
            anzsic.get("subclass", {}).get("title")
            or anzsic.get("class", {}).get("title")
            or anzsic.get("group", {}).get("title")
            or anzsic.get("subdivision", {}).get("title")
            or anzsic.get("division", {}).get("title")
            or tx.get("class", {}).get("title")
            or tx.get("subClass", {}).get("title")
            or "Uncategorized"
        )

    def compute_metrics(transactions, accounts):
        now = datetime.utcnow()
        month_ago = now - timedelta(days=30)
        recent = []
        for tx in transactions:
            tx_date = parse_date(tx.get("postDate") or tx.get("transactionDate"))
            if tx_date and tx_date >= month_ago:
                recent.append(tx)

        total_income = 0.0
        total_spend = 0.0
        for tx in recent:
            try:
                amount = float(tx.get("amount") or 0)
            except (TypeError, ValueError):
                continue
            if amount >= 0:
                total_income += amount
            else:
                total_spend += abs(amount)

        savings_rate = 0.0
        if total_income > 0:
            savings_rate = max(0.0, (total_income - total_spend) / total_income * 100)

        net_worth = 0.0
        for account in accounts:
            try:
                net_worth += float(account.get("balance") or 0)
            except (TypeError, ValueError):
                continue

        return net_worth, total_income, total_spend, savings_rate

    # -----------------------------
    # Open Banking – Create User & Consent
    # -----------------------------
    st.markdown("### Connect Your Bank Account")

    email = st.text_input("Email")
    mobile = st.text_input("Mobile (+61...)")

    if st.button("Connect to Bank"):
        try:
            access_token = get_access_token()
            user = create_basiq_user_object(access_token, email=email, mobile=mobile)
            user_id = user["id"]
            consents = get_consents(user_id)
            client_token = get_client_access_token(user_id)
            redirect_url = os.getenv("BASIQ_CONSENT_REDIRECT_URL")
            consent_url = f"https://consent.basiq.io/home?userId={user_id}&token={client_token}"
            if redirect_url:
                consent_url = f"{consent_url}&redirect_uri={quote(redirect_url)}"

            # SHOW USER ID CLEARLY
            st.success("Basiq user created successfully")

            st.markdown("**Basiq User ID (copy this):**")
            st.code(user_id, language="text")

            # Save in session (for later use)
            st.session_state["basiq_user_id"] = user_id

            st.markdown("**Redirect to bank consent page:**")
            st.markdown(f"[Click here to select your bank and approve]({consent_url})", unsafe_allow_html=True)

            with st.expander("Sandbox test credentials"):
                st.markdown("Use the test bank: Hooli (sandbox)")
                st.markdown("- loginId: gavinBelson | password: hooli2016")
                st.markdown("- loginId: jared | password: django")
                st.markdown("- loginId: richard | password: tabsnotspaces")
                st.markdown("- loginId: Wentworth-Smith | password: whislter")
                st.markdown("- loginId: Whistler | password: ShowBox")

            st.markdown("**Existing Consents (if any):**")
            st.json(consents)

            st.info(
                "This is a sandbox Open Banking demo. "
                "Live bank data requires CDR approval."
            )

        except Exception as e:
            st.error(f"Failed to create user: {e}")

    # Check for jobIds from Basiq redirect
    query_params = st.query_params
    job_ids = query_params.get("jobIds", None)
    user_id = st.session_state.get("basiq_user_id")

    if job_ids and user_id:
        st.divider()
        st.markdown("### Bank Connection Status")

        job_ids_list = job_ids if isinstance(job_ids, list) else [job_ids]

        if "polling_in_progress" not in st.session_state:
            st.session_state["polling_in_progress"] = True
            st.session_state["job_completed"] = False
            st.session_state["job_error"] = None
            st.session_state["accounts"] = None

        if st.session_state["polling_in_progress"] and not st.session_state["job_completed"]:
            progress_bar = st.progress(0)
            status_text = st.empty()

            max_attempts = 60
            for _ in range(max_attempts):
                try:
                    job_id = job_ids_list[0]
                    job_status = get_job_status(job_id)
                    steps = job_status.get("steps", [])

                    verify_step = next((s for s in steps if s.get("title") == "verify-credentials"), None)
                    retrieve_step = next((s for s in steps if s.get("title") == "retrieve-accounts"), None)

                    if verify_step and verify_step.get("status") == "in-progress":
                        status_text.info("🔐 Verifying bank credentials...")
                        progress_bar.progress(25)
                    elif retrieve_step and retrieve_step.get("status") == "in-progress":
                        status_text.info("📊 Retrieving accounts...")
                        progress_bar.progress(50)
                    elif verify_step and verify_step.get("status") == "failed":
                        st.session_state["job_error"] = verify_step.get("result", {}).get("detail", "Verification failed")
                        st.session_state["polling_in_progress"] = False
                        break
                    elif retrieve_step and retrieve_step.get("status") == "failed":
                        st.session_state["job_error"] = retrieve_step.get("result", {}).get("detail", "Account retrieval failed")
                        st.session_state["polling_in_progress"] = False
                        break

                    all_complete = steps and all(s.get("status") == "success" for s in steps)
                    if all_complete:
                        status_text.success("✅ Bank connection successful!")
                        progress_bar.progress(100)
                        st.session_state["job_completed"] = True
                        st.session_state["polling_in_progress"] = False
                        st.session_state["accounts"] = get_accounts(user_id)
                        if st.session_state["accounts"]:
                            first_institution = st.session_state["accounts"][0].get("institution")
                            if isinstance(first_institution, dict):
                                st.session_state["institution_id"] = first_institution.get("id")
                            elif first_institution:
                                st.session_state["institution_id"] = first_institution
                        break

                    time.sleep(2)
                except Exception as e:
                    st.session_state["job_error"] = str(e)
                    st.session_state["polling_in_progress"] = False
                    break

        if st.session_state.get("job_error"):
            st.error(f"❌ Connection failed: {st.session_state['job_error']}")

        if st.session_state.get("job_completed") and st.session_state.get("accounts"):
            if st.session_state.get("post_consent_consents") is None:
                try:
                    st.session_state["post_consent_consents"] = get_consents(user_id)
                except Exception as e:
                    st.warning(f"Could not retrieve consents: {e}")

            st.markdown("### Consents")
            col_consents_left, col_consents_right = st.columns([1, 1])
            with col_consents_left:
                if st.button("Refresh Consents"):
                    try:
                        st.session_state["post_consent_consents"] = get_consents(user_id)
                    except Exception as e:
                        st.error(f"Failed to refresh consents: {e}")
            with col_consents_right:
                st.write("Latest consents after approval")

            if st.session_state.get("post_consent_consents"):
                st.json(st.session_state["post_consent_consents"])
            else:
                st.info("No consents found yet. Try refreshing.")

            st.divider()

            accounts = st.session_state["accounts"]

            if accounts:
                st.markdown("### Dashboard")

                if st.button("Load All Transactions"):
                    all_transactions = []
                    with st.spinner("Loading transactions across accounts..."):
                        for account in accounts:
                            account_id = account.get("id")
                            if not account_id:
                                continue
                            try:
                                all_transactions.extend(get_transactions(account_id))
                            except Exception as e:
                                logger.error(f"Failed to load transactions for account {account_id}: {e}")
                    st.session_state["transactions"] = all_transactions

                transactions = st.session_state.get("transactions") or []
                net_worth, total_income, total_spend, savings_rate = compute_metrics(transactions, accounts)

                col_a, col_b, col_c, col_d = st.columns(4)
                with col_a:
                    st.metric("Net Worth", format_money(net_worth, accounts[0].get("currency")))
                with col_b:
                    st.metric("Income (30d)", format_money(total_income, accounts[0].get("currency")))
                with col_c:
                    st.metric("Spending (30d)", format_money(total_spend, accounts[0].get("currency")))
                with col_d:
                    st.metric("Savings Rate", f"{savings_rate:.1f}%")

                st.write("")
                col_left, col_right = st.columns([1, 1])

                with col_left:
                    st.markdown(
                        """
                        <div class="pfm-card">
                            <div class="pfm-title">Accounts</div>
                            <div class="pfm-muted">Select an account to view details</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    account_list = []
                    for acc in accounts:
                        name = acc.get("name") or "Account"
                        account_no = acc.get("accountNumber") or acc.get("accountNo") or ""
                        last4 = account_no[-4:] if account_no else "----"
                        account_list.append(
                            {
                                "id": acc.get("id"),
                                "name": name,
                                "last4": last4,
                                "balance": acc.get("balance"),
                                "currency": acc.get("currency"),
                                "status": acc.get("status"),
                                "institution": acc.get("institution"),
                            }
                        )

                    if account_list and not st.session_state.get("selected_account_id"):
                        st.session_state["selected_account_id"] = account_list[0]["id"]

                    for acc in account_list:
                        is_selected = acc["id"] == st.session_state.get("selected_account_id")
                        border_color = "#4338ca" if is_selected else "#e5e7eb"
                        st.markdown(
                            f"""
                            <div class="pfm-card" style="border: 2px solid {border_color}; margin-bottom: 8px;">
                                <div style="font-weight: 600;">{acc['name']} (...{acc['last4']})</div>
                                <div class="pfm-muted">Balance: {format_money(acc['balance'], acc['currency'])}</div>
                                <div class="pfm-muted">Status: {acc['status'] or 'N/A'} | Institution: {acc['institution'] or 'N/A'}</div>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )
                        if st.button("Select", key=f"select_account_{acc['id']}"):
                            st.session_state["selected_account_id"] = acc["id"]

                with col_right:
                    st.markdown(
                        """
                        <div class="pfm-card">
                            <div class="pfm-title">Account Snapshot</div>
                            <div class="pfm-muted">Balance and status</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    selected_account_id = st.session_state.get("selected_account_id")
                    selected_account = None
                    if selected_account_id:
                        selected_account = next(
                            (acc for acc in accounts if acc.get("id") == selected_account_id),
                            None,
                        )

                    if selected_account:
                        st.session_state["selected_account"] = selected_account
                        account_id = selected_account.get("id")
                        account_details = st.session_state["account_details"].get(account_id)
                        detail_source = account_details or selected_account

                        institution_value = detail_source.get("institution")
                        if isinstance(institution_value, dict):
                            st.session_state["institution_id"] = institution_value.get("id")
                        elif institution_value:
                            st.session_state["institution_id"] = institution_value

                        if st.session_state.get("selected_account_id") != account_id:
                            st.session_state["selected_account_id"] = account_id

                        currency = detail_source.get("currency")
                        balance = detail_source.get("balance")
                        available_funds = detail_source.get("availableFunds")
                        credit_limit = detail_source.get("creditLimit")

                        st.metric("Balance", format_money(balance, currency))
                        st.metric("Available", format_money(available_funds, currency))
                        st.metric("Credit Limit", format_money(credit_limit, currency))
                        st.write(f"Status: {detail_source.get('status', 'N/A')}")
                        st.write(f"Type: {detail_source.get('class', {}).get('type', 'N/A')}")
                        st.write(f"Institution: {detail_source.get('institution', 'N/A')}")

                st.write("")

                selected_account_id = st.session_state.get("selected_account_id")
                selected_account = None
                if selected_account_id:
                    selected_account = next(
                        (acc for acc in accounts if acc.get("id") == selected_account_id),
                        None,
                    )

                if selected_account:
                    account_id = selected_account.get("id")
                    account_details = st.session_state["account_details"].get(account_id)
                    detail_source = account_details or selected_account
                    currency = detail_source.get("currency")

                    if st.button("Load Selected Account Transactions"):
                        try:
                            with st.spinner("Loading transactions for selected account..."):
                                st.session_state["transactions"] = get_transactions(account_id)
                        except Exception as e:
                            st.error(f"Failed to load transactions: {e}")

                    if st.button("Load Full Account Details"):
                        try:
                            with st.spinner("Loading account details..."):
                                details = get_account_details(account_id)
                                st.session_state["account_details"][account_id] = details
                                detail_source = details
                        except Exception as e:
                            st.error(f"Failed to load account details: {e}")

                    with st.expander("Account Details", expanded=False):
                        st.write(f"Product: {detail_source.get('class', {}).get('product', 'N/A')}")
                        st.write(f"Account Holder: {detail_source.get('accountHolder', 'N/A')}")
                        st.write(f"Currency: {currency or 'N/A'}")
                        st.write(f"Account Number: {detail_source.get('maskedNumber') or detail_source.get('accountNo') or detail_source.get('accountNumber') or 'N/A'}")
                        st.write(f"BSB: {detail_source.get('bsb', 'N/A')}")
                        st.write(f"Last Updated: {detail_source.get('lastUpdated', 'N/A')}")

                        if detail_source.get("meta", {}).get("creditCard"):
                            credit_card = detail_source["meta"]["creditCard"]
                            st.write("Credit Card Details")
                            st.write(f"Payment Due: {format_money(credit_card.get('paymentDueAmount'), credit_card.get('paymentCurrency') or currency)}")
                            st.write(f"Minimum Payment: {format_money(credit_card.get('minPaymentAmount'), credit_card.get('paymentCurrency') or currency)}")
                            st.write(f"Payment Due Date: {credit_card.get('paymentDueDate', 'N/A')}")

                        if detail_source.get("meta", {}).get("loan"):
                            loan = detail_source["meta"]["loan"]
                            st.write("Loan Details")
                            st.write(f"Start Date: {loan.get('startDate', 'N/A')}")
                            st.write(f"End Date: {loan.get('endDate', 'N/A')}")
                            st.write(f"Repayment Type: {loan.get('repaymentType', 'N/A')}")
                            st.write(f"Original Amount: {format_money(loan.get('originalLoanAmount'), loan.get('originalLoanCurrency') or currency)}")
                            st.write(f"Minimum Instalment: {format_money(loan.get('minInstalmentAmount'), currency)}")
                            st.write(f"Next Instalment Date: {loan.get('nextInstalmentDate', 'N/A')}")

                        if detail_source.get("meta", {}).get("fees"):
                            st.write("Fees")
                            for fee in detail_source["meta"]["fees"]:
                                amount = format_money(fee.get("amount"), fee.get("currency") or currency)
                                st.write(f"- {fee.get('name', 'Fee')}: {amount} ({fee.get('feeType', 'N/A')})")

                        if detail_source.get("meta", {}).get("features"):
                            st.write("Features")
                            for feature in detail_source["meta"]["features"]:
                                status = "active" if feature.get("isActivated") else "inactive"
                                st.write(f"- {feature.get('featureType', 'Feature')} ({status})")

                        if detail_source.get("transactionIntervals"):
                            st.write("Transaction Intervals")
                            for interval in detail_source["transactionIntervals"]:
                                st.write(f"From {interval.get('from')} to {interval.get('to')}")

                if transactions:
                    st.markdown("### Spending by Category")
                    category_totals = defaultdict(float)
                    for tx in transactions:
                        try:
                            amount = float(tx.get("amount") or 0)
                        except (TypeError, ValueError):
                            continue
                        if amount < 0:
                            category = get_tx_category(tx)
                            category_totals[category] += abs(amount)

                    if category_totals:
                        chart_data = {
                            "Category": list(category_totals.keys()),
                            "Spend": list(category_totals.values()),
                        }
                        st.bar_chart(chart_data, x="Category", y="Spend")
                    else:
                        st.info("No spending data available yet.")

                    st.markdown("### Recent Transactions")
                    tx_rows = []
                    for tx in transactions[:50]:
                        tx_rows.append(
                            {
                                "Date": tx.get("postDate", "N/A"),
                                "Description": tx.get("description", "N/A"),
                                "Amount": format_money(tx.get("amount"), tx.get("currency")),
                                "Status": tx.get("status", "N/A"),
                            }
                        )
                    st.dataframe(tx_rows, use_container_width=True)
                else:
                    st.info("Load transactions to see spending and recent activity.")
            else:
                st.info("No accounts found for this connection.")

    # -----------------------------
    # Retrieve User 
    # -----------------------------
    st.divider()
    st.markdown("### Retrieve Basiq User")

    default_user_id = st.session_state.get("basiq_user_id", "")

    user_id_input = st.text_input(
        "Enter Basiq User ID",
        value=default_user_id
    )

    if st.button("Retrieve User"):
        try:
            access_token = get_access_token()
            user_data = get_user(access_token, user_id_input)
            st.success("User retrieved successfully")
            st.json(user_data)
        except Exception as e:
            st.error(f"Failed to retrieve user: {e}")

    st.divider()
    st.markdown("### Retrieve Accounts")

    accounts_user_id = st.text_input(
        "Enter Basiq User ID for Accounts",
        value=default_user_id,
    )

    if st.button("Get Accounts"):
        try:
            st.session_state["debug_accounts"] = get_accounts(accounts_user_id)
            st.success("Accounts retrieved successfully")
        except Exception as e:
            st.error(f"Failed to retrieve accounts: {e}")

    debug_accounts = st.session_state.get("debug_accounts") or []
    if debug_accounts:
        account_labels = {}
        for acc in debug_accounts:
            name = acc.get("name") or "Account"
            account_no = acc.get("accountNumber") or acc.get("accountNo") or ""
            last4 = account_no[-4:] if account_no else "----"
            label = f"{name} (...{last4})"
            account_labels[label] = acc.get("id")

        selected_label = st.selectbox(
            "Select an account",
            options=list(account_labels.keys()),
            index=0 if account_labels else None,
            key="debug_accounts_select",
        )

        if st.button("Use Selected Account"):
            selected_account_id = account_labels.get(selected_label)
            st.session_state["selected_account_id"] = selected_account_id
            st.success(f"Selected account set: {selected_label}")
    else:
        st.info("No accounts loaded yet. Click Get Accounts to load them.")

    st.divider()
    st.markdown("### Retrieve Transaction")

    transaction_user_id = st.text_input(
        "Enter Basiq User ID for Transaction",
        value=default_user_id,

    )
    transaction_id_input = st.text_input(
        "Enter Transaction ID",
        key="debug_transaction_id",
    )

    if st.button("Retrieve Transaction"):
        try:
            transaction_data = get_transaction(transaction_user_id, transaction_id_input)
            st.success("Transaction retrieved successfully")
            st.json(transaction_data)
        except Exception as e:
            st.error(f"Failed to retrieve transaction: {e}")

    st.divider()
    st.markdown("### Create Statement")
    st.caption("Statement upload is not supported in sandbox (AU00000).")

    stored_institution_id = st.session_state.get("institution_id", "")
    statement_user_id = st.text_input(
        "Enter Basiq User ID for Statement",
        value=default_user_id,
   
    )
    institution_id_input = st.text_input(
        "Enter Institution ID",
        value=stored_institution_id,
        placeholder="e.g., AU01001",
        key="debug_statement_institution_id",
    )
    statement_file = st.file_uploader(
        "Upload PDF or CSV statement",
        type=["pdf", "csv"],
        key="debug_statement_file",
    )

    if st.button("Create Statement Job"):
        try:
            if not statement_file:
                raise ValueError("Please upload a PDF or CSV statement file")
            job_data = create_statement(
                statement_user_id,
                institution_id_input,
                statement_file.name,
                statement_file.getvalue(),
                statement_file.type,
            )
            st.session_state["statement_job_id"] = job_data.get("id")
            st.session_state["statement_job_user_id"] = statement_user_id
            st.success("Statement job created successfully")
            st.json(job_data)
        except Exception as e:
            st.error(f"Failed to create statement: {e}")

    statement_job_id = st.session_state.get("statement_job_id")
    statement_job_user_id = st.session_state.get("statement_job_user_id")

    if statement_job_id:
        st.write(f"Latest Statement Job ID: {statement_job_id}")

    if st.button("Check Statement Job Status"):
        try:
            if not statement_job_id:
                raise ValueError("No statement job ID available. Create a statement job first.")
            job_status = get_job_status(statement_job_id)
            st.json(job_status)

            steps = job_status.get("steps", [])
            all_complete = steps and all(step.get("status") == "success" for step in steps)
            if all_complete and statement_job_user_id:
                st.session_state["accounts"] = get_accounts(statement_job_user_id)
                st.success("Statement job completed. Accounts refreshed.")
            elif steps:
                st.info("Statement job still in progress. Check again shortly.")
        except Exception as e:
            st.error(f"Failed to check statement job: {e}")

    st.divider()
    st.markdown("### Events")
    st.caption("Events only cover the last 7 days.")

    events_filter = st.text_input(
        "Events filter",
        placeholder="e.g., user.id.eq(USER_ID) or event.type.eq(connection.updated)",
        key="events_filter",
    )

    if st.button("List Events"):
        try:
            events_data = list_events(events_filter or None)
            st.json(events_data)
            events = events_data.get("data") if isinstance(events_data, dict) else None
            institution_id = None
            if isinstance(events, list):
                for event in events:
                    data = event.get("data") if isinstance(event, dict) else None
                    if isinstance(data, dict):
                        institution_id = data.get("institutionId")
                        if institution_id:
                            break
                    meta = event.get("meta") if isinstance(event, dict) else None
                    if isinstance(meta, dict):
                        institution_id = meta.get("institutionId")
                        if institution_id:
                            break
            if institution_id:
                st.session_state["institution_id"] = institution_id
                st.success(f"Institution ID captured from events: {institution_id}")
        except Exception as e:
            st.error(f"Failed to list events: {e}")

    event_id_input = st.text_input(
        "Event ID",
        key="debug_event_id",
    )

    if st.button("Retrieve Event"):
        try:
            event_data = get_event(event_id_input)
            st.json(event_data)
        except Exception as e:
            st.error(f"Failed to retrieve event: {e}")

    if st.button("List Event Types"):
        try:
            event_types = list_event_types()
            st.json(event_types)
        except Exception as e:
            st.error(f"Failed to list event types: {e}")

    event_type_id_input = st.text_input(
        "Event Type ID",
        key="debug_event_type_id",
    )

    if st.button("Retrieve Event Type"):
        try:
            event_type_data = get_event_type(event_type_id_input)
            st.json(event_type_data)
        except Exception as e:
            st.error(f"Failed to retrieve event type: {e}")

    # st.divider()
    # st.markdown("### Demo Statement Export")

    # sample_tx = [
    #     {"date": "2025-12-01", "description": "Salary", "amount": 3000, "balance": 28000},
    #     {"date": "2025-12-03", "description": "Rent", "amount": -1500, "balance": 26500},
    # ]

    # if st.button("Download Demo CSV"):
    #     export_transactions_csv(sample_tx, "statement.csv")

    #     with open("statement.csv", "rb") as f:
    #         st.download_button(
    #             "Download CSV",
    #             f,
    #             file_name="bank_statement.csv",
    #             mime="text/csv"
    #         )
