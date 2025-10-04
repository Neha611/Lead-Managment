from frappe.model.document import Document
# Copyright (c) 2025, Neha and contributors
# For license information, please see license.txt





import frappe

from crm_override.crm_override.broadcast_utils import send_email_to_segment

class EmailCampaign(Document):
	def send_campaign(self):
		"""
		Send campaign email to all leads in the selected segment.
		"""
		if not self.segment:
			frappe.throw("No segment selected.")
		if not self.subject or not self.message or not self.sender:
			frappe.throw("Subject, message, and sender are required.")
		return send_email_to_segment(self.segment, self.subject, self.message, self.sender)
