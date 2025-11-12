# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class EmailValidationAudit(Document):
	@staticmethod
	def default_list_data():
		return {
			"fields": [
				"`tabEmail Validation Audit`.name",
				"`tabEmail Validation Audit`.sender_email",
				"`tabEmail Validation Audit`.category",
				"`tabEmail Validation Audit`.validation_method",
				"`tabEmail Validation Audit`.validated_on",
				"`tabEmail Validation Audit`.lead_doctype",
				"`tabEmail Validation Audit`.lead_name",
			],
			"order_by": "`tabEmail Validation Audit`.validated_on desc",
			"page_length": 20,
		}


@frappe.whitelist()
def recategorize_email(audit_id, new_category, reason):
	"""
	Recategorize an email validation by changing its category.

	This will:
	1. Get the audit record and linked Communication
	2. Delete the old lead (if it's the only Communication linked to it)
	3. Create a new lead in the new category
	4. Update the Communication reference
	5. Create a new audit record with validation_method="Manual"

	Args:
		audit_id: Name of the Email Validation Audit record
		new_category: New category (Lead/Non Lead/Query)
		reason: Reason for recategorization

	Returns:
		dict: Success message with new lead details
	"""
	# Validate inputs
	if new_category not in ["Lead", "Non Lead", "Query"]:
		frappe.throw("Invalid category. Must be Lead, Non Lead, or Query")

	# Get audit record
	audit = frappe.get_doc("Email Validation Audit", audit_id)

	# Get linked Communication
	communication = frappe.get_doc("Communication", audit.communication)

	# Store old category
	old_category = audit.category
	old_lead_doctype = audit.lead_doctype
	old_lead_name = audit.lead_name

	# Check if it's the same category
	if old_category == new_category:
		frappe.throw(f"Email is already categorized as {new_category}")

	# Map category to doctype
	doctype_map = {
		"Lead": "CRM Lead",
		"Non Lead": "Non Lead",
		"Query": "Query"
	}
	new_lead_doctype = doctype_map[new_category]

	# Get old lead
	old_lead = frappe.get_doc(old_lead_doctype, old_lead_name)

	# Create new lead in the new category
	from crm_override.crm_override.communication_hooks import find_or_create_lead

	new_lead = find_or_create_lead(
		email=communication.sender,
		full_name=communication.sender_full_name or communication.sender,
		subject=communication.subject,
		doctype=new_lead_doctype,
		tags=[]
	)

	# Update Communication reference
	communication.reference_doctype = new_lead_doctype
	communication.reference_name = new_lead.name
	communication.validation_category = new_category
	communication.validation_reason = f"Manually recategorized from {old_category} to {new_category}: {reason}"
	communication.validated_on = frappe.utils.now()

	# Update email_status for Non Lead
	if new_category == "Non Lead":
		communication.email_status = "Spam"
	elif old_category == "Non Lead":
		# Clear spam status if moving away from Non Lead
		communication.email_status = "Open"

	communication.flags.ignore_mandatory = True
	communication.save(ignore_permissions=True)

	# Check if old lead has any other Communications
	other_communications = frappe.get_all(
		"Communication",
		filters={
			"reference_doctype": old_lead_doctype,
			"reference_name": old_lead_name,
			"name": ["!=", communication.name]
		},
		limit=1
	)

	# If no other Communications, delete the old lead
	if not other_communications:
		try:
			frappe.delete_doc(old_lead_doctype, old_lead_name, ignore_permissions=True)
			frappe.logger().info(f"Deleted old lead {old_lead_doctype}: {old_lead_name} (no other communications)")
		except Exception as e:
			frappe.logger().error(f"Failed to delete old lead: {str(e)}")
			# Don't fail the recategorization if deletion fails

	# Create new audit record for manual recategorization
	from crm_override.crm_override.communication_hooks import create_audit_record

	# Set previous category in the old audit record
	audit.previous_category = old_category
	audit.save(ignore_permissions=True)

	# Create new audit record
	create_audit_record(
		communication,
		new_lead_doctype,
		new_lead.name,
		new_category,
		f"Manually recategorized from {old_category}: {reason}",
		validation_method="Manual"
	)

	frappe.db.commit()

	return {
		"success": True,
		"message": f"Successfully recategorized from {old_category} to {new_category}",
		"old_lead": f"{old_lead_doctype}: {old_lead_name}",
		"new_lead": f"{new_lead_doctype}: {new_lead.name}",
		"communication": communication.name
	}


@frappe.whitelist()
def get_recategorization_stats(date_range=30):
	"""
	Get statistics on manual recategorizations.

	Args:
		date_range: Number of days to look back (default: 30)

	Returns:
		dict: Statistics on recategorizations
	"""
	from frappe.utils import add_days, get_datetime

	start_date = add_days(get_datetime(), -date_range)

	# Get all manual validations
	manual_validations = frappe.get_all(
		"Email Validation Audit",
		filters={
			"validation_method": "Manual",
			"validated_on": [">=", start_date]
		},
		fields=["category", "previous_category", "validated_on"]
	)

	# Calculate stats
	total_manual = len(manual_validations)

	# Group by category transitions
	transitions = {}
	for validation in manual_validations:
		if validation.previous_category:
			key = f"{validation.previous_category} → {validation.category}"
			transitions[key] = transitions.get(key, 0) + 1

	return {
		"total_manual_recategorizations": total_manual,
		"date_range_days": date_range,
		"transitions": transitions,
		"manual_validations": manual_validations
	}
