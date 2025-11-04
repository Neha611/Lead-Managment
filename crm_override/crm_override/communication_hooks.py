import frappe
from crm_override.crm_override.email_validator import validate_email_with_gemini, is_validation_enabled

# Module loaded confirmation - only visible during bench start in dev
# In production, this goes to supervisor logs


def validate_before_linking_to_lead(doc, method=None):
	"""
	Hook: Communication.before_insert

	Validates incoming emails with Gemini before linking them to CRM Lead.
	- ALL emails are pulled and stored as Communications
	- Only VALID emails are linked to CRM Lead
	- Invalid (promotional/spam) emails are stored but unlinked

	Args:
		doc: Communication document
		method: Hook method name
	"""
	# Log to dedicated email validation log file
	logger = frappe.logger("email_validation", allow_site=True, file_count=5)
	logger.info("🔵 Hook triggered for Communication")
	logger.info(f"  Medium: {doc.communication_medium}")
	logger.info(f"  Direction: {doc.sent_or_received}")
	logger.info(f"  Ref DocType: {doc.reference_doctype}")
	logger.info(f"  Ref Name: {doc.reference_name}")

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

	# Only validate if being linked to a doctype (has reference)
	if not doc.reference_doctype or not doc.reference_name:
		logger.info("  ❌ Skipping: No reference doctype/name")
		return  # Silent skip for emails without references

	logger.info("  ✅ All checks passed - proceeding with validation")

	# Get email details
	sender = doc.sender or "Unknown"
	subject = doc.subject or "No Subject"
	content = doc.content or ""

	# Log to dedicated email validation log file
	logger = frappe.logger("email_validation", allow_site=True, file_count=5)

	logger.info("="*80)
	logger.info("📧 NEW EMAIL DETECTED")
	logger.info(f"From: {sender}")
	logger.info(f"Subject: {subject}")
	logger.info(f"Will link to: {doc.reference_doctype} - {doc.reference_name}")
	logger.info("Validating with Gemini AI...")
	logger.info("="*80)

	# Prepare email content for validation
	# Use the raw email if available, otherwise construct from fields
	raw_email = f"""From: {sender}
Subject: {subject}

{content}
"""
	logger.info("  📨 Prepared raw email content for validation")
	# Validate with Gemini
	validation_result = validate_email_with_gemini(raw_email, sender, subject)

	if validation_result == "Invalid":
		# Email is promotional/spam - UNLINK from reference doctype
		original_ref_doctype = doc.reference_doctype
		original_ref_name = doc.reference_name

		# Clear the reference to prevent linking to Lead
		doc.reference_doctype = None
		doc.reference_name = None

		# Mark as promotional
		doc.email_status = "Spam"  # Using existing field

		# Set custom flag for tracking
		doc.flags.ai_validation_result = "Invalid"
		doc.flags.original_reference_doctype = original_ref_doctype
		doc.flags.original_reference_name = original_ref_name

		# Flag to delete auto-created Lead after insert
		doc.flags.delete_auto_created_lead = True

		# Log the decision
		logger = frappe.logger("email_validation", allow_site=True, file_count=5)

		logger.warning("!"*80)
		logger.warning("❌ PROMOTIONAL/SPAM EMAIL DETECTED")
		logger.warning(f"Sender: {sender}")
		logger.warning(f"Subject: {subject}")
		logger.warning(f"AI Decision: INVALID (Promotional/Automated)")
		logger.warning(f"Action: UNLINKED from {original_ref_doctype}")
		logger.warning(f"Result: Communication saved, but NOT visible on Lead")
		logger.warning("!"*80)

		# Also create an error log for tracking
		frappe.log_error(
			title=f"Promotional Email Not Linked: {subject[:50]}",
			message=f"""
Sender: {sender}
Subject: {subject}
Original Reference: {original_ref_doctype} - {original_ref_name}

AI Validation: Invalid (Promotional/Automated)

This email was pulled successfully but NOT linked to {original_ref_doctype}
because it was identified as promotional or automated content.

The Communication record exists and can be reviewed in the Communication list.
			"""
		)

	else:
		# Email is valid - allow linking
		doc.flags.ai_validation_result = "Valid"

		logger = frappe.logger("email_validation", allow_site=True, file_count=5)

		logger.info("✅ PERSONAL EMAIL VALIDATED")
		logger.info(f"Sender: {sender}")
		logger.info(f"Subject: {subject}")
		logger.info(f"AI Decision: VALID (Personal Communication)")
		logger.info(f"Action: LINKED to {doc.reference_doctype}")
		logger.info(f"Result: Will appear on Lead timeline")
		logger.info("="*80)


def add_validation_info_to_communication(doc, method=None):
	"""
	Hook: Communication.after_insert

	Adds validation metadata to the communication comment/description
	Also deletes auto-created Leads for invalid emails
	"""
	if hasattr(doc.flags, 'ai_validation_result'):
		validation_result = doc.flags.ai_validation_result

		if validation_result == "Invalid":
			# Delete auto-created Lead if it was just created for this spam email
			if hasattr(doc.flags, 'delete_auto_created_lead') and doc.flags.delete_auto_created_lead:
				original_ref_doctype = doc.flags.get('original_reference_doctype')
				original_ref_name = doc.flags.get('original_reference_name')

				if original_ref_doctype == "CRM Lead" and original_ref_name:
					try:
						# Check if Lead exists
						if frappe.db.exists("CRM Lead", original_ref_name):
							lead = frappe.get_doc("CRM Lead", original_ref_name)

							# Safety check: Only delete if Lead was created very recently (within 30 seconds)
							# This ensures we only delete auto-created Leads, not existing ones
							from frappe.utils import now_datetime, get_datetime
							lead_age_seconds = (now_datetime() - get_datetime(lead.creation)).total_seconds()

							if lead_age_seconds <= 30:
								# Check if Lead has any other Communications
								other_comms = frappe.get_all(
									"Communication",
									filters={
										"reference_doctype": "CRM Lead",
										"reference_name": original_ref_name,
										"name": ["!=", doc.name]
									}
								)

								# Only delete if no other Communications exist
								if not other_comms:
									logger = frappe.logger("email_validation", allow_site=True, file_count=5)
									logger.warning(f"🗑️  Deleting auto-created Lead: {original_ref_name}")

									frappe.delete_doc("CRM Lead", original_ref_name, force=1, ignore_permissions=True)
									frappe.db.commit()

									logger.warning(f"✅ Lead {original_ref_name} deleted successfully")
								else:
									logger = frappe.logger("email_validation", allow_site=True, file_count=5)
									logger.info(f"⚠️  Not deleting Lead {original_ref_name} - has other Communications")
							else:
								logger = frappe.logger("email_validation", allow_site=True, file_count=5)
								logger.info(f"⚠️  Not deleting Lead {original_ref_name} - created {lead_age_seconds}s ago (not auto-created)")

					except Exception as e:
						logger = frappe.logger("email_validation", allow_site=True, file_count=5)
						logger.error(f"❌ Failed to delete Lead {original_ref_name}: {str(e)}")

			# Add a comment to the Communication
			comment = f"""
<div style="background: #fff3cd; border-left: 4px solid #ffc107; padding: 10px; margin: 10px 0;">
	<strong>🤖 AI Validation:</strong> This email was identified as <strong>promotional/automated</strong>
	and was not linked to {doc.flags.get('original_reference_doctype', 'reference document')}.
	<br><br>
	<em>You can review this email and manually link it if needed.</em>
</div>
			"""

			doc.add_comment("Info", comment)
