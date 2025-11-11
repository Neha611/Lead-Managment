"""
Dashboard API for Email Validation Analytics
"""
import frappe
from frappe import _
from frappe.utils import now_datetime, add_to_date


@frappe.whitelist()
def get_recent_audits(hours=168, limit=50):
	"""
	Get recent audit records for dashboard table with conversion actions.

	Args:
		hours: Number of hours to look back (default: 168 = 7 days)
		limit: Maximum number of records to return (default: 50)

	Returns:
		list: Recent audit records
	"""
	try:
		hours = int(hours)
		limit = int(limit)
	except (ValueError, TypeError):
		hours = 168
		limit = 50

	# Calculate cutoff time
	cutoff_time = add_to_date(now_datetime(), hours=-hours)

	# Get recent audits
	audits = frappe.get_all(
		"Email Validation Audit",
		filters={
			"validated_on": [">=", cutoff_time]
		},
		fields=[
			"name",
			"communication",
			"sender_email",
			"subject",
			"category",
			"lead_doctype",
			"lead_name",
			"validated_on",
			"validation_method"
		],
		order_by="validated_on desc",
		limit=limit
	)

	return audits


@frappe.whitelist()
def convert_lead_category(lead_doctype, lead_name, target_category, audit_id=None):
	"""
	Convert a lead from one category to another.

	Args:
		lead_doctype: Current doctype (CRM Lead, Non Lead, Query)
		lead_name: Name of the lead document
		target_category: Target category (Lead, Non Lead, Query)
		audit_id: Optional audit record ID to update

	Returns:
		dict: Conversion result with new lead details
	"""
	# Validate inputs
	valid_doctypes = ["CRM Lead", "Non Lead", "Query"]
	valid_categories = ["Lead", "Non Lead", "Query"]

	if lead_doctype not in valid_doctypes:
		frappe.throw(_("Invalid lead doctype"))

	if target_category not in valid_categories:
		frappe.throw(_("Invalid target category"))

	# Map category to doctype
	category_to_doctype = {
		"Lead": "CRM Lead",
		"Non Lead": "Non Lead",
		"Query": "Query"
	}

	target_doctype = category_to_doctype[target_category]

	# Check if same category
	if lead_doctype == target_doctype:
		frappe.throw(_("Lead is already in the target category"))

	# Check permissions
	if not frappe.has_permission(lead_doctype, "write", lead_name):
		frappe.throw(_("Not allowed to convert this lead"), frappe.PermissionError)

	# Get source lead
	source_lead = frappe.get_doc(lead_doctype, lead_name)

	# Create target lead
	target_lead = frappe.new_doc(target_doctype)

	# Map common fields
	fields_to_copy = [
		"salutation",
		"first_name",
		"middle_name",
		"last_name",
		"lead_name",
		"organization",
		"website",
		"territory",
		"industry",
		"annual_revenue",
		"no_of_employees",
		"email",
		"mobile_no",
		"phone",
		"status",
		"lead_owner",
		"image",
		"job_title",
		"gender",
		"whatsapp_no",
		"custom_tags",
	]

	for field in fields_to_copy:
		if hasattr(source_lead, field) and source_lead.get(field):
			target_lead.set(field, source_lead.get(field))

	# Insert target lead
	target_lead.flags.ignore_mandatory = False
	target_lead.insert(ignore_permissions=True)

	# Copy communications
	communications = frappe.get_all(
		"Communication",
		filters={
			"reference_doctype": lead_doctype,
			"reference_name": lead_name
		},
		fields=["name"]
	)

	for comm in communications:
		frappe.db.set_value(
			"Communication",
			comm.name,
			{
				"reference_doctype": target_doctype,
				"reference_name": target_lead.name
			}
		)

	# Copy comments
	comments = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": lead_doctype,
			"reference_name": lead_name
		},
		fields=["name"]
	)

	for comment in comments:
		frappe.db.set_value(
			"Comment",
			comment.name,
			{
				"reference_doctype": target_doctype,
				"reference_name": target_lead.name
			}
		)

	# Copy attachments
	attachments = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": lead_doctype,
			"attached_to_name": lead_name
		},
		fields=["name"]
	)

	for attachment in attachments:
		frappe.db.set_value(
			"File",
			attachment.name,
			{
				"attached_to_doctype": target_doctype,
				"attached_to_name": target_lead.name
			}
		)

	# Update audit records to track conversion
	audit_records = frappe.get_all(
		"Email Validation Audit",
		filters={
			"lead_doctype": lead_doctype,
			"lead_name": lead_name
		},
		fields=["name", "category"]
	)

	for audit in audit_records:
		frappe.db.set_value(
			"Email Validation Audit",
			audit.name,
			{
				"previous_category": audit.category,
				"category": target_category,
				"lead_doctype": target_doctype,
				"lead_name": target_lead.name,
				"validation_method": "Manual"
			}
		)

	# Delete source lead
	frappe.delete_doc(lead_doctype, lead_name, ignore_permissions=True)

	frappe.db.commit()

	return {
		"success": True,
		"message": f"Successfully converted to {target_category}",
		"new_lead_doctype": target_doctype,
		"new_lead_name": target_lead.name
	}


