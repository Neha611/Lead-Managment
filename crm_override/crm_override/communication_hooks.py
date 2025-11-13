import frappe
from crm_override.crm_override.email_validator import validate_email_with_gemini, is_validation_enabled
from crm_override.crm_override.broadcast_utils import add_lead_to_segment

# Module loaded confirmation - only visible during bench start in dev
# In production, this goes to supervisor logs


# Legacy function - kept for reference
# def create_audit_record(communication_doc, lead_doctype, lead_name, category, reason, validation_method="Auto"):
# 	"""
# 	Create an Email Validation Audit record for tracking.
#
# 	Args:
# 		communication_doc: Communication document
# 		lead_doctype: Type of lead created (CRM Lead/Non Lead/Query)
# 		lead_name: Name of the lead record
# 		category: Validation category (Lead/Non Lead/Query)
# 		reason: AI validation reason
# 		validation_method: "Auto" or "Manual" (default: "Auto")
# 	"""
# 	try:
# 		audit = frappe.get_doc({
# 			"doctype": "Email Validation Audit",
# 			"communication": communication_doc.name,
# 			"lead_doctype": lead_doctype,
# 			"lead_name": lead_name,
# 			"category": category,
# 			"validation_reason": reason,
# 			"sender_email": communication_doc.sender,
# 			"subject": communication_doc.subject,
# 			"validated_on": frappe.utils.now(),
# 			"validation_method": validation_method
# 		})
# 		audit.insert(ignore_permissions=True)
# 		frappe.db.commit()
# 	except Exception as e:
# 		frappe.logger().error(f"Failed to create audit record: {str(e)}")

def create_audit_record(communication_doc, lead_doctype, lead_name, category, reason, validation_method="Auto"):
    """
    Enhanced version of create_audit_record with better error handling, logging and database checks.

    Args:
        communication_doc: Communication document
        lead_doctype: Type of lead created (CRM Lead/Non Lead/Query)
        lead_name: Name of the lead record
        category: Validation category (Lead/Non Lead/Query)
        reason: AI validation reason
        validation_method: "Auto" or "Manual" (default: "Auto")
    """
    # Get dedicated logger
    logger = frappe.logger("email_validation", allow_site=True, file_count=5)
    
    try:
        # Validate communication doc exists in database
        if not communication_doc or not communication_doc.name:
            logger.error("Communication document is None or missing name!")
            return
            
        if not frappe.db.exists("Communication", communication_doc.name):
            # Try to commit in case it's just not committed yet
            frappe.db.commit()
            if not frappe.db.exists("Communication", communication_doc.name):
                logger.error(f"Communication doc {communication_doc.name} does not exist in database!")
                return
                
        # Validate lead exists
        if not frappe.db.exists(lead_doctype, lead_name):
            logger.error(f"Lead {lead_name} of type {lead_doctype} does not exist!")
            return

        # Log the attempt
        logger.info(f"Creating audit record for communication: {communication_doc.name}")
        logger.info(f"Lead: {lead_doctype} - {lead_name}")
        logger.info(f"Category: {category}")

        # Create and insert audit record
        try:
            audit = frappe.get_doc({
                "doctype": "Email Validation Audit",
                "communication": communication_doc.name,
                "sender_email": communication_doc.sender,
                "subject": communication_doc.subject or "No Subject",
                "validated_on": frappe.utils.now(),
                "validation_method": validation_method,
                "category": category,
                "validation_reason": reason,
                "lead_doctype": lead_doctype,
                "lead_name": lead_name
            })

            # Insert with ignore_permissions (this will automatically validate)
            audit.insert(ignore_permissions=True)
            frappe.db.commit()

            logger.info(f"✅ Successfully created audit record: {audit.name}")
            return audit

        except Exception as e:
            logger.error(f"❌ Failed to create audit record:")
            logger.error(f"   Error Type: {type(e).__name__}")
            logger.error(f"   Error Message: {str(e)}")
            logger.error(f"   Traceback: {frappe.get_traceback()}")
            frappe.db.rollback()
            raise
            
    except Exception as e:
        logger.error("Unexpected error in create_audit_record:")
        logger.error(f"Error Type: {type(e).__name__}")
        logger.error(f"Error Message: {str(e)}")
        if hasattr(e, "args"):
            logger.error(f"Error Args: {e.args}")
        frappe.db.rollback()
        # Re-raise the exception after logging
        raise


