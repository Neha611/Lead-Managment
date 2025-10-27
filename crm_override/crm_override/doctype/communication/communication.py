# Copyright (c) 2025, Neha and contributors
# For license information, please see license.txt
import frappe
from frappe.utils import time_diff_in_seconds, parse_addr
from frappe.utils.user import is_system_user
from frappe.model.document import Document
from frappe.core.utils import get_parent_doc
from frappe.automation.doctype.assignment_rule.assignment_rule import apply as apply_assignment_rule
from frappe.core.doctype.communication.communication import Communication as BaseCommunication
from frappe.core.doctype.communication.communication import update_comment_in_doc


class Communication(BaseCommunication):
	"""
	Custom Communication class for CRM Override.
	Frappe already handles email threading and reply detection.
	We just need to update our Lead Email Tracker when replies come in.
	"""
	
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		if not hasattr(self, 'comment_type'):
			self.comment_type = None

	def after_insert(self):
		"""Called after Communication is created by Frappe"""
		super().after_insert()
		
		# Update Lead Email Tracker if this is a reply from a lead
		if (
			self.communication_medium == "Email" 
			and self.sent_or_received == "Received"
			and self.reference_doctype == "CRM Lead"
			and self.reference_name
		):
			self.update_lead_email_tracker_on_reply()
			frappe.logger().info(
				f"[Reply Received] Communication {self.name} linked to CRM Lead: {self.reference_name}"
			)

	def update_lead_email_tracker_on_reply(self):
		"""Update Lead Email Tracker status when a reply is received"""
		try:
			# Find the most recent tracker for this lead that's still active
			tracker = frappe.db.get_value(
				"Lead Email Tracker",
				{
					"lead": self.reference_name,
					"status": ["in", ["Sent", "Opened", "Queued", "Delivered"]]
				},
				["name"],
				order_by="last_sent_on desc"
			)
			
			if tracker:
				frappe.db.set_value(
					"Lead Email Tracker",
					tracker,
					{
						"status": "Replied",
						"last_opened_on": frappe.utils.now_datetime()
					},
					update_modified=False
				)
				frappe.logger().info(
					f"[Reply Tracking] Updated tracker {tracker} status to 'Replied' for lead {self.reference_name}"
				)
				
				# Publish real-time update
				frappe.publish_realtime(
					"lead_email_reply",
					{
						"lead": self.reference_name,
						"tracker": tracker,
						"communication": self.name
					},
					after_commit=True
				)
			else:
				frappe.logger().info(
					f"[Reply Tracking] No active tracker found for lead {self.reference_name}"
				)
				
		except Exception as e:
			frappe.log_error(
				title=f"Failed to update Lead Email Tracker for reply",
				message=f"Lead: {self.reference_name}\nCommunication: {self.name}\nError: {str(e)}\n{frappe.get_traceback()}"
			)

	def on_update(self):
		"""Override to handle missing comment_type attribute"""
		try:
			update_comment_in_doc(self)
			parent = get_parent_doc(self)
			if parent and hasattr(parent, 'on_communication_update') and callable(parent.on_communication_update):
				parent.on_communication_update(self)
			elif parent:
				update_parent_document_on_communication(self)
		except AttributeError as e:
			if 'comment_type' in str(e):
				if not hasattr(self, 'comment_type'):
					self.comment_type = None
				super().on_update()
			else:
				raise


def update_parent_document_on_communication(doc):
	"""Update parent document when communication is received/sent"""
	parent = get_parent_doc(doc)
	if not parent:
		return

	status_field = parent.meta.get_field("status")
	if status_field:
		options = (status_field.options or "").splitlines()

		# Update status for CRM Lead when reply is received
		if (
			(("Open" in options) and parent.status == "Replied" and doc.sent_or_received == "Received")
			or (parent.doctype in ["Issue", "CRM Lead"] and ("Open" in options) and doc.sent_or_received == "Received")
		):
			parent.db_set("status", "Replied")
			if hasattr(parent, 'handle_hold_time'):
				parent.run_method("handle_hold_time", "Replied")
			apply_assignment_rule(parent)

	update_first_response_time(parent, doc)
	set_avg_response_time(parent, doc)
	
	if hasattr(parent, 'notify_communication'):
		parent.run_method("notify_communication", doc)
	parent.notify_update()


def update_first_response_time(parent, communication):
	if parent.meta.has_field("first_response_time") and not parent.get("first_response_time"):
		if (
			is_system_user(communication.sender)
			or frappe.get_cached_value("User", frappe.session.user, "user_type") == "System User"
		):
			if (
				communication.sent_or_received == "Sent"
				and communication.communication_type == "Communication"
			):
				first_responded_on = communication.creation
				if parent.meta.has_field("first_responded_on"):
					parent.db_set("first_responded_on", first_responded_on)
				first_response_time = round(time_diff_in_seconds(first_responded_on, parent.creation), 2)
				parent.db_set("first_response_time", first_response_time)


def set_avg_response_time(parent, communication):
	if parent.meta.has_field("avg_response_time") and communication.sent_or_received == "Sent":
		communications = frappe.get_list(
			"Communication",
			filters={"reference_doctype": parent.doctype, "reference_name": parent.name},
			fields=["sent_or_received", "name", "creation"],
			order_by="creation",
		)

		if len(communications):
			response_times = []
			for i in range(len(communications)):
				if (
					communications[i].sent_or_received == "Sent"
					and communications[i - 1].sent_or_received == "Received"
				):
					response_time = round(
						time_diff_in_seconds(communications[i].creation, communications[i - 1].creation), 2
					)
					if response_time > 0:
						response_times.append(response_time)
			if response_times:
				avg_response_time = sum(response_times) / len(response_times)
				parent.db_set("avg_response_time", avg_response_time)