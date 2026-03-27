import base64
import json
from datetime import date, datetime
from urllib import error, request

import streamlit as st
import streamlit.components.v1 as components

API_BASE_URL = "http://127.0.0.1:8000"


def _escape_pdf_text(text):
	value = str(text or "")
	value = value.replace("\\", "\\\\")
	value = value.replace("(", "\\(")
	value = value.replace(")", "\\)")
	return value


def _build_pdf_bytes(lines):
	content_lines = ["BT", "/F1 11 Tf", "40 790 Td", "14 TL"]
	for line in lines:
		content_lines.append(f"({_escape_pdf_text(line)}) Tj")
		content_lines.append("T*")
	content_lines.append("ET")
	content = "\n".join(content_lines).encode("latin-1", errors="replace")

	objects = []
	objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
	objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
	objects.append(
		b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
	)
	objects.append(b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream")
	objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

	pdf = bytearray(b"%PDF-1.4\n")
	offsets = [0]
	for idx, obj in enumerate(objects, start=1):
		offsets.append(len(pdf))
		pdf.extend(f"{idx} 0 obj\n".encode("ascii"))
		pdf.extend(obj)
		pdf.extend(b"\nendobj\n")

	xref_offset = len(pdf)
	pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
	pdf.extend(b"0000000000 65535 f \n")
	for off in offsets[1:]:
		pdf.extend(f"{off:010d} 00000 n \n".encode("ascii"))
	pdf.extend(
		f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode("ascii")
	)
	return bytes(pdf)


def _invoice_pdf_bytes(invoice, business):
	invoice_date = _parse_datetime(invoice.get("invoice_date"))
	due_date = _parse_datetime(invoice.get("due_date"))
	lines = [
		"Invoice",
		"",
		f"Invoice Number: {invoice.get('invoice_number', 'N/A')}",
		f"Status: {invoice.get('status', 'N/A')}",
		f"Date: {invoice_date.strftime('%Y-%m-%d') if invoice_date else 'N/A'}",
		f"Due Date: {due_date.strftime('%Y-%m-%d') if due_date else 'N/A'}",
		"",
		f"From: {business.get('name', 'N/A')}",
		f"Email: {business.get('email') or 'N/A'}",
		f"Phone: {business.get('phone') or 'N/A'}",
		f"Tax ID: {business.get('tax_id') or 'N/A'}",
		"",
		f"Bill To: {invoice.get('bill_to_name') or 'N/A'}",
		f"Bill To Email: {invoice.get('bill_to_email') or 'N/A'}",
		f"Bill To Phone: {invoice.get('bill_to_phone') or 'N/A'}",
		f"Bill To Address: {invoice.get('bill_to_address') or 'N/A'}",
		"",
		"Items:",
	]

	for item in invoice.get("items", []):
		description = item.get("description", "")
		qty = float(item.get("quantity", 0) or 0)
		unit_price = float(item.get("unit_price", 0) or 0)
		line_total = float(item.get("line_total", 0) or 0)
		lines.append(f"- {description} | Qty: {qty:g} | Unit: Rs. {unit_price:,.2f} | Total: Rs. {line_total:,.2f}")

	lines.extend(
		[
			"",
			f"Subtotal: Rs. {float(invoice.get('subtotal', 0) or 0):,.2f}",
			f"Tax: Rs. {float(invoice.get('tax_amount', 0) or 0):,.2f}",
			f"Discount: Rs. {float(invoice.get('discount_amount', 0) or 0):,.2f}",
			f"Total: Rs. {float(invoice.get('total_amount', 0) or 0):,.2f}",
		]
	)

	if invoice.get("notes"):
		lines.extend(["", "Notes:", str(invoice.get("notes"))])

	return _build_pdf_bytes(lines)


def _api_request(method, path, payload=None):
	body = None
	if payload is not None:
		body = json.dumps(payload).encode("utf-8")

	req = request.Request(
		url=f"{API_BASE_URL.rstrip('/')}{path}",
		data=body,
		headers={"Content-Type": "application/json"},
		method=method,
	)

	try:
		with request.urlopen(req, timeout=30) as resp:
			content = resp.read().decode("utf-8")
			return json.loads(content) if content else {}
	except error.HTTPError as exc:
		detail = exc.read().decode("utf-8", errors="replace")
		raise RuntimeError(f"API error {exc.code}: {detail}") from exc
	except error.URLError as exc:
		raise RuntimeError(f"Connection error: {exc}") from exc


def _parse_datetime(value):
	if not value:
		return None
	text = str(value).replace("Z", "+00:00")
	try:
		return datetime.fromisoformat(text)
	except ValueError:
		return None


def _get_next_invoice_number():
	try:
		result = _api_request("GET", "/invoice/next-number")
		return result.get("invoice_number", "INV-0001")
	except Exception:
		return "INV-0001"


def _list_businesses():
	return _api_request("GET", "/invoice/business")


def _create_business(payload):
	return _api_request("POST", "/invoice/business", payload)


def _get_business(business_id):
	return _api_request("GET", f"/invoice/business/{business_id}")


def _update_business(business_id, payload):
	return _api_request("PATCH", f"/invoice/business/{business_id}", payload)


def _delete_business(business_id):
	return _api_request("DELETE", f"/invoice/business/{business_id}")


def _create_invoice(payload):
	return _api_request("POST", "/invoice", payload)


def _list_invoices():
	return _api_request("GET", "/invoice")


def _update_invoice_status(invoice_id, status_value):
	return _api_request("PATCH", f"/invoice/{invoice_id}/status", {"status": status_value})


def _delete_invoice(invoice_id):
	return _api_request("DELETE", f"/invoice/{invoice_id}")


def _encode_logo_file(logo_file):
	if not logo_file:
		return None
	logo_bytes = logo_file.read()
	logo_b64 = base64.b64encode(logo_bytes).decode("utf-8")
	mime_type = logo_file.type or "image/png"
	return f"data:{mime_type};base64,{logo_b64}"


def _format_display_date(value):
	if value is None:
		return ""
	if isinstance(value, datetime):
		return value.strftime("%d/%m/%Y")
	if hasattr(value, "strftime"):
		return value.strftime("%d/%m/%Y")
	parsed = _parse_datetime(value)
	if parsed:
		return parsed.strftime("%d/%m/%Y")
	return str(value)


def _build_invoice_preview_html(inv_number, inv_date, inv_due, inv_ref, inv_terms, notes, business, bill_to, line_items, gst_rate=0.0):
	rows_html = ""
	for item in line_items:
		amount = float(item["quantity"]) * float(item["unit_price"])
		description = item["description"] or "<em style='color:#bbb'>-</em>"
		quantity = int(item["quantity"]) if item["quantity"] == int(item["quantity"]) else item["quantity"]
		rows_html += f"""
		<tr>
		  <td>{description}</td>
		  <td class='mono'>{quantity}</td>
		  <td class='mono'>Rs. {float(item['unit_price']):,.2f}</td>
		  <td class='mono'>Rs. {amount:,.2f}</td>
		</tr>"""

	subtotal = sum(float(item["quantity"]) * float(item["unit_price"]) for item in line_items)
	gst_amount = subtotal * (gst_rate / 100.0) if gst_rate else 0.0
	grand_total = subtotal + gst_amount
	inv_date_text = _format_display_date(inv_date)
	due_text = _format_display_date(inv_due)
	due_row = f"<tr><td class='lbl'>Due Date</td><td>{due_text}</td></tr>" if due_text else ""
    
	ref_row = f"<tr><td class='lbl'>Reference</td><td>{inv_ref}</td></tr>" if inv_ref else ""
	terms_row = f"<tr><td class='lbl'>Payment Terms</td><td>{inv_terms}</td></tr>" if inv_terms else ""

	business_rows = ""
	if business and business.get("tax_id"):
		business_rows += f"<p>ABN: {business['tax_id']}</p>"
	if business and business.get("address"):
		business_rows += f"<p>{business['address']}</p>"
	if business and business.get("phone"):
		business_rows += f"<p>{business['phone']}</p>"
	if business and business.get("email"):
		business_rows += f"<p>{business['email']}</p>"

	bill_rows = ""
	if bill_to.get("address"):
		bill_rows += f"<p>{bill_to['address']}</p>"
	if bill_to.get("phone"):
		bill_rows += f"<p>{bill_to['phone']}</p>"
	if bill_to.get("email"):
		bill_rows += f"<p>{bill_to['email']}</p>"

	business_name = business.get("name") if business else "<span class='empty'>[Business Name]</span>"
	bill_name = bill_to.get("name") or "<span class='empty'>[Client Name]</span>"

	if business and business.get("logo_url"):
		logo_html = (
			f"<img src='{business['logo_url']}' "
			"style='max-height:80px;max-width:180px;object-fit:contain;display:block'/>"
		)
	else:
		logo_html = """
		<div style="
			width:160px;height:70px;
			border:2px dashed #d0cec6;border-radius:8px;
			display:flex;flex-direction:column;
			align-items:center;justify-content:center;
			color:#ccc;font-size:11px;gap:5px;background:#fafaf8;">
			<span>No Logo</span>
		</div>"""

	notes_html = f"<div class='inv-notes'>{notes}</div>" if notes else ""

	return f"""<!DOCTYPE html>
<html>
<head>
<meta charset='utf-8'/>
<link href='https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=DM+Mono:wght@400;500&display=swap' rel='stylesheet'/>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'DM Sans', sans-serif; background: #f4f3ef; padding: 16px; }}
  .box {{ background: #fff; border: 1px solid #e2e0d8; border-radius: 10px; padding: 36px 42px; box-shadow: 0 1px 3px rgba(0,0,0,0.08), 0 4px 16px rgba(0,0,0,0.06); }}
  .top {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 18px; }}
  .biz-label {{ font-size: 10px; font-weight: 700; letter-spacing: 0.8px; text-transform: uppercase; color: #6b6b6b; margin-bottom: 6px; }}
  .biz-name {{ font-size: 15px; font-weight: 700; color: #1c1c1c; margin-bottom: 4px; }}
  .biz p {{ font-size: 12.5px; color: #444; line-height: 1.7; }}
  .logo-slot {{ display:flex; align-items:flex-start; }}
  hr {{ border: none; border-top: 1.5px solid #e2e0d8; margin: 14px 0 18px; }}
  .bill-row {{ display: flex; justify-content: space-between; margin-bottom: 20px; }}
  .bill-lbl {{ font-size: 10px; font-weight: 700; letter-spacing: 0.7px; text-transform: uppercase; color: #6b6b6b; margin-bottom: 6px; }}
  .bill p {{ font-size: 12.5px; line-height: 1.7; color: #333; }}
  .bill .cn {{ font-weight: 600; font-size: 13.5px; }}
  table.meta {{ border-collapse: collapse; }}
  table.meta td {{ font-size: 12.5px; padding: 2px 0; }}
  table.meta .lbl {{ font-weight: 600; color: #6b6b6b; text-align: right; padding-right: 12px; }}
  table.meta td:last-child {{ font-family: 'DM Mono', monospace; color: #1c1c1c; }}
  table.items {{ width: 100%; border-collapse: collapse; }}
  table.items th {{ background: #f7f6f2; padding: 9px 13px; text-align: left; font-size: 10.5px; font-weight: 700; letter-spacing: 0.7px; text-transform: uppercase; color: #6b6b6b; border: 1px solid #e2e0d8; }}
  table.items td {{ padding: 9px 13px; font-size: 12.5px; border: 1px solid #e2e0d8; color: #1c1c1c; }}
  table.items td.mono {{ font-family: 'DM Mono', monospace; }}
  .total-row td {{ background: #e8f4ef !important; font-weight: 700; color: #1a6b4a; font-family: 'DM Mono', monospace; font-size: 13.5px; }}
  .inv-notes {{ margin-top: 20px; font-size: 12px; color: #6b6b6b; line-height: 1.65; border-top: 1px solid #e2e0d8; padding-top: 14px; }}
  .empty {{ color: #bbb; font-style: italic; }}
</style>
</head>
<body>
<div class='box'>
  <div class='top'>
    <div class='biz'>
      <div class='biz-label'>Business Details</div>
      <div class='biz-name'>{business_name}</div>
      {business_rows}
    </div>
    <div class='logo-slot'>{logo_html}</div>
  </div>
  <hr/>
  <div class='bill-row'>
    <div class='bill'>
      <div class='bill-lbl'>Bill To</div>
      <p class='cn'>{bill_name}</p>
      {bill_rows}
    </div>
    <div>
      <table class='meta'>
        <tr><td class='lbl'>Invoice Number</td><td>{inv_number}</td></tr>
				<tr><td class='lbl'>Date</td><td>{inv_date_text}</td></tr>
        {due_row}{ref_row}{terms_row}
      </table>
    </div>
  </div>
  <table class='items'>
    <thead>
      <tr>
        <th style='width:44%'>Description</th>
        <th style='width:14%'>Quantity</th>
        <th style='width:20%'>Unit Price</th>
        <th style='width:22%'>Amount</th>
      </tr>
    </thead>
    <tbody>
      {rows_html}
      <tr style='border-top:2px solid #e2e0d8'>
        <td colspan='3' style='text-align:right;font-size:12.5px;color:#6b6b6b'>Subtotal</td>
        <td class='mono' style='font-size:12.5px'>Rs. {subtotal:,.2f}</td>
      </tr>
      {f"<tr><td colspan='3' style='text-align:right;font-size:12.5px;color:#6b6b6b'>GST ({gst_rate:.0f}%)</td><td class='mono' style='font-size:12.5px'>Rs. {gst_amount:,.2f}</td></tr>" if gst_rate else ""}
      <tr class='total-row'>
        <td colspan='3' style='text-align:right'>Total</td>
        <td>Rs. {grand_total:,.2f}</td>
      </tr>
    </tbody>
  </table>
  {notes_html}
</div>
</body>
</html>"""


def _render_invoice_preview(inv_number, inv_date, inv_due, inv_ref, inv_terms, notes, business, bill_to, apply_gst=False):
	html = _build_invoice_preview_html(
		inv_number=inv_number,
		inv_date=inv_date,
		inv_due=inv_due,
		inv_ref=inv_ref,
		inv_terms=inv_terms,
		notes=notes,
		business=business,
		bill_to=bill_to,
		line_items=st.session_state.invoice_line_items,
		gst_rate=10.0 if apply_gst else 0.0,
	)

	components.html(html, height=720, scrolling=True)


def _open_print_dialog(html_content):
	payload = json.dumps(html_content)
	components.html(
		f"""
		<script>
		const invoiceHtml = {payload};
		const win = window.open('', '_blank');
		if (win) {{
			win.document.open();
			win.document.write(invoiceHtml);
			win.document.close();
			setTimeout(() => {{
				win.focus();
				win.print();
			}}, 250);
		}}
		</script>
		""",
		height=0,
	)


def _render_business_settings_tab():
	current_user = st.session_state.get("user", {}) or {}
	role_names = [str(r).strip().lower() for r in current_user.get("roles", [])]
	is_admin = bool(current_user.get("is_admin", False) or ("admin" in role_names))
	if not is_admin:
		st.error("Access denied. Admin users only.")
		return

	try:
		businesses = _list_businesses()
	except Exception as exc:
		st.error(f"Failed to load businesses: {exc}")
		businesses = []

	create_tab, manage_tab = st.tabs(["Create Business", "Manage Businesses"])

	with create_tab:
		with st.form("invoice_create_business_form"):
			col1, col2 = st.columns(2)
			with col1:
				name = st.text_input("Business Name *", placeholder="Your Company Name")
				email = st.text_input("Email", placeholder="info@company.com")
				phone = st.text_input("Phone")
			with col2:
				tax_id = st.text_input("Tax ID / ABN *")
				website = st.text_input("Website")
				logo_file = st.file_uploader("Upload Logo", type=["png", "jpg", "jpeg", "svg"], key="invoice_logo_upload")

			address = st.text_input("Street Address")
			city_col, state_col, zip_col = st.columns(3)
			city = city_col.text_input("City")
			state = state_col.text_input("State")
			postal_code = zip_col.text_input("Postal Code")
			country = st.text_input("Country", value="Australia")

			submitted = st.form_submit_button("Save Business", use_container_width=True)
			if submitted:
				if not name or not tax_id:
					st.error("Business Name and Tax ID are required.")
				else:
					try:
						_create_business(
							{
								"name": name,
								"email": email or None,
								"phone": phone or None,
								"address": address or None,
								"city": city or None,
								"state": state or None,
								"postal_code": postal_code or None,
								"country": country or None,
								"tax_id": tax_id,
								"website": website or None,
								"logo_url": _encode_logo_file(logo_file),
							}
						)
						st.toast(f"✅ Business '{name}' created successfully!", icon="✅")
						st.rerun()
					except Exception as exc:
						st.error(f"Failed to create business: {exc}")

	with manage_tab:
		if not businesses:
			st.info("No businesses found. Create one in the first tab.")
			return

		for business in businesses:
			with st.expander(f"{business.get('name', 'N/A')} (ID: {business.get('id', 'N/A')})", expanded=False):
				col1, col2, col3 = st.columns([1, 1, 0.4])

				with col1:
					st.markdown("**Basic Information**")
					st.text(f"Email: {business.get('email') or 'N/A'}")
					st.text(f"Phone: {business.get('phone') or 'N/A'}")
					st.text(f"Tax ID: {business.get('tax_id') or 'N/A'}")
					st.text(f"Website: {business.get('website') or 'N/A'}")

				with col2:
					st.markdown("**Address**")
					st.text(f"{business.get('address') or 'N/A'}")
					st.text(f"{business.get('city') or ''}, {business.get('state') or ''} {business.get('postal_code') or ''}")
					st.text(f"{business.get('country') or 'N/A'}")

				with col3:
					if st.button("✏️ Edit", key=f"edit_business_{business.get('id')}", use_container_width=True):
						st.session_state.edit_business_id = business.get("id")
					if st.button("🗑️ Delete", key=f"delete_business_{business.get('id')}", use_container_width=True):
						try:
							_delete_business(business.get("id"))
							if st.session_state.get("edit_business_id") == business.get("id"):
								st.session_state.pop("edit_business_id", None)
							st.success(f"Business '{business.get('name', 'N/A')}' deleted successfully.")
							st.rerun()
						except Exception as exc:
							st.error(f"Failed to delete business: {exc}")

				if business.get("logo_url"):
					st.image(business["logo_url"], width=140)

				created_at = _parse_datetime(business.get("created_at"))
				updated_at = _parse_datetime(business.get("updated_at"))
				created_text = created_at.strftime('%Y-%m-%d %H:%M') if created_at else "N/A"
				updated_text = updated_at.strftime('%Y-%m-%d %H:%M') if updated_at else "N/A"
				st.caption(f"Created: {created_text} | Updated: {updated_text}")

		business_id = st.session_state.get("edit_business_id")
		if not business_id:
			return

		try:
			business = _get_business(business_id)
		except Exception as exc:
			st.error(f"Failed to load business details: {exc}")
			st.session_state.pop("edit_business_id", None)
			return

		st.divider()
		st.markdown(f"### Edit Business: {business.get('name', 'N/A')}")
		with st.form("edit_business_form"):
			col1, col2 = st.columns(2)

			with col1:
				name = st.text_input("Business Name", value=business.get("name") or "")
				email = st.text_input("Email", value=business.get("email") or "")
				phone = st.text_input("Phone", value=business.get("phone") or "")

			with col2:
				tax_id = st.text_input("Tax ID", value=business.get("tax_id") or "")
				website = st.text_input("Website", value=business.get("website") or "")
				logo_file = st.file_uploader(
					"Upload New Logo",
					type=["png", "jpg", "jpeg", "svg"],
					key="invoice_logo_upload_edit",
				)
				if logo_file:
					st.image(logo_file, width=100)
				elif business.get("logo_url"):
					st.image(business["logo_url"], width=100)

			col3, col4 = st.columns(2)
			with col3:
				address = st.text_input("Street Address", value=business.get("address") or "")
				city = st.text_input("City", value=business.get("city") or "")
			with col4:
				state = st.text_input("State/Province", value=business.get("state") or "")
				postal_code = st.text_input("Postal Code", value=business.get("postal_code") or "")

			country = st.text_input("Country", value=business.get("country") or "")
			button_col1, button_col2 = st.columns(2)
			save_edit = button_col1.form_submit_button("💾 Update Business", use_container_width=True)
			cancel_edit = button_col2.form_submit_button("❌ Cancel", use_container_width=True)

			if cancel_edit:
				st.session_state.pop("edit_business_id", None)
				st.rerun()

			if save_edit:
				try:
					_update_business(
						business_id,
						{
							"name": name or None,
							"email": email or None,
							"phone": phone or None,
							"address": address or None,
							"city": city or None,
							"state": state or None,
							"postal_code": postal_code or None,
							"country": country or None,
							"tax_id": tax_id or None,
							"website": website or None,
							"logo_url": _encode_logo_file(logo_file) or business.get("logo_url"),
						},
					)
					st.session_state.pop("edit_business_id", None)
					st.success(f"Business '{name}' updated successfully.")
					st.rerun()
				except Exception as exc:
					st.error(f"Failed to update business: {exc}")


def _render_invoice_tab():
	pending_print = st.session_state.pop("invoice_pending_print", None)
	if pending_print:
		print_html = _build_invoice_preview_html(
			inv_number=pending_print.get("inv_number"),
			inv_date=pending_print.get("inv_date"),
			inv_due=pending_print.get("inv_due"),
			inv_ref=pending_print.get("inv_ref", ""),
			inv_terms=pending_print.get("inv_terms", ""),
			notes=pending_print.get("notes", ""),
			business=pending_print.get("business") or {},
			bill_to=pending_print.get("bill_to") or {},
			line_items=pending_print.get("line_items") or [],
			gst_rate=10.0 if pending_print.get("apply_gst") else 0.0,
		)
		_open_print_dialog(print_html)
		st.info("Print dialog opened for the saved invoice.")

	try:
		businesses = _list_businesses()
	except Exception as exc:
		st.error(f"Failed to load businesses: {exc}")
		businesses = []

	business_options = {f"{b.get('name', 'N/A')} (ID: {b.get('id', 'N/A')})": b for b in businesses}
	selected_business = None

	left, right = st.columns([1, 1.1], gap="large")

	with left:
		st.markdown('<p class="sec-label">Business Details</p>', unsafe_allow_html=True)
		with st.container(border=True):
			if business_options:
				selected_label = st.selectbox("Select Business", options=list(business_options.keys()), key="invoice_selected_business")
				selected_business = business_options[selected_label]
				col1, col2 = st.columns(2)
				with col1:
					st.text(f"Email: {selected_business.get('email') or 'N/A'}")
					st.text(f"Phone: {selected_business.get('phone') or 'N/A'}")
					st.text(f"Address: {selected_business.get('address') or 'N/A'}")
				with col2:
					st.text(f"Tax ID: {selected_business.get('tax_id') or 'N/A'}")
					st.text(f"Website: {selected_business.get('website') or 'N/A'}")
					st.text(f"Country: {selected_business.get('country') or 'N/A'}")
				if selected_business.get("logo_url"):
					st.image(selected_business["logo_url"], width=120)
			else:
				st.warning("No businesses found. Create one in Business Settings.")

		st.markdown('<p class="sec-label">Bill To</p>', unsafe_allow_html=True)
		with st.container(border=True):
			bill_name = st.text_input("Client Name", key="invoice_bill_name")
			bill_address = st.text_input("Address", key="invoice_bill_address")
			bill_phone = st.text_input("Phone", key="invoice_bill_phone")
			bill_email = st.text_input("Email", key="invoice_bill_email")

		st.markdown('<p class="sec-label">Invoice Details</p>', unsafe_allow_html=True)
		with st.container(border=True):
			col_invoice, col_refresh, col_date = st.columns([2.5, 0.5, 2])
			with col_invoice:
				inv_number = st.text_input("Invoice Number", value=st.session_state.next_invoice_number, disabled=True)
			with col_refresh:
				st.write("")
				if st.button("🔄", key="invoice_refresh_number", use_container_width=True, help="Reload next invoice number"):
					st.session_state.next_invoice_number = _get_next_invoice_number()
					st.rerun()
			with col_date:
				inv_date = st.date_input("Date", value=date.today(), key="invoice_date")

			col_due, col_ref = st.columns(2)
			inv_due = col_due.date_input("Due Date", value=None, key="invoice_due_date")
			inv_ref = col_ref.text_input("Reference", placeholder="PO-1234", key="invoice_ref")
			inv_terms = st.text_input("Payment Terms", key="invoice_terms")

		st.markdown('<p class="sec-label">Line Items</p>', unsafe_allow_html=True)
		with st.container(border=True):
			to_delete = []
			for idx, item in enumerate(st.session_state.invoice_line_items):
				col_desc, col_qty, col_price, col_delete = st.columns([4, 1.5, 2, 0.8])
				item["description"] = col_desc.text_input("Description", value=item["description"], key=f"invoice_desc_{idx}")
				item["quantity"] = col_qty.number_input("Qty", min_value=0.0, step=1.0, value=float(item["quantity"]), key=f"invoice_qty_{idx}")
				item["unit_price"] = col_price.number_input("Unit Price", min_value=0.0, step=0.01, value=float(item["unit_price"]), key=f"invoice_price_{idx}")
				if len(st.session_state.invoice_line_items) > 1 and col_delete.button("Delete", key=f"invoice_delete_{idx}"):
					to_delete.append(idx)

			for idx in reversed(to_delete):
				st.session_state.invoice_line_items.pop(idx)
				st.rerun()

			if st.button("Add Line Item", use_container_width=True):
				st.session_state.invoice_line_items.append({"description": "", "quantity": 1, "unit_price": 0.0})
				st.rerun()

			subtotal = sum(item["quantity"] * item["unit_price"] for item in st.session_state.invoice_line_items)
			apply_gst = st.checkbox("Apply GST (10%)", value=True, key="invoice_apply_gst")
			gst_amount = subtotal * 0.10 if apply_gst else 0.0
			total = subtotal + gst_amount
			if apply_gst:
				st.markdown(
					f'<div style="text-align:right;margin-top:6px;font-size:13px;color:#555">'
					f'Subtotal: <b>Rs. {subtotal:,.2f}</b> &nbsp;+&nbsp; GST (10%): <b>Rs. {gst_amount:,.2f}</b></div>',
					unsafe_allow_html=True,
				)
			st.markdown(
				f'<div style="text-align:right;margin-top:4px">TOTAL &nbsp;<span class="badge-total">Rs. {total:,.2f}</span></div>',
				unsafe_allow_html=True,
			)

		st.markdown('<p class="sec-label">Payment Notes</p>', unsafe_allow_html=True)
		notes = st.text_area(
			"Notes",
			key="invoice_notes",
			height=90,
			value="Payment terms: Net 30 days. Multiple payment options accepted including bank transfer and credit card. Please include invoice number in payment reference.",
		)

		st.markdown('<p class="sec-label">Save Invoice</p>', unsafe_allow_html=True)
		if selected_business:
			if st.button("Save Invoice", type="primary", use_container_width=True):
				try:
					current_line_items = [
						{
							"description": item.get("description", ""),
							"quantity": float(item.get("quantity", 0) or 0),
							"unit_price": float(item.get("unit_price", 0) or 0),
						}
						for item in st.session_state.invoice_line_items
					]
					created = _create_invoice(
						{
							"invoice_number": inv_number,
							"business_id": selected_business["id"],
							"customer_id": None,
							"invoice_date": datetime.combine(inv_date, datetime.min.time()).isoformat(),
							"due_date": datetime.combine(inv_due, datetime.min.time()).isoformat() if inv_due else None,
							"items": current_line_items,
							"notes": notes,
							"payment_terms": inv_terms or None,
						"tax_percent": 10.0 if apply_gst else 0.0,
							"bill_to_name": bill_name or None,
							"bill_to_address": bill_address or None,
							"bill_to_phone": bill_phone or None,
							"bill_to_email": bill_email or None,
						}
					)
					st.session_state.next_invoice_number = _get_next_invoice_number()
					saved_number = created.get("invoice_number", inv_number)
					st.session_state.invoice_pending_print = {
						"inv_number": saved_number,
						"inv_date": inv_date,
						"inv_due": inv_due,
						"inv_ref": inv_ref,
						"inv_terms": inv_terms,
						"notes": notes,
						"business": dict(selected_business),
						"bill_to": {
							"name": bill_name,
							"address": bill_address,
							"phone": bill_phone,
							"email": bill_email,
						},
						"line_items": current_line_items,
						"apply_gst": apply_gst,
					}
					st.success(f"Invoice '{saved_number}' saved successfully.")
					st.balloons()
					st.rerun()
				except Exception as exc:
					st.error(f"Failed to save invoice: {exc}")
		else:
			st.warning("Select a business before saving the invoice.")

	with right:
		st.markdown('<p class="sec-label">Live Preview</p>', unsafe_allow_html=True)
		_render_invoice_preview(
			inv_number=st.session_state.next_invoice_number,
			inv_date=st.session_state.get("invoice_date", date.today()),
			inv_due=st.session_state.get("invoice_due_date"),
			inv_ref=st.session_state.get("invoice_ref", ""),
			inv_terms=st.session_state.get("invoice_terms", ""),
			notes=st.session_state.get("invoice_notes", ""),
			business=selected_business,
			bill_to={
				"name": st.session_state.get("invoice_bill_name", ""),
				"address": st.session_state.get("invoice_bill_address", ""),
				"phone": st.session_state.get("invoice_bill_phone", ""),
				"email": st.session_state.get("invoice_bill_email", ""),
			},
			apply_gst=st.session_state.get("invoice_apply_gst", True),
		)


def _render_invoice_history_tab():
	try:
		businesses = _list_businesses()
		invoices = _list_invoices()
	except Exception as exc:
		st.error(f"Failed to load invoices: {exc}")
		return

	business_options = {f"{b.get('name', 'N/A')} (ID: {b.get('id', 'N/A')})": b for b in businesses}
	business_by_id = {b.get("id"): b for b in businesses}

	col1, col2, col3 = st.columns(3)
	with col1:
		if business_options:
			selected_filter = st.selectbox(
				"Filter by Business",
				options=["All"] + list(business_options.keys()),
				key="invoice_history_business_filter",
			)
			selected_business = business_options.get(selected_filter) if selected_filter != "All" else None
		else:
			st.info("No businesses found")
			selected_business = None
	with col2:
		status_filter = st.selectbox(
			"Filter by Status",
			options=["All", "draft", "sent", "paid", "overdue"],
			key="invoice_history_status_filter",
		)
	with col3:
		search_invoice = st.text_input("Search Invoice Number", placeholder="INV-0001", key="invoice_history_search")

	if selected_business:
		invoices = [invoice for invoice in invoices if invoice.get("business_id") == selected_business.get("id")]
	if status_filter != "All":
		invoices = [invoice for invoice in invoices if invoice.get("status") == status_filter]
	if search_invoice:
		invoices = [
			invoice
			for invoice in invoices
			if search_invoice.lower() in str(invoice.get("invoice_number", "")).lower()
		]

	if not invoices:
		st.info("No invoices found matching your filters.")
		return

	col_stat1, col_stat2, col_stat3, col_stat4 = st.columns(4)
	col_stat1.metric("Total Invoices", len(invoices))
	col_stat2.metric("Total Amount", f"Rs. {sum(float(invoice.get('total_amount', 0) or 0) for invoice in invoices):,.2f}")
	col_stat3.metric("Paid", len([invoice for invoice in invoices if invoice.get("status") == "paid"]))
	col_stat4.metric("Drafts", len([invoice for invoice in invoices if invoice.get("status") == "draft"]))
	st.divider()

	status_icons = {"draft": "🔵", "sent": "🟡", "paid": "🟢", "overdue": "🔴"}
	for invoice in invoices:
		invoice_id = invoice.get("id")
		status_value = invoice.get("status", "draft")
		invoice_date = _parse_datetime(invoice.get("invoice_date"))
		due_date = _parse_datetime(invoice.get("due_date"))
		business = business_by_id.get(invoice.get("business_id"), {})
		with st.expander(
			f"{status_icons.get(status_value, '⚪')} {invoice.get('invoice_number', 'N/A')} • {business.get('name', 'N/A')} • Rs. {float(invoice.get('total_amount', 0) or 0):,.2f}",
			expanded=False,
		):
			col_a, col_b, col_c, col_d = st.columns(4)
			with col_a:
				st.markdown("**Invoice Details**")
				st.text(f"Number: {invoice.get('invoice_number', 'N/A')}")
				st.text(f"Date: {invoice_date.strftime('%Y-%m-%d') if invoice_date else 'N/A'}")
				st.text(f"Status: {status_value}")
				if due_date:
					st.text(f"Due: {due_date.strftime('%Y-%m-%d')}")
			with col_b:
				st.markdown("**From**")
				st.text(f"{business.get('name', 'N/A')}")
				st.text(f"{business.get('email') or 'N/A'}")
				st.text(f"{business.get('phone') or 'N/A'}")
				st.text(f"{business.get('tax_id') or 'N/A'}")
			with col_c:
				st.markdown("**Bill To**")
				st.text(f"{invoice.get('bill_to_name') or 'N/A'}")
				st.text(f"{invoice.get('bill_to_email') or 'N/A'}")
				st.text(f"{invoice.get('bill_to_phone') or 'N/A'}")
				st.text(f"{invoice.get('bill_to_address') or 'N/A'}")
			with col_d:
				st.markdown("**Amounts**")
				st.text(f"Subtotal: Rs. {float(invoice.get('subtotal', 0) or 0):,.2f}")
				st.text(f"Tax: Rs. {float(invoice.get('tax_amount', 0) or 0):,.2f}")
				discount_amount = float(invoice.get("discount_amount", 0) or 0)
				if discount_amount:
					st.text(f"Discount: Rs. {discount_amount:,.2f}")
				st.text(f"Total: Rs. {float(invoice.get('total_amount', 0) or 0):,.2f}")

			st.divider()

			items_data = [
				{
					"Description": item.get("description", ""),
					"Quantity": item.get("quantity", 0),
					"Unit Price": f"Rs. {float(item.get('unit_price', 0) or 0):,.2f}",
					"Total": f"Rs. {float(item.get('line_total', 0) or 0):,.2f}",
				}
				for item in invoice.get("items", [])
			]
			st.dataframe(items_data, use_container_width=True, hide_index=True)

			if invoice.get("notes"):
				st.markdown("**Notes**")
				st.text(invoice.get("notes"))

			button_col1, button_col2, button_col3, button_col4 = st.columns(4)
			with button_col1:
				status_options = ["draft", "sent", "paid", "overdue"]
				status_index = status_options.index(status_value) if status_value in status_options else 0
				new_status = st.selectbox(
					"Change Status",
					options=status_options,
					index=status_index,
					key=f"history_status_{invoice_id}",
				)
				if st.button("Update Status", key=f"history_update_{invoice_id}", use_container_width=True):
					try:
						_update_invoice_status(invoice_id, new_status)
						st.rerun()
					except Exception as exc:
						st.error(f"Failed to update status: {exc}")
			with button_col2:
				if st.button("🖨️ Print", key=f"history_print_{invoice_id}", use_container_width=True):
					print_html = _build_invoice_preview_html(
						inv_number=invoice.get("invoice_number", "N/A"),
						inv_date=invoice_date,
						inv_due=due_date,
						inv_ref=invoice.get("reference", ""),
						inv_terms=invoice.get("payment_terms", ""),
						notes=invoice.get("notes", ""),
						business=business,
						bill_to={
							"name": invoice.get("bill_to_name", ""),
							"address": invoice.get("bill_to_address", ""),
							"phone": invoice.get("bill_to_phone", ""),
							"email": invoice.get("bill_to_email", ""),
						},
						line_items=invoice.get("items", []),
					)
					_open_print_dialog(print_html)
			with button_col3:
				pdf_bytes = _invoice_pdf_bytes(invoice, business)
				file_name = f"{invoice.get('invoice_number', f'invoice_{invoice_id}')}.pdf"
				st.download_button(
					"📥 Download PDF",
					data=pdf_bytes,
					file_name=file_name,
					mime="application/pdf",
					key=f"history_pdf_{invoice_id}",
					use_container_width=True,
				)
			with button_col4:
				if st.button("🗑️ Delete", key=f"history_delete_{invoice_id}", use_container_width=True):
					try:
						_delete_invoice(invoice_id)
						st.rerun()
					except Exception as exc:
						st.error(f"Failed to delete invoice: {exc}")


def render():
	st.markdown(
		"""
		<style>
		@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&display=swap');

		html, body, [class*="css"] { font-family: 'DM Sans', sans-serif !important; }
		.main { background: #f4f3ef; }
		.sec-label {
			font-size: 10.5px; font-weight: 700; letter-spacing: 0.9px;
			text-transform: uppercase; color: #6b6b6b; margin-bottom: 4px;
		}
		.badge-total {
			display: inline-block; background: #e8f4ef;
			border: 1.5px solid #b8dece; border-radius: 8px;
			padding: 8px 20px; color: #1a6b4a; font-weight: 700;
			font-size: 17px; font-family: monospace;
		}
		div.stButton > button {
			background: #1a6b4a; color: white; border: none;
			font-weight: 600; border-radius: 8px;
		}
		div.stButton > button:hover { background: #155a3e; }
		</style>
		""",
		unsafe_allow_html=True,
	)

	if "invoice_line_items" not in st.session_state:
		st.session_state.invoice_line_items = [
			{"description": "Professional Services", "quantity": 1, "unit_price": 0.0}
		]

	if "next_invoice_number" not in st.session_state:
		st.session_state.next_invoice_number = _get_next_invoice_number()

	st.markdown("<h3 style='margin-top:0rem;margin-bottom:0.8rem'>Invoice Management</h3>", unsafe_allow_html=True)

	current_user = st.session_state.get("user", {}) or {}
	role_names = [str(r).strip().lower() for r in current_user.get("roles", [])]
	is_admin = bool(current_user.get("is_admin", False) or ("admin" in role_names))

	if is_admin:
		tab_invoice, tab_business, tab_history = st.tabs(["Invoice", "Business Settings", "Invoice History"])
	else:
		tab_invoice, tab_history = st.tabs(["Invoice", "Invoice History"])

	with tab_invoice:
		_render_invoice_tab()

	if is_admin:
		with tab_business:
			_render_business_settings_tab()

	with tab_history:
		_render_invoice_history_tab()