@frappe.whitelist(allow_guest=True, methods=["POST", "GET"])
def api_test_endpoint():
    """
    Simple test endpoint to verify API is working.
    """
    return {
        "status": "success",
        "message": "API is working correctly",
        "timestamp": frappe.utils.now()
    }

@frappe.whitelist(allow_guest=True, methods=["POST", "GET"])
def api_find_or_create_lead(email, full_name=None, subject=None, doctype="CRM Lead", organization=None, mobile_no=None, tags=None, job_title=None, lead_name=None):
    """
    API endpoint to find or create a lead.
    Expects: email (str), full_name (str), subject (str), doctype (str), tags (list or comma-separated str), organization (str)
    """
    frappe.flags.ignore_csrf = True
    if tags and isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    lead = find_or_create_lead(email, full_name, subject, doctype, tags=tags, organization=organization, mobile_no=mobile_no, job_title=job_title, lead_name=lead_name)
    return {"lead_name": lead.name, "doctype": doctype}
# tags, organization, products, market, lead_type
def find_or_create_lead(email, full_name, subject, doctype="CRM Lead", tags=None, organization=None, products=None, market=None, lead_type=None, mobile_no=None, job_title=None, lead_name=None, lead_role=None):
	"""
	Find existing lead by email or create new one with row-level locking to prevent duplicates.
	Uses SELECT FOR UPDATE with retry logic for race conditions.
	Performs upsert operation - updates existing lead with new field values.

	Args:
		email: Sender email address
		full_name: Sender full name
		subject: Email subject
		doctype: "CRM Lead" or "Non Lead"
		tags: List of tags from AI validation (optional)
		organization: Organization name (optional)

	Returns:
		Lead document (existing or newly created)
	"""
	logger = frappe.logger("email_validation", allow_site=True, file_count=5)
	max_retries = 3
	for attempt in range(max_retries):
		try:
			# Check if lead exists WITH row-level locking
			# FOR UPDATE locks the row/index entry if found, or locks the gap if not found
			existing_lead = frappe.db.sql(f"""
				SELECT name
				FROM `tab{doctype}`
				WHERE email = %s
				FOR UPDATE
			""", (email,))

			if existing_lead:
				existing_lead_name = existing_lead[0][0]
				logger.info(f"🔗 Found existing {doctype}: {existing_lead_name} for {email}")
				lead = frappe.get_doc(doctype, existing_lead_name)

				# Upsert operation - update fields with new values if provided
				updated = False

				# Update tags
				if tags:
					existing_tags = lead.get("custom_tags") or ""
					# Parse existing tags (comma-separated)
					existing_tags_list = [t.strip() for t in existing_tags.split(",") if t.strip()] if existing_tags else []
					# Merge with new tags, avoiding duplicates
					merged_tags = list(set(existing_tags_list + tags))
					new_tags_value = ", ".join(merged_tags)
					if lead.custom_tags != new_tags_value:
						lead.custom_tags = new_tags_value
						updated = True

				# Update organization if provided
				if organization:
					if lead.get("organization") != organization:
						lead.organization = organization
						updated = True
						logger.info(f"📝 Updating organization for {doctype}: {lead.name} - Organization: {organization}")

				if mobile_no:
					if lead.get("mobile_no") != mobile_no:
						lead.mobile_no = mobile_no
						updated = True
				if job_title:
					if lead.get("job_title") != job_title:
						lead.job_title = job_title
						updated = True
				if lead_type:
					if lead.get("lead_type") != lead_type:
						lead.lead_type = lead_type
						updated = True
				if lead_role:
					if lead.get("lead_role") != lead_role:
						lead.lead_role = lead_role
						updated = True

				# Update full_name if provided and different
				if full_name and lead.get("lead_name") != full_name:
					lead.lead_name = full_name
					if hasattr(lead, "first_name"):
						lead.first_name = full_name
					updated = True

				# Save only if something was updated
				if updated:
					lead.flags.ignore_mandatory = True
					lead.save(ignore_permissions=True)
					logger.info(f"📝 Updated {doctype}: {lead.name} - Tags: {lead.custom_tags}, Organization: {lead.get('organization', 'N/A')}")
				else:
					logger.info(f"ℹ️ No updates needed for {doctype}: {lead.name}")

				return lead

			# No existing lead found, create new one
			logger.info(f"➕ Creating new {doctype} for {email} (attempt {attempt + 1}/{max_retries})")

			lead_data = {
				"doctype": doctype,
				"email": email,
				"first_name": full_name or email.split("@")[0],
				"lead_name": full_name or email,
				"custom_tags": ", ".join(tags) if tags else ""
			}

			# Add organization if provided
			if organization:
				lead_data["organization"] = organization

			if mobile_no:
				lead_data["mobile_no"] = mobile_no

			if job_title:	
				lead_data["job_title"] = job_title

			if lead_type:
				lead_data["lead_type"] = lead_type

			if products:
				lead_data["custom_products"] = products

			if market:
				lead_data["market"] = market
			
			if lead_role:
				lead_data["lead_role"] = lead_role

			lead = frappe.get_doc(lead_data)

			lead.flags.ignore_mandatory = True
			lead.insert(ignore_permissions=True)

			logger.info(f"✅ Created {doctype}: {lead.name} with tags: {lead.custom_tags}, organization: {lead.get('organization', 'N/A')}")
			return lead

		except frappe.DuplicateEntryError:
			# Another process created the lead between our SELECT and INSERT
			logger.warning(f"⚠️ Duplicate entry detected for {email}, retrying (attempt {attempt + 1}/{max_retries})")

			# Try to get the existing lead
			existing_lead_name = frappe.db.get_value(doctype, {"email": email}, "name")
			if existing_lead_name:
				logger.info(f"🔄 Retrieved existing lead after duplicate: {existing_lead_name}")
				lead = frappe.get_doc(doctype, existing_lead_name)

				# Upsert operation - update fields with new values if provided
				updated = False

				# Update tags
				if tags:
					existing_tags = lead.get("custom_tags") or ""
					existing_tags_list = [t.strip() for t in existing_tags.split(",") if t.strip()] if existing_tags else []
					merged_tags = list(set(existing_tags_list + tags))
					new_tags_value = ", ".join(merged_tags)
					if lead.custom_tags != new_tags_value:
						lead.custom_tags = new_tags_value
						updated = True

				# Update organization if provided
				if organization:
					if lead.get("organization") != organization:
						lead.organization = organization
						updated = True
				
				if mobile_no:
					if lead.get("mobile_no") != mobile_no:
						lead.mobile_no = mobile_no
						updated = True
				if job_title:
					if lead.get("job_title") != job_title:
						lead.job_title = job_title
						updated = True
				if lead_role:
					if lead.get("lead_role") != lead_role:
						lead.lead_role = lead_role
						updated = True

				# Update full_name if provided and different
				if full_name and lead.get("lead_name") != full_name:
					lead.lead_name = full_name
					if hasattr(lead, "first_name"):
						lead.first_name = full_name
					updated = True

				# Save only if something was updated
				if updated:
					lead.flags.ignore_mandatory = True
					lead.save(ignore_permissions=True)
					logger.info(f"📝 Updated {doctype}: {lead.name} - Tags: {lead.custom_tags}, Organization: {lead.get('organization', 'N/A')}")

				return lead

			if attempt < max_retries - 1:
				continue
			else:
				raise

		except Exception as e:
			logger.error(f"❌ Error in find_or_create_lead: {str(e)}")

			# Try one more time to get the lead in case it was created by another process
			existing_lead_name = frappe.db.get_value(doctype, {"email": email}, "name")
			if existing_lead_name:
				logger.info(f"🔄 Retrieved lead after error: {existing_lead_name}")
				lead = frappe.get_doc(doctype, existing_lead_name)

				# Upsert operation - update fields with new values if provided
				updated = False

				# Update tags
				if tags:
					existing_tags = lead.get("custom_tags") or ""
					existing_tags_list = [t.strip() for t in existing_tags.split(",") if t.strip()] if existing_tags else []
					merged_tags = list(set(existing_tags_list + tags))
					new_tags_value = ", ".join(merged_tags)
					if lead.custom_tags != new_tags_value:
						lead.custom_tags = new_tags_value
						updated = True

				# Update organization if provided
				if organization:
					if lead.get("organization") != organization:
						lead.organization = organization
						updated = True
				
				if mobile_no:
					if lead.get("mobile_no") != mobile_no:
						lead.mobile_no = mobile_no
						updated = True
				if job_title:
					if lead.get("job_title") != job_title:
						lead.job_title = job_title
						updated = True
				if lead_role:
					if lead.get("lead_role") != lead_role:
						lead.lead_role = lead_role
						updated = True

				# Update full_name if provided and different
				if full_name and lead.get("lead_name") != full_name:
					lead.lead_name = full_name
					if hasattr(lead, "first_name"):
						lead.first_name = full_name
					updated = True

				# Save only if something was updated
				if updated:
					lead.flags.ignore_mandatory = True
					lead.save(ignore_permissions=True)
					logger.info(f"📝 Updated {doctype}: {lead.name} - Tags: {lead.custom_tags}, Organization: {lead.get('organization', 'N/A')}")

				return lead

			if attempt < max_retries - 1:
				continue
			else:
				raise


