# Copyright (c) 2025, Neha and Contributors
# See license.txt


import unittest
import frappe
import random
from crm_override.crm_override.broadcast_utils import create_lead_segment
from crm_override.crm_override.doctype.email_campaign.email_campaign import EmailCampaign

class TestEmailCampaign(unittest.TestCase):
	def setUp(self):
		frappe.db.rollback()

	def test_send_campaign(self):
		# Create a unique sender user
		unique_sender_email = f"sender{random.randint(1000, 9999)}@example.com"
		try:
			user_doc = frappe.get_doc("User", unique_sender_email)
		except frappe.DoesNotExistError:
			user_doc = frappe.get_doc({
				"doctype": "User",
				"email": unique_sender_email,
				"first_name": "Sender",
				"user_type": "System User",
				"enabled": 1
			}).insert()
		# Create a lead
		# Remove all Lead Segments and Lead Segment Items first
		for seg in frappe.get_all("Lead Segment", fields=["name"]):
			frappe.delete_doc("Lead Segment", seg["name"], ignore_permissions=True)
		frappe.db.commit()
		for item in frappe.get_all("Lead Segment Item", fields=["name"]):
			frappe.delete_doc("Lead Segment Item", item["name"], ignore_permissions=True)
		frappe.db.commit()
		# Remove all CRM Leads
		for lead in frappe.get_all("CRM Lead", fields=["name"]):
			frappe.delete_doc("CRM Lead", lead["name"], ignore_permissions=True)
		frappe.db.commit()
		# Create a single valid CRM Lead
		lead = frappe.get_doc({
			"doctype": "CRM Lead",
			"lead_name": "Test Lead",
			"first_name": "Test",
			"email": "lead@example.com"
		}).insert()
		frappe.db.commit()
		lead.reload()
		print(f"DEBUG: Created CRM Lead with name: {lead.name}")
		exists = frappe.db.exists("CRM Lead", lead.name)
		print(f"DEBUG: CRM Lead exists in DB: {exists}")
		self.assertEqual(lead.email, "lead@example.com")
		lead_names = [lead.name]
		segment = create_lead_segment("Test Segment 3", lead_names)
		frappe.db.commit()
		segment.reload()
		# Create a Campaign document and use its name
		campaign_doc = frappe.get_doc({
			"doctype": "Campaign",
			"campaign_name": f"Test Campaign {random.randint(1000,9999)}",
			"naming_series": "SAL-CAM-.YYYY.-",
			"description": "Test campaign for email broadcast"
		}).insert()
		frappe.db.commit()
		campaign = frappe.get_doc({
			"doctype": "Email Campaign",
			"campaign_name": campaign_doc.name,
			"email_campaign_for": "CRM Lead",
			"recipient": lead.name,
			"sender": user_doc.name,
			"subject": "Test Subject",
			"message": "Test Message",
			"segment": segment.name,
			"start_date": frappe.utils.nowdate()
		}).insert()
		frappe.db.commit()
		result = EmailCampaign(campaign).send_campaign()
		self.assertEqual(len(result), 1)
		self.assertEqual(result[0]["status_code"], 200)
