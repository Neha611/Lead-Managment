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

		campaign_name: DF.Data
		campaign_schedules: DF.Table[CampaignEmailSchedule]
		description: DF.Text | None
		naming_series: str | None
	# end: auto-generated types

	def after_insert(self):
		if frappe.db.exists("UTM Campaign", self.campaign_name):
			mc = frappe.get_doc("UTM Campaign", self.campaign_name)
		else:
			mc = frappe.new_doc("UTM Campaign")
			mc.name = self.campaign_name
		mc.campaign_description = self.description
		mc.crm_campaign = self.campaign_name
		mc.save(ignore_permissions=True)

	def on_change(self):
		try:
			mc = frappe.get_doc("UTM Campaign", self.campaign_name)
		except frappe.DoesNotExistError:
			mc = frappe.new_doc("UTM Campaign")
			mc.name = self.campaign_name
		mc.campaign_description = self.description
		mc.crm_campaign = self.campaign_name
		mc.save(ignore_permissions=True)

	def autoname(self):
		if not self.campaign_name:
			frappe.throw(_("Campaign Name is required"))
			
		if frappe.defaults.get_global_default("campaign_naming_by") == "Naming Series":
			if not self.naming_series:
				self.naming_series = "SAL-CAM-.YYYY.-"
			set_name_by_naming_series(self)
		else:
			self.name = self.campaign_name
