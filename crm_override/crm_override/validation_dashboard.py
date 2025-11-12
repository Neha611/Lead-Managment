# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_days, get_datetime


@frappe.whitelist()
def get_validation_metrics(date_range=24):
	"""
	Get email validation metrics for the dashboard.

	Args:
		date_range: Number of hours to look back (default: 24)

	Returns:
		dict: Validation metrics including:
			- total_validations: Total number of validations
			- categories: Breakdown by category (Lead/Non Lead/Query)
			- conversion_rates: Conversion metrics
			- validation_timeline: Hourly validation counts
	"""
	# Convert hours to datetime
	hours = int(date_range)
	start_date = add_days(get_datetime(), -hours/24)

	# Get all validations in the date range
	validations = frappe.get_all(
		"Email Validation Audit",
		filters={
			"validated_on": [">=", start_date]
		},
		fields=["category", "validation_method", "validated_on", "lead_doctype", "lead_name"]
	)

	total_validations = len(validations)

	# Calculate category breakdown
	categories = {
		"Lead": {"count": 0, "percentage": 0},
		"Non Lead": {"count": 0, "percentage": 0},
		"Query": {"count": 0, "percentage": 0}
	}

	for validation in validations:
		if validation.category in categories:
			categories[validation.category]["count"] += 1

	# Calculate percentages
	if total_validations > 0:
		for category in categories:
			categories[category]["percentage"] = round(
				(categories[category]["count"] / total_validations) * 100, 1
			)

	# Calculate conversion rates
	conversion_rates = calculate_conversion_rates(start_date)

	# Get validation timeline (hourly breakdown)
	timeline = get_validation_timeline(start_date)

	# Get validation method breakdown
	method_breakdown = {
		"Auto": sum(1 for v in validations if v.validation_method == "Auto"),
		"Manual": sum(1 for v in validations if v.validation_method == "Manual")
	}

	return {
		"total_validations": total_validations,
		"date_range_hours": hours,
		"categories": categories,
		"conversion_rates": conversion_rates,
		"validation_timeline": timeline,
		"method_breakdown": method_breakdown,
		"period_start": start_date.strftime("%Y-%m-%d %H:%M:%S"),
		"period_end": get_datetime().strftime("%Y-%m-%d %H:%M:%S")
	}


def calculate_conversion_rates(start_date):
	"""
	Calculate conversion rates for different categories.

	Args:
		start_date: Start date for the calculation period

	Returns:
		dict: Conversion rate metrics
	"""
	# Lead to Opportunity conversion (if using Frappe CRM)
	lead_to_opportunity = 0
	try:
		# Get leads created in date range
		leads = frappe.get_all(
			"CRM Lead",
			filters={
				"creation": [">=", start_date]
			},
			fields=["name"]
		)

		if leads:
			# Check how many were converted to opportunities/deals
			converted = frappe.db.count(
				"CRM Deal",
				filters={
					"lead": ["in", [l.name for l in leads]],
					"creation": [">=", start_date]
				}
			)

			lead_to_opportunity = round((converted / len(leads)) * 100, 1) if len(leads) > 0 else 0
	except Exception as e:
		frappe.logger().error(f"Error calculating lead to opportunity conversion: {str(e)}")

	# Query to Lead conversion (manual recategorization)
	query_to_lead = 0
	try:
		# Get queries that were recategorized to leads
		recategorizations = frappe.get_all(
			"Email Validation Audit",
			filters={
				"validation_method": "Manual",
				"category": "Lead",
				"previous_category": "Query",
				"validated_on": [">=", start_date]
			}
		)

		# Get total queries in period
		total_queries = frappe.db.count(
			"Email Validation Audit",
			filters={
				"category": "Query",
				"validated_on": [">=", start_date]
			}
		)

		query_to_lead = round((len(recategorizations) / total_queries) * 100, 1) if total_queries > 0 else 0
	except Exception as e:
		frappe.logger().error(f"Error calculating query to lead conversion: {str(e)}")

	# Non Lead to Lead conversion (false positive correction)
	non_lead_to_lead = 0
	try:
		# Get non-leads that were recategorized to leads
		recategorizations = frappe.get_all(
			"Email Validation Audit",
			filters={
				"validation_method": "Manual",
				"category": "Lead",
				"previous_category": "Non Lead",
				"validated_on": [">=", start_date]
			}
		)

		# Get total non-leads in period
		total_non_leads = frappe.db.count(
			"Email Validation Audit",
			filters={
				"category": "Non Lead",
				"validated_on": [">=", start_date]
			}
		)

		non_lead_to_lead = round((len(recategorizations) / total_non_leads) * 100, 1) if total_non_leads > 0 else 0
	except Exception as e:
		frappe.logger().error(f"Error calculating non-lead to lead conversion: {str(e)}")

	return {
		"lead_to_opportunity": f"{lead_to_opportunity}%",
		"query_to_lead": f"{query_to_lead}%",
		"non_lead_to_lead": f"{non_lead_to_lead}%"
	}


