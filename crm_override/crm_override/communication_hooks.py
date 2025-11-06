import frappe
from crm_override.crm_override.email_validator import validate_email_with_gemini, is_validation_enabled

# Module loaded confirmation - only visible during bench start in dev
# In production, this goes to supervisor logs


def find_or_create_lead(email, full_name, subject, doctype="CRM Lead"):
	"""
	Find existing lead by email or create new one.
	Prevents duplicates by checking email address.

	Args:
		email: Sender email address
		full_name: Sender full name
		subject: Email subject
		doctype: "CRM Lead" or "Rejected CRM Lead"

	Returns:
		Lead document (existing or newly created)
	"""
	logger = frappe.logger("email_validation", allow_site=True, file_count=5)

	# Check if lead with this email already exists
	existing_lead_name = frappe.db.get_value(
		doctype,
		filters={"email": email},
		fieldname="name"
	)

	if existing_lead_name:
		logger.info(f"🔗 Found existing {doctype}: {existing_lead_name} for {email}")
		return frappe.get_doc(doctype, existing_lead_name)

	# Create new lead
	logger.info(f"➕ Creating new {doctype} for {email}")

	lead = frappe.get_doc({
		"doctype": doctype,
		"email": email,
		"first_name": full_name or email.split("@")[0],
		"lead_name": full_name or email,
	})

	lead.flags.ignore_mandatory = True
	lead.insert(ignore_permissions=True)

	logger.info(f"✅ Created {doctype}: {lead.name}")

	return lead


def validate_before_linking_to_lead(doc, method=None):
	"""
	Hook: Communication.before_insert

	New Flow:
	1. Validates incoming emails with Gemini
	2. If VALID: Creates/finds CRM Lead and links Communication
	3. If INVALID: Creates/finds Rejected CRM Lead and links Communication

	Args:
		doc: Communication document
		method: Hook method name
	"""
	# Log to dedicated email validation log file
	logger = frappe.logger("email_validation", allow_site=True, file_count=5)
	logger.info("🔵 Hook triggered for Communication")
	logger.info(f"  Medium: {doc.communication_medium}")
	logger.info(f"  Direction: {doc.sent_or_received}")

	# Only validate incoming emails
	if doc.communication_medium != "Email":
		logger.info("  ❌ Skipping: Not an email")
		return

	if doc.sent_or_received != "Received":
		logger.info("  ❌ Skipping: Not received email")
		return

	# Skip if validation is disabled
	if not is_validation_enabled():
		logger.warning("  ❌ Validation disabled in settings, skipping")
		return

	logger.info("  ✅ All checks passed - proceeding with validation")

	# Get email details
	sender = doc.sender or "Unknown"
	subject = doc.subject or "No Subject"
	content = doc.content or ""
	sender_full_name = doc.sender_full_name or sender

	logger.info("="*80)
	logger.info("📧 NEW EMAIL DETECTED")
	logger.info(f"From: {sender}")
	logger.info(f"Subject: {subject}")
	logger.info("Validating with Gemini AI...")
	logger.info("="*80)

	# Prepare email content for validation
	raw_email = f"""From: {sender}
Subject: {subject}

{content}
"""

	# Validate with Gemini
	validation_result = validate_email_with_gemini(raw_email, sender, subject)
	doc.flags.ai_validation_result = validation_result

	# Find or create Lead based on validation result
	if validation_result == "Valid":
		# Valid email - create/find CRM Lead
		lead = find_or_create_lead(sender, sender_full_name, subject, "CRM Lead")

		doc.reference_doctype = "CRM Lead"
		doc.reference_name = lead.name

		logger.info("✅ VALID EMAIL")
		logger.info(f"Linked to CRM Lead: {lead.name}")

	else:
		# Invalid email - create/find Rejected CRM Lead
		lead = find_or_create_lead(sender, sender_full_name, subject, "Rejected CRM Lead")

		doc.reference_doctype = "Rejected CRM Lead"
		doc.reference_name = lead.name
		doc.email_status = "Spam"

		logger.warning("❌ INVALID EMAIL (Spam/Promotional)")
		logger.warning(f"Linked to Rejected CRM Lead: {lead.name}")
		logger.warning("="*80)


def add_validation_info_to_communication(doc, method=None):
	"""
	Hook: Communication.after_insert

	Adds validation metadata comment for spam emails
	"""
	if hasattr(doc.flags, 'ai_validation_result'):
		validation_result = doc.flags.ai_validation_result

		if validation_result == "Invalid":
			# Add a comment to the Communication
			comment = f"""
<div style="background: #fff3cd; border-left: 4px solid #ffc107; padding: 10px; margin: 10px 0;">
	<strong>🤖 AI Validation:</strong> This email was identified as <strong>spam/promotional</strong>
	<br><br>
	<em>Email status marked as Spam.</em>
</div>
			"""

			doc.add_comment("Info", comment)
