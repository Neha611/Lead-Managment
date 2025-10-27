# Copyright (c) 2025, Neha and contributors
# For license information, please see license.txt
import frappe
from frappe.utils import time_diff_in_seconds, parse_addr
from frappe.utils.user import is_system_user
from frappe.model.document import Document
from frappe.core.utils import get_parent_doc
from frappe.automation.doctype.assignment_rule.assignment_rule import (
	apply as apply_assignment_rule,
)
from frappe.core.doctype.communication.communication import Communication as BaseCommunication
from frappe.core.doctype.communication.communication import update_comment_in_doc


class Communication(BaseCommunication):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		# Backward compatibility for older frappe versions (pre-v12) that had comment_type
		if not hasattr(self, 'comment_type'):
			self.comment_type = None

	def validate(self):
		"""Override validate to add custom linking logic for replies"""
		super().validate()
		
		# Link incoming emails to CRM Lead if they're replies
		if (
			self.communication_medium == "Email" 
			and self.sent_or_received == "Received"
			and not self.reference_doctype
		):
			self.link_to_crm_lead_from_email()

	def link_to_crm_lead_from_email(self):
		"""
		Try to link incoming email to a CRM Lead by:
		1. Checking if it's a reply to a previous communication (using In-Reply-To or References headers)
		2. Looking up CRM Lead by sender email address
		3. Checking Email Queue for original message
		"""
		linked = False
		
		# Method 1: Check if this is a reply using in_reply_to or message_id
		if self.in_reply_to:
			# Try to find original communication by message_id
			original_comms = frappe.get_all(
				"Communication",
				filters={"message_id": self.in_reply_to},
				fields=["reference_doctype", "reference_name", "name"],
				limit=1
			)
			
			if original_comms:
				original = original_comms[0]
				# Check if it references CRM Lead or standard Lead
				if original.reference_doctype in ["CRM Lead", "Lead"]:
					self.reference_doctype = original.reference_doctype
					self.reference_name = original.reference_name
					linked = True
					frappe.logger().info(f"Linked reply to {original.reference_doctype}: {original.reference_name} via in_reply_to")
		
		# Method 2: Check Email Queue for the original message
		if not linked and self.in_reply_to:
			email_queues = frappe.get_all(
				"Email Queue",
				filters={"message_id": self.in_reply_to},
				fields=["reference_doctype", "reference_name"],
				limit=1
			)
			
			if email_queues:
				eq = email_queues[0]
				if eq.reference_doctype in ["CRM Lead", "Lead"]:
					self.reference_doctype = eq.reference_doctype
					self.reference_name = eq.reference_name
					linked = True
					frappe.logger().info(f"Linked reply to {eq.reference_doctype}: {eq.reference_name} via Email Queue")
		
		# Method 3: Find by sender email in CRM Lead
		if not linked and self.sender:
			sender_email = parse_addr(self.sender)[1] if self.sender else None
			
			if sender_email:
				# First try CRM Lead
				crm_lead = frappe.db.get_value(
					"CRM Lead",
					{"email": sender_email},
					["name"],
					order_by="modified desc"
				)
				
				if crm_lead:
					self.reference_doctype = "CRM Lead"
					self.reference_name = crm_lead
					linked = True
					frappe.logger().info(f"Linked reply to CRM Lead: {crm_lead} via sender email")
				else:
					# Fallback to standard Lead
					lead = frappe.db.get_value(
						"Lead",
						{"email_id": sender_email},
						["name"],
						order_by="modified desc"
					)
					
					if lead:
						self.reference_doctype = "Lead"
						self.reference_name = lead
						linked = True
						frappe.logger().info(f"Linked reply to Lead: {lead} via sender email")
		
		# Method 4: Check subject line for Lead ID (e.g., #CRM-LEAD-2025-06678)
		if not linked and self.subject:
			import re
			# Look for CRM Lead pattern in subject
			crm_lead_match = re.search(r'#?CRM-LEAD-\d{4}-\d+', self.subject, re.IGNORECASE)
			if crm_lead_match:
				lead_id = crm_lead_match.group(0).replace('#', '')
				if frappe.db.exists("CRM Lead", lead_id):
					self.reference_doctype = "CRM Lead"
					self.reference_name = lead_id
					linked = True
					frappe.logger().info(f"Linked reply to CRM Lead: {lead_id} via subject line")
		
		if linked:
			# Update status to show it's been linked
			self.status = "Linked"

	def on_update(self):
		"""Override to handle missing comment_type attribute in older frappe versions"""
		try:
			# Call parent's on_update
			update_comment_in_doc(self)

			# Handle parent doc communication update
			parent = get_parent_doc(self)
			if parent and hasattr(parent, 'on_communication_update') and callable(parent.on_communication_update):
				parent.on_communication_update(self)
			elif parent:
				update_parent_document_on_communication(self)
		except AttributeError as e:
			# If attribute error occurs (like comment_type), handle gracefully
			if 'comment_type' in str(e):
				# Set default and retry
				if not hasattr(self, 'comment_type'):
					self.comment_type = None
				# Try parent method again
				super().on_update()
			else:
				raise

	def after_insert(self):
		"""Override to ensure proper linking after insert"""
		super().after_insert()

		# If this is a received email and still not linked, try one more time
		if (
			self.communication_medium == "Email"
			and self.sent_or_received == "Received"
			and not self.reference_doctype
		):
			self.link_to_crm_lead_from_email()
			if self.reference_doctype:
				# Update the document without triggering full save
				frappe.db.set_value(
					"Communication",
					self.name,
					{
						"reference_doctype": self.reference_doctype,
						"reference_name": self.reference_name,
						"status": "Linked"
					},
					update_modified=False
				)
				frappe.logger().info(f"After insert: Linked Communication {self.name} to {self.reference_doctype}: {self.reference_name}")

		# Delete original message if this is a reply
		self.delete_original_message_on_reply()

	def delete_original_message_on_reply(self):
		"""
		Delete the original message when a reply is received.
		Only keeps the latest reply in the conversation thread.
		"""
		# Only process received emails that are replies
		if (
			self.communication_medium != "Email"
			or self.sent_or_received != "Received"
			or not self.in_reply_to
		):
			return

		try:
			# Check if the original communication exists first
			if not frappe.db.exists("Communication", self.in_reply_to):
				# Original not found - silently skip
				return

			# Get the original communication
			original_comm = frappe.get_doc("Communication", self.in_reply_to)

			# Only delete if the original was sent by us (not another received email)
			if original_comm.sent_or_received == "Sent":
				# Unlink from all linked documents first
				self._unlink_communication_from_linked_docs(original_comm.name)

				# Commit the unlinking before attempting delete
				frappe.db.commit()

				# Now delete the communication
				frappe.delete_doc("Communication", original_comm.name, ignore_permissions=True, force=True)
				frappe.db.commit()
				frappe.logger().info(
					f"Deleted original message {original_comm.name} after receiving reply {self.name}"
				)
		except Exception:
			# Silently skip all errors to prevent breaking email receive process
			pass

	def _unlink_communication_from_linked_docs(self, communication_name):
		"""Unlink communication from Lead Email Tracker and other linked records."""
		# Direct SQL updates for known link fields
		known_links = [
			("Lead Email Tracker", "communication"),
			("Email Queue", "communication"),
			("Communication", "in_reply_to"),
		]

		for doctype, fieldname in known_links:
			try:
				# Check if doctype/table exists before attempting update
				if not frappe.db.table_exists(f"tab{doctype}"):
					continue

				frappe.db.sql(
					f"UPDATE `tab{doctype}` SET `{fieldname}` = NULL WHERE `{fieldname}` = %s",
					(communication_name,)
				)
			except frappe.db.TableMissingError:
				# Table doesn't exist - silently skip
				pass
			except frappe.db.InternalError as e:
				# Handle SQL errors like unknown column - silently skip
				if "Unknown column" in str(e) or "doesn't exist" in str(e):
					pass
				else:
					# Log unexpected SQL errors but don't fail
					try:
						frappe.logger().debug(f"SQL error unlinking {doctype}.{fieldname}: {str(e)}")
					except Exception:
						pass
			except Exception:
				# Silently skip any other errors
				pass


def update_parent_document_on_communication(doc):
	"""Update mins_to_first_communication of parent document based on who is replying."""

	parent = get_parent_doc(doc)
	if not parent:
		return

	status_field = parent.meta.get_field("status")
	if status_field:
		options = (status_field.options or "").splitlines()

		# Handle both Issue and CRM Lead status updates
		if (
			(("Open" in options) and parent.status == "Replied" and doc.sent_or_received == "Received")
			or (
				parent.doctype in ["Issue", "CRM Lead"] and ("Open" in options) and doc.sent_or_received == "Received"
			)
		):
			parent.db_set("status", "Open")
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
		# avg response time for all the responses
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