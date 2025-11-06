# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from crm.integrations.api import get_contact_by_phone_number
from crm.utils import seconds_to_duration


class CRMCallLog(Document):
	@staticmethod
	def default_list_data():
		columns = [
			{
				"label": "Type",
				"type": "Select",
				"key": "type",
				"width": "7rem",
			},
			{
				"label": "Status",
				"type": "Select",
				"key": "status",
				"width": "8rem",
			},
			{
				"label": "From",
				"type": "Data",
				"key": "from_number",
				"width": "10rem",
			},
			{
				"label": "To",
				"type": "Data",
				"key": "to",
				"width": "10rem",
			},
			{
				"label": "Duration",
				"type": "Duration",
				"key": "duration",
				"width": "7rem",
			},
			{
				"label": "Agent ID",
				"type": "Data",
				"key": "agent_id",
				"width": "9rem",
			},
			{
				"label": "Medium",
				"type": "Data",
				"key": "telephony_medium",
				"width": "7rem",
			},
			{
				"label": "Sentiment",
				"type": "Data",
				"key": "sentiment",
				"width": "8rem",
			},
			{
				"label": "Classification",
				"type": "Data",
				"key": "classification",
				"width": "10rem",
			},
			{
				"label": "Start Time",
				"type": "Datetime",
				"key": "start_time",
				"width": "9rem",
			},
			{
				"label": "Created On",
				"type": "Datetime",
				"key": "creation",
				"width": "9rem",
			},
		]
		rows = [
			"name",
			"id",
			"caller",
			"receiver",
			"type",
			"status",
			"from_number",
			"to",
			"duration",
			"agent_id",
			"telephony_medium",
			"medium",
			"sentiment",
			"classification",
			"start_time",
			"end_time",
			"note",
			"recording_url",
			"recording_duration",
			"transcript",
			"summary",
			"key_points",
			"action_items",
			"call_disconnect_reason",
			"reference_doctype",
			"reference_docname",
			"creation",
			"modified",
		]
		return {"columns": columns, "rows": rows}

	def parse_list_data(calls):
		return [parse_call_log(call) for call in calls] if calls else []

	def has_link(self, doctype, name):
		for link in self.links:
			if link.link_doctype == doctype and link.link_name == name:
				return True

	def link_with_reference_doc(self, reference_doctype, reference_name):
		if self.has_link(reference_doctype, reference_name):
			return

		self.append("links", {"link_doctype": reference_doctype, "link_name": reference_name})


def parse_call_log(call):
	call["show_recording"] = False
	call["_duration"] = seconds_to_duration(call.get("duration"))
	if call.get("type") == "Incoming":
		call["activity_type"] = "incoming_call"
		contact = get_contact_by_phone_number(call.get("from_number"))
		receiver = (
			frappe.db.get_values("User", call.get("receiver"), ["full_name", "user_image"])[0]
			if call.get("receiver")
			else [None, None]
		)
		call["_caller"] = {
			"label": contact.get("full_name", "Unknown") if contact else "Unknown",
			"image": contact.get("image") if contact else None,
		}
		call["_receiver"] = {
			"label": receiver[0],
			"image": receiver[1],
		}
	elif call.get("type") == "Outgoing":
		call["activity_type"] = "outgoing_call"
		contact = get_contact_by_phone_number(call.get("to"))
		caller = (
			frappe.db.get_values("User", call.get("caller"), ["full_name", "user_image"])[0]
			if call.get("caller")
			else [None, None]
		)
		call["_caller"] = {
			"label": caller[0],
			"image": caller[1],
		}
		call["_receiver"] = {
			"label": contact.get("full_name", "Unknown") if contact else "Unknown",
			"image": contact.get("image") if contact else None,
		}

	return call

@frappe.whitelist()
def get_call_log(name=None):
	"""Get complete call log with all fields"""
	# Validate name parameter
	# if not name:
	# 	frappe.throw("Call Log name is required")
	
	# Get the call log document
	if name:
		try:
			call = frappe.get_cached_doc("CRM Call Log", name).as_dict()
		except frappe.DoesNotExistError:
			frappe.throw(f"Call Log {name} does not exist")
	else:
		try:
			call = frappe.get_cached_doc("CRM Call Log").as_dict()
		except frappe.DoesNotExistError:
			frappe.throw(f"Call Logs does not exist")
	
	# Parse call log for UI display
	call = parse_call_log(call)

	# Get notes
	notes = []
	if call.get("note"):
		try:
			note = frappe.get_cached_doc("FCRM Note", call.get("note")).as_dict()
			notes.append(note)
		except:
			pass

	# Get tasks
	tasks = []

	# Handle reference documents
	if call.get("reference_doctype") and call.get("reference_docname"):
		if call.get("reference_doctype") == "CRM Lead":
			call["_lead"] = call.get("reference_docname")
		elif call.get("reference_doctype") == "CRM Deal":
			call["_deal"] = call.get("reference_docname")

	# Handle links
	if call.get("links"):
		for link in call.get("links"):
			try:
				if link.get("link_doctype") == "CRM Task":
					task = frappe.get_cached_doc("CRM Task", link.get("link_name")).as_dict()
					tasks.append(task)
				elif link.get("link_doctype") == "FCRM Note":
					note = frappe.get_cached_doc("FCRM Note", link.get("link_name")).as_dict()
					notes.append(note)
				elif link.get("link_doctype") == "CRM Lead":
					call["_lead"] = link.get("link_name")
				elif link.get("link_doctype") == "CRM Deal":
					call["_deal"] = link.get("link_name")
			except:
				continue

	# Add client analysis data if available
	if call.get("client_analysis"):
		call["_client_analysis"] = call.get("client_analysis")

	call["_tasks"] = tasks
	call["_notes"] = notes
	print(f"Call Log fetched: {call}")
	return call


@frappe.whitelist()
def create_lead_from_call_log(call_log, lead_details=None):
	lead = frappe.new_doc("CRM Lead")
	lead_details = frappe.parse_json(lead_details or "{}")

	if not lead_details.get("lead_owner"):
		lead_details["lead_owner"] = frappe.session.user
	if not lead_details.get("mobile_no"):
		lead_details["mobile_no"] = call_log.get("from_number") or call_log.get("from") or ""
	if not lead_details.get("first_name"):
		lead_details["first_name"] = "Lead from call " + (
			lead_details.get("mobile_no") or call_log.get("name")
		)

	lead.update(lead_details)
	lead.save(ignore_permissions=True)

	# link call log with lead
	call_log = frappe.get_doc("CRM Call Log", call_log.get("name"))
	call_log.link_with_reference_doc("CRM Lead", lead.name)
	call_log.save(ignore_permissions=True)

	return lead.name

