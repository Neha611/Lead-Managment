from frappe.model.document import Document
# Copyright (c) 2025, Neha and contributors
# For license information, please see license.txt





import frappe

from crm_override.crm_override.broadcast_utils import send_email_to_segment

class LeadSegment(Document):
	@staticmethod
	def default_list_data():
		columns = [
			{
				"label": "Segment Name",
				"type": "Data",
				"key": "segmentname",
				"width": "16rem",
			},
			{
				"label": "Owner",
				"type": "Link",
				"key": "owner",
				"options": "User",
				"width": "12rem",
			},
			{
				"label": "Created On",
				"type": "Datetime",
				"key": "creation",
				"width": "10rem",
			},
			{
				"label": "Last Modified",
				"type": "Datetime",
				"key": "modified",
				"width": "10rem",
			},
		]
		rows = [
			"name",
			"segmentname",
			"owner",
			"creation",
			"modified",
		]
		return {'columns': columns, 'rows': rows}

	def create_segment(self, lead_names, description=None):
		"""
		Create a segment with selected leads.
		"""
		self.segmentname = self.segmentname or self.name
		self.description = description or self.description
		self.leads = [{"lead": lead} for lead in lead_names]
		self.save()
		frappe.db.commit()
		return self

	def send_broadcast(self, subject, message, sender_email):
		"""
		Send broadcast email to all leads in this segment.
		"""
		return send_email_to_segment(self.name, subject, message, sender_email)
