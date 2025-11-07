# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class NonLead(Document):
	"""
	Non Lead - for spam/promotional emails.
	Simplified version without CRM workflows.
	"""
	@staticmethod
	def default_list_data():
		columns = [
			{
				"label": "Name",
				"type": "Data",
				"key": "lead_name",
				"width": "12rem",
			},
			{
				"label": "Organization",
				"type": "Link",
				"key": "organization",
				"options": "CRM Organization",
				"width": "10rem",
			},
			{
				"label": "Status",
				"type": "Select",
				"key": "status",
				"width": "8rem",
			},
			{
				"label": "Email",
				"type": "Data",
				"key": "email",
				"width": "12rem",
			},
			{
				"label": "Mobile No",
				"type": "Data",
				"key": "mobile_no",
				"width": "11rem",
			},
			{
				"label": "Tags",
				"type": "Data",
				"key": "custom_tags",
				"width": "12rem",
			},
			{
				"label": "Assigned To",
				"type": "Text",
				"key": "_assign",
				"width": "10rem",
			},
			{
				"label": "Last Modified",
				"type": "Datetime",
				"key": "modified",
				"width": "8rem",
			},
		]
		rows = [
			"name",
			"lead_name",
			"organization",
			"status",
			"email",
			"mobile_no",
			"custom_tags",
			"territory",
			"_assign",
			"modified",
		]
		return {"columns": columns, "rows": rows}

	@staticmethod
	def default_kanban_settings():
		return {
			"column_field": "status",
			"title_field": "lead_name",
			"kanban_fields": '["organization", "email", "mobile_no", "custom_tags", "_assign", "modified"]',
		}


@frappe.whitelist()
def convert_to_lead(rejected_lead_id):
	"""Convert a Non Lead to CRM Lead"""
	from frappe import _

	# Check permissions
	if not frappe.has_permission("Non Lead", "write", rejected_lead_id):
		frappe.throw(_("Not allowed to convert Rejected Lead to Lead"), frappe.PermissionError)

	# Get the rejected lead
	rejected_lead = frappe.get_doc("Non Lead", rejected_lead_id)

	# Create new CRM Lead
	lead = frappe.new_doc("CRM Lead")

	# Map fields from Non Lead to CRM Lead
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
		if hasattr(rejected_lead, field) and rejected_lead.get(field):
			lead.set(field, rejected_lead.get(field))

	# Set converted flag
	lead.flags.ignore_mandatory = False
	lead.insert(ignore_permissions=True)

	# Copy communications (emails) from rejected lead to new lead
	communications = frappe.get_all(
		"Communication",
		filters={
			"reference_doctype": "Non Lead",
			"reference_name": rejected_lead_id
		},
		fields=["name"]
	)

	for comm in communications:
		frappe.db.set_value(
			"Communication",
			comm.name,
			{
				"reference_doctype": "CRM Lead",
				"reference_name": lead.name
			}
		)

	# Copy comments
	comments = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": "Non Lead",
			"reference_name": rejected_lead_id
		},
		fields=["name"]
	)

	for comment in comments:
		frappe.db.set_value(
			"Comment",
			comment.name,
			{
				"reference_doctype": "CRM Lead",
				"reference_name": lead.name
			}
		)

	# Copy attachments
	attachments = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": "Non Lead",
			"attached_to_name": rejected_lead_id
		},
		fields=["name"]
	)

	for attachment in attachments:
		frappe.db.set_value(
			"File",
			attachment.name,
			{
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": lead.name
			}
		)

	# Delete the rejected lead
	frappe.delete_doc("Non Lead", rejected_lead_id, ignore_permissions=True)

	frappe.db.commit()

	return lead.name