def validate_before_linking_to_lead(doc, method=None):
	"""
	Hook: Communication.before_insert

	New Flow (immediate validation with smart pre-linking):
	1. Check if skip_validation flag is set (existing lead found)
	   → Link to existing lead, set validation_status="Linked", exit
	2. Check if validate_immediately flag is set (new sender)
	   → Run AI validation immediately, create audit record
	3. Legacy fallback: Old behavior for manually created Communications
	   → Validates incoming emails with Gemini
	   → Creates appropriate lead (CRM Lead/Non Lead/Query)

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

	# ==================== NEW LOGIC: Check flags from InboundMail ====================

	# Case 1: Existing lead found during ingestion (skip validation)
	if hasattr(doc, 'flags') and getattr(doc.flags, 'skip_validation', False):
		link_doctype = getattr(doc.flags, 'link_to_doctype', None)
		link_name = getattr(doc.flags, 'link_to_name', None)

		if link_doctype and link_name:
			doc.reference_doctype = link_doctype
			doc.reference_name = link_name
			doc.validation_status = "Linked"

			logger.info(f"✅ EXISTING CONTACT: Linked to {link_doctype}: {link_name}")
			logger.info(f"   Validation skipped - known sender")
			return  # Exit early, no validation needed

	# Case 2: New sender, validate immediately
	validate_immediately = hasattr(doc, 'flags') and getattr(doc.flags, 'validate_immediately', False)

	if validate_immediately:
		logger.info(f"⚡ NEW CONTACT: Validating immediately - {doc.sender}")
		# Fall through to validation logic below

	# ==================== VALIDATION LOGIC (for both immediate and manual Communications) ====================

	# If not validate_immediately, check if this is a manually created Communication
	if not validate_immediately:
		# Skip if validation is disabled for manual Communications
		if not is_validation_enabled():
			logger.warning("  ❌ Validation disabled in settings, skipping")
			return

	# CRITICAL FIX: Check if reference was already set by Email Account's append_to setting
	# This prevents duplicate lead creation
	if doc.reference_doctype and doc.reference_name:
		logger.warning(f"  ⚠️ Reference already set: {doc.reference_doctype} - {doc.reference_name}")
		logger.warning("  ⚠️ This indicates Email Account 'append_to' is set incorrectly!")
		logger.warning("  ⚠️ Clearing reference to proceed with validation-based lead creation")
		# Clear the pre-set reference so we can set it based on validation
		doc.reference_doctype = None
		doc.reference_name = None

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

	# Validate with Gemini (returns structured output)
	validation_result = validate_email_with_gemini(raw_email, sender, subject)

	# Handle case where validation returns None (error in validation function)
	if not validation_result or not isinstance(validation_result, dict):
		logger.error(f"❌ Validation failed or returned invalid result: {validation_result}")
		# Default to creating a Lead without validation
		validation_result = {
			"category": "Lead",
			"tags": [],
			"organization": None,
			"reason": "Validation failed - defaulted to Lead",
			"product": None,
			"market": None,
			"lead_type": "Unclassified",
			"lead_role": None,
			"mobile_no": None
		}

	doc.flags.ai_validation_result = validation_result

	# Extract category from structured response (Lead/Non Lead/Query)
	category = validation_result.get("category", "Lead")  # Default to Lead for backward compatibility
	tags = validation_result.get("tags", [])
	organization = validation_result.get("organization", None)
	reason = validation_result.get("reason", "No reason provided")
	products = validation_result.get("product", None)
	market = validation_result.get("market", None)
	lead_type = validation_result.get("lead_type", "Unclassified")
	mobile_no = validation_result.get("mobile_no", None)
	lead_role = validation_result.get("lead_role", None)
	logger.info(f"📋 Validation Result:")
	logger.info(f"   Category: {category}")
	logger.info(f"   Tags: {tags}")
	logger.info(f"   Reason: {reason}")

	# Set validation fields
	doc.validation_status = "Validated"
	doc.validation_category = category
	doc.validation_reason = reason
	doc.validated_on = frappe.utils.now()

	# Find or create Lead based on category
	if category == "Lead":
		# Lead email - create/find CRM Lead
		lead = find_or_create_lead(sender, sender_full_name, subject, "CRM Lead", tags=tags, organization=organization, products=products, market=market, lead_type=lead_type, mobile_no=mobile_no, lead_role=lead_role)

		# Set reference fields
		doc.reference_doctype = "CRM Lead"
		doc.reference_name = lead.name
		segment_ids = validation_result.get("segment_ids", [])
		if isinstance(segment_ids, str):
			segment_ids = [seg.strip() for seg in segment_ids.split(",") if seg.strip()]
		for segment in segment_ids:
			print(segment)
			try:
				if segment != "null":
					add_lead_to_segment(segment, lead.name)
			except Exception as e:
				logger.error(f"❌ Failed to add lead to segment {segment}: {str(e)}")
		logger.info("✅ LEAD EMAIL")
		logger.info(f"✅ Successfully linked Communication to CRM Lead: {lead.name}")
		logger.info(f"   reference_doctype: {doc.reference_doctype}")
		logger.info(f"   reference_name: {doc.reference_name}")

	elif category == "Non Lead":
		# Non Lead email - create/find Non Lead
		lead = find_or_create_lead(sender, sender_full_name, subject, "Non Lead", tags=tags, organization=organization, products=products, market=market, lead_type=lead_type, mobile_no=mobile_no, lead_role=lead_role)

		# Set reference fields
		doc.reference_doctype = "Non Lead"
		doc.reference_name = lead.name
		doc.email_status = "Spam"

		logger.warning("❌ NON LEAD EMAIL (Spam/Promotional)")
		logger.warning(f"❌ Successfully linked Communication to Non Lead: {lead.name}")
		logger.warning(f"   reference_doctype: {doc.reference_doctype}")
		logger.warning(f"   reference_name: {doc.reference_name}")
		logger.warning("="*80)

	elif category == "Query":
		# Query email - create/find Query
		lead = find_or_create_lead(sender, sender_full_name, subject, "Query", tags=tags, organization=organization, products=products, market=market, lead_type=lead_type, mobile_no=mobile_no, lead_role=lead_role)

		# Set reference fields
		doc.reference_doctype = "Query"
		doc.reference_name = lead.name

		logger.info("🔍 QUERY EMAIL (Customer Support)")
		logger.info(f"✅ Successfully linked Communication to Query: {lead.name}")
		logger.info(f"   reference_doctype: {doc.reference_doctype}")
		logger.info(f"   reference_name: {doc.reference_name}")


def add_validation_info_to_communication(doc, method=None):  # noqa: ARG001
	"""
	Hook: Communication.after_insert

	Adds validation metadata comment with tags and reason for emails.
	Also creates audit record after the Communication has been inserted.

	Args:
		doc: Communication document
		method: Hook method name (unused but required by Frappe hook signature)
	"""
	if hasattr(doc.flags, 'ai_validation_result') and doc.flags.ai_validation_result:
		validation_result = doc.flags.ai_validation_result

		# Validate that validation_result is a dict
		if not isinstance(validation_result, dict):
			logger = frappe.logger("email_validation", allow_site=True, file_count=5)
			logger.error(f"❌ Invalid validation_result type: {type(validation_result)}")
			return

		# Extract structured data
		category = validation_result.get("category", "Lead")
		tags = validation_result.get("tags", [])
		reason = validation_result.get("reason", "No reason provided")
		validity = validation_result.get("validity", "Unknown")

		# Create audit record now that Communication has been inserted and has a name
		if doc.reference_doctype and doc.reference_name:
			try:
				create_audit_record(doc, doc.reference_doctype, doc.reference_name, category, reason)
			except Exception as e:
				logger = frappe.logger("email_validation", allow_site=True, file_count=5)
				logger.error(f"Failed to create audit record in after_insert: {str(e)}")

		if validity == "Invalid":
			# Format tags for display
			tags_html = ", ".join([f"<span style='background: #e3f2fd; padding: 2px 8px; border-radius: 3px; margin: 0 2px;'>{tag}</span>" for tag in tags]) if tags else "None"

			# Add a comment to the Communication
			comment = f"""
<div style="background: #fff3cd; border-left: 4px solid #ffc107; padding: 10px; margin: 10px 0;">
	<strong>🤖 AI Validation:</strong> This email was identified as <strong>spam/promotional</strong>
	<br><br>
	<strong>Tags:</strong> {tags_html}
	<br>
	<strong>Reason:</strong> {reason}
	<br><br>
	<em>Email status marked as Spam.</em>
</div>
			"""

			doc.add_comment("Info", comment)

		elif validity == "Valid":
			# Optionally add a comment for valid emails too (with success styling)
			tags_html = ", ".join([f"<span style='background: #e8f5e9; padding: 2px 8px; border-radius: 3px; margin: 0 2px;'>{tag}</span>" for tag in tags]) if tags else "None"

			comment = f"""
<div style="background: #e8f5e9; border-left: 4px solid #4caf50; padding: 10px; margin: 10px 0;">
	<strong>🤖 AI Validation:</strong> This email was identified as <strong>valid lead</strong>
	<br><br>
	<strong>Tags:</strong> {tags_html}
	<br>
	<strong>Reason:</strong> {reason}
</div>
			"""

			doc.add_comment("Info", comment)