@frappe.whitelist()
def get_validation_stats(hours=168):
	"""
	Get validation statistics for dashboard.

	Args:
		hours: Number of hours to look back (default: 168 = 7 days)

	Returns:
		dict: Dashboard statistics
	"""
	try:
		hours = int(hours)
	except (ValueError, TypeError):
		hours = 168

	# Calculate cutoff time
	cutoff_time = add_to_date(now_datetime(), hours=-hours)

	# Get all validations in time range
	audits = frappe.get_all(
		"Email Validation Audit",
		filters={
			"validated_on": [">=", cutoff_time]
		},
		fields=["name", "category", "sender_email", "validated_on", "lead_doctype", "lead_name"]
	)

	# Calculate category counts
	total = len(audits)
	lead_count = sum(1 for a in audits if a.category == "Lead")
	non_lead_count = sum(1 for a in audits if a.category == "Non Lead")
	query_count = sum(1 for a in audits if a.category == "Query")

	# Calculate percentages
	lead_pct = (lead_count / total * 100) if total > 0 else 0
	non_lead_pct = (non_lead_count / total * 100) if total > 0 else 0
	query_pct = (query_count / total * 100) if total > 0 else 0

	# Get category distribution for chart
	category_distribution = [
		{"category": "Lead", "count": lead_count},
		{"category": "Non Lead", "count": non_lead_count},
		{"category": "Query", "count": query_count}
	]

	# Get timeline data (grouped by day)
	timeline_data = frappe.db.sql("""
		SELECT
			DATE(validated_on) as date,
			category,
			COUNT(*) as count
		FROM `tabEmail Validation Audit`
		WHERE validated_on >= %s
		GROUP BY DATE(validated_on), category
		ORDER BY date ASC
	""", (cutoff_time,), as_dict=True)

	# Get top senders
	top_senders = frappe.db.sql("""
		SELECT
			sender_email,
			category,
			COUNT(*) as count
		FROM `tabEmail Validation Audit`
		WHERE validated_on >= %s
		GROUP BY sender_email, category
		ORDER BY count DESC
		LIMIT 10
	""", (cutoff_time,), as_dict=True)

	# Calculate conversion rates (placeholder logic)
	# You'll need to implement this based on your Lead/Opportunity tracking
	lead_to_opportunity = calculate_conversion_rate("Lead", "Opportunity", cutoff_time)
	query_to_lead = calculate_reclassification_rate("Query", "Lead", cutoff_time)
	non_lead_to_lead = calculate_reclassification_rate("Non Lead", "Lead", cutoff_time)

	return {
		"total": total,
		"lead_count": lead_count,
		"non_lead_count": non_lead_count,
		"query_count": query_count,
		"lead_percentage": round(lead_pct, 1),
		"non_lead_percentage": round(non_lead_pct, 1),
		"query_percentage": round(query_pct, 1),
		"category_distribution": category_distribution,
		"timeline_data": timeline_data,
		"top_senders": top_senders,
		"conversions": {
			"lead_to_opportunity": lead_to_opportunity,
			"query_to_lead": query_to_lead,
			"non_lead_to_lead": non_lead_to_lead
		}
	}


def calculate_conversion_rate(from_category, to_doctype, cutoff_time):
	"""
	Calculate conversion rate from audit category to another doctype (e.g., Lead → Opportunity).

	This is a placeholder - implement based on your Lead/Opportunity tracking logic.
	"""
	# Example: Get leads created from emails, then check how many became opportunities
	try:
		# Get leads from email validation
		leads_from_email = frappe.db.sql("""
			SELECT COUNT(DISTINCT lead_name) as total
			FROM `tabEmail Validation Audit`
			WHERE category = %s
			AND validated_on >= %s
		""", (from_category, cutoff_time), as_dict=True)

		total_leads = leads_from_email[0].total if leads_from_email else 0

		if total_leads == 0:
			return "N/A"

		# This is placeholder logic - adjust based on your CRM structure
		# For now, return N/A
		return "N/A"

	except Exception as e:
		frappe.log_error(f"Conversion rate calculation error: {str(e)}")
		return "N/A"


def calculate_reclassification_rate(from_category, to_category, cutoff_time):
	"""
	Calculate how often emails are reclassified from one category to another.

	Uses the previous_category field in Email Validation Audit.
	"""
	try:
		# Get total count of from_category
		total_count = frappe.db.count(
			"Email Validation Audit",
			filters={
				"previous_category": from_category,
				"validated_on": [">=", cutoff_time]
			}
		)

		if total_count == 0:
			return "0%"

		# Get count of reclassified to to_category
		reclassified_count = frappe.db.count(
			"Email Validation Audit",
			filters={
				"previous_category": from_category,
				"category": to_category,
				"validated_on": [">=", cutoff_time]
			}
		)

		rate = (reclassified_count / total_count * 100) if total_count > 0 else 0
		return f"{round(rate, 1)}%"

	except Exception as e:
		frappe.log_error(f"Reclassification rate calculation error: {str(e)}")
		return "N/A"
