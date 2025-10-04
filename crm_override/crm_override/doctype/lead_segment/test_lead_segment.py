# Copyright (c) 2025, Neha and Contributors
# See license.txt


import frappe
from frappe.tests.utils import FrappeTestCase
from unittest.mock import patch, MagicMock
from crm_override.crm_override.broadcast_utils import create_lead_segment, send_email_to_segment

class LeadSegmentTestCase(FrappeTestCase):
	def setUp(self):
		frappe.db.rollback()

	def setUp(self):
		"""Create test leads and clean up old data"""
		super().setUp()
		# Delete old test data
		self._cleanup_test_data()
		
		# Create test leads
		self.test_leads = []
		lead_data = [
			{
				"name": "CRM-LEAD-2025-00001",
				"lead_name": "Test Lead 1",
				"first_name": "Test1",
				"email": "lead1@example.com"
			},
			{
				"name": "CRM-LEAD-2025-00002",
				"lead_name": "Test Lead 2",
				"first_name": "Test2",
				"email": "lead2@example.com"
			},
			{
				"name": "CRM-LEAD-2025-00003",
				"lead_name": "Test Lead 3",
				"first_name": "Test3",
				"email": "lead3@example.com"
			}
		]
		
		for data in lead_data:
			lead = frappe.get_doc({
				"doctype": "CRM Lead",
				**data
			}).insert(ignore_if_duplicate=True)
			self.test_leads.append(lead.name)
		
		frappe.db.commit()

	def _cleanup_test_data(self):
		"""Clean up test data"""
		# Delete Email Campaigns
		for ec in frappe.get_all("Email Campaign", fields=["name"]):
			frappe.delete_doc("Email Campaign", ec["name"], ignore_permissions=True)
		
		# Delete Lead Segments
		for seg in frappe.get_all("Lead Segment", fields=["name"]):
			frappe.delete_doc("Lead Segment", seg["name"], ignore_permissions=True)
		
		# Delete test leads
		test_lead_names = ["CRM-LEAD-2025-00001", "CRM-LEAD-2025-00002", "CRM-LEAD-2025-00003"]
		for lead in frappe.get_all("CRM Lead", fields=["name"]):
			if lead["name"] in test_lead_names:
				frappe.delete_doc("CRM Lead", lead["name"], ignore_permissions=True)
		
		frappe.db.commit()

	def test_create_segment_basic(self):
		"""Test basic segment creation with two leads"""
		# Create segment with two leads
		segment = create_lead_segment("Test Segment", 
			[self.test_leads[0], self.test_leads[1]], 
			description="Test segment with two leads")
		frappe.db.commit()

		self.assertEqual(segment.segmentname, "Test Segment")
		self.assertEqual(len(segment.leads), 2)
		self.assertEqual(
			set([item.lead for item in segment.leads]), 
			set([self.test_leads[0], self.test_leads[1]])
		)

	def test_create_segment_single_lead(self):
		"""Test segment creation with a single lead"""
		segment = create_lead_segment("Single Lead Segment", 
			[self.test_leads[0]], 
			description="Test segment with one lead")
		frappe.db.commit()

		self.assertEqual(segment.segmentname, "Single Lead Segment")
		self.assertEqual(len(segment.leads), 1)
		self.assertEqual(segment.leads[0].lead, self.test_leads[0])

	def test_create_segment_all_leads(self):
		"""Test segment creation with all test leads"""
		segment = create_lead_segment("All Leads Segment", 
			self.test_leads, 
			description="Test segment with all leads")
		frappe.db.commit()

		self.assertEqual(segment.segmentname, "All Leads Segment")
		self.assertEqual(len(segment.leads), len(self.test_leads))
		self.assertEqual(
			set([item.lead for item in segment.leads]), 
			set(self.test_leads)
		)

	def test_segment_validation(self):
		"""Test validation cases"""
		# Test empty segment name
		with self.assertRaises(frappe.ValidationError):
			create_lead_segment("", self.test_leads)

		# Test empty leads list
		with self.assertRaises(frappe.ValidationError):
			create_lead_segment("Empty Segment", [])
