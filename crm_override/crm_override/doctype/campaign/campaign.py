# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import set_name_by_naming_series


class Campaign(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.
	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF
		from crm_override.crm_override.doctype.campaign_email_schedule.campaign_email_schedule import (
			CampaignEmailSchedule,
		)
		from crm_override.crm_override.doctype.campaign_call_schedule.campaign_call_schedule import (
			CampaignCallSchedule,
		)

		campaign_name: DF.Data
		campaign_type: DF.Link | None  # "Email" or "Call"
		campaign_schedules: DF.Table[CampaignEmailSchedule]
		campaign_schedules_call: DF.Table[CampaignCallSchedule]
		description: DF.Text | None
		naming_series: str | None
	# end: auto-generated types

	# ---------------------------
	#  Core Logic
	# ---------------------------
	def validate(self):
		self.validate_campaign_type()

	def after_insert(self):
		self.sync_utm_campaign()

	def on_change(self):
		self.sync_utm_campaign()

	def autoname(self):
		if not self.campaign_name:
			frappe.throw(_("Campaign Name is required"))

		if frappe.defaults.get_global_default("campaign_naming_by") == "Naming Series":
			if not self.naming_series:
				self.naming_series = "SAL-CAM-.YYYY.-"
			set_name_by_naming_series(self)
		else:
			self.name = self.campaign_name

	# ---------------------------
	#  Custom Methods
	# ---------------------------
	def validate_campaign_type(self):
		if not self.campaign_type:
			frappe.throw(_("Please select a Campaign Type (Email or Call)."))

	def sync_utm_campaign(self):
		"""Sync campaign with UTM Campaign record."""
		if frappe.db.exists("UTM Campaign", self.campaign_name):
			mc = frappe.get_doc("UTM Campaign", self.campaign_name)
		else:
			mc = frappe.new_doc("UTM Campaign")
			mc.name = self.campaign_name

		mc.campaign_description = self.description
		mc.crm_campaign = self.campaign_name
		mc.save(ignore_permissions=True)

	@staticmethod
	def default_list_data():
		columns = [
			{"label": "Campaign Name", "type": "Data", "key": "campaign_name", "width": "16rem"},
			{"label": "Type", "type": "Data", "key": "campaign_type", "width": "6rem"},
			{"label": "Description", "type": "Text", "key": "description", "width": "20rem"},
			{"label": "Last Modified", "type": "Datetime", "key": "modified", "width": "8rem"},
		]
		rows = ["name", "campaign_name", "campaign_type", "description", "modified", "creation"]
		return {"columns": columns, "rows": rows}