def get_validation_timeline(start_date):
	"""
	Get hourly validation counts for timeline visualization.

	Args:
		start_date: Start date for the timeline

	Returns:
		list: List of dicts with hour and count
	"""
	# Get all validations grouped by hour
	timeline_data = frappe.db.sql("""
		SELECT
			DATE_FORMAT(validated_on, '%%Y-%%m-%%d %%H:00:00') as hour,
			category,
			COUNT(*) as count
		FROM `tabEmail Validation Audit`
		WHERE validated_on >= %s
		GROUP BY hour, category
		ORDER BY hour ASC
	""", (start_date,), as_dict=True)

	# Group by hour
	timeline = {}
	for row in timeline_data:
		hour = row.hour
		if hour not in timeline:
			timeline[hour] = {
				"hour": hour,
				"Lead": 0,
				"Non Lead": 0,
				"Query": 0,
				"total": 0
			}
		timeline[hour][row.category] = row.count
		timeline[hour]["total"] += row.count

	return list(timeline.values())


@frappe.whitelist()
def get_validation_summary(date_range=24):
	"""
	Get a simplified summary of validation metrics for quick display.

	Args:
		date_range: Number of hours to look back (default: 24)

	Returns:
		dict: Simplified metrics
	"""
	hours = int(date_range)
	start_date = add_days(get_datetime(), -hours/24)

	# Get category counts
	categories = frappe.db.sql("""
		SELECT
			category,
			COUNT(*) as count
		FROM `tabEmail Validation Audit`
		WHERE validated_on >= %s
		GROUP BY category
	""", (start_date,), as_dict=True)

	total = sum(c.count for c in categories)

	summary = {
		"total": total,
		"Lead": 0,
		"Non Lead": 0,
		"Query": 0
	}

	for cat in categories:
		summary[cat.category] = cat.count

	return summary


@frappe.whitelist()
def get_top_senders(date_range=24, limit=10):
	"""
	Get top email senders by validation count.

	Args:
		date_range: Number of hours to look back (default: 24)
		limit: Number of top senders to return (default: 10)

	Returns:
		list: List of top senders with counts
	"""
	hours = int(date_range)
	limit = int(limit)
	start_date = add_days(get_datetime(), -hours/24)

	top_senders = frappe.db.sql("""
		SELECT
			sender_email,
			category,
			COUNT(*) as count
		FROM `tabEmail Validation Audit`
		WHERE validated_on >= %s
		GROUP BY sender_email, category
		ORDER BY count DESC
		LIMIT %s
	""", (start_date, limit), as_dict=True)

	return top_senders


@frappe.whitelist()
def get_audit_list(filters=None, limit=20, offset=0):
	"""
	Get paginated list of Email Validation Audit records with filters.

	Args:
		filters: Dict of filters (category, validation_method, sender_email, date_range)
		limit: Number of records per page (default: 20)
		offset: Pagination offset (default: 0)

	Returns:
		dict: Audit records and pagination info
	"""
	import json

	if isinstance(filters, str):
		filters = json.loads(filters)

	filters = filters or {}
	limit = int(limit)
	offset = int(offset)

	# Build filter conditions
	conditions = {}

	if filters.get("category"):
		conditions["category"] = filters["category"]

	if filters.get("validation_method"):
		conditions["validation_method"] = filters["validation_method"]

	if filters.get("sender_email"):
		conditions["sender_email"] = ["like", f"%{filters['sender_email']}%"]

	if filters.get("date_range"):
		hours = int(filters["date_range"])
		start_date = add_days(get_datetime(), -hours/24)
		conditions["validated_on"] = [">=", start_date]

	# Get total count
	total_count = frappe.db.count("Email Validation Audit", filters=conditions)

	# Get records
	records = frappe.get_all(
		"Email Validation Audit",
		filters=conditions,
		fields=[
			"name",
			"sender_email",
			"subject",
			"category",
			"validation_method",
			"validated_on",
			"lead_doctype",
			"lead_name",
			"communication"
		],
		order_by="validated_on desc",
		limit=limit,
		start=offset
	)

	return {
		"records": records,
		"total_count": total_count,
		"limit": limit,
		"offset": offset,
		"has_more": (offset + limit) < total_count
	}
