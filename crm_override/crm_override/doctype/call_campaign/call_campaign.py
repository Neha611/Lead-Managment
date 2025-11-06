# Copyright (c) 2025, Neha and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate, get_datetime, now_datetime
from crm_override.crm_override.broadcast_calls_utils import launch_call_campaign


class CallCampaign(Document):
    @staticmethod
    def default_list_data():
        columns = [
            {
                "label": "Campaign Name",
                "type": "Link",
                "key": "campaign",
                "options": "Campaign",
                "width": "14rem",
            },
            {
                "label": "Recipient",
                "type": "Data",
                "key": "recipient",
                "width": "12rem",
            },
            {
                "label": "Status",
                "type": "Select",
                "key": "status",
                "width": "10rem",
            },
            {
                "label": "Start Date",
                "type": "Date",
                "key": "start_date",
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
            "campaign",
            "call_campaign_for",
            "recipient",
            "status",
            "start_date",
            "end_date",
            "modified",
            "creation",
        ]
        return {"columns": columns, "rows": rows}

    def validate(self):
        self.validate_campaign()

    def validate_campaign(self):
        """Validate required fields for the campaign"""
        if not self.campaign:
            frappe.throw(_("Campaign is required"))
        if not self.recipient:
            frappe.throw(_("Recipient is required"))
        
        # Allow start_date to be today or in the future
        if self.start_date and getdate(self.start_date) < getdate(nowdate()):
            if self.is_new() or self.docstatus == 0:
                frappe.msgprint(
                    _("Start Date is in the past. Campaign will be scheduled immediately."),
                    indicator="orange"
                )

    def after_insert(self):
        """Trigger campaign immediately after creation"""
        self.launch_if_ready()

    def on_update(self):
        """Optional: launch again if updated later and not launched yet"""
        if not self.status or self.status not in ["Launched", "In Progress", "Completed", "Error"]:
            self.launch_if_ready()

    def launch_if_ready(self):
        """Handles scheduling logic for call campaigns"""
        try:
            self.status = self.status or "Scheduled"

            # Support both Lead Segment and CRM Lead
            if self.call_campaign_for in ["Lead Segment", "CRM Lead"]:
                # Base start time
                if self.start_date:
                    start_datetime = get_datetime(self.start_date)
                    if start_datetime < now_datetime():
                        frappe.msgprint(
                            _("Start date is in the past. Using current time instead."),
                            indicator="orange"
                        )
                        start_datetime = now_datetime()
                else:
                    start_datetime = now_datetime()

                frappe.logger().info(
                    f"[Call Campaign] Launching campaign '{self.name}' for {self.call_campaign_for} '{self.recipient}' at {start_datetime}"
                )

                # Launch campaign
                if self.call_campaign_for == "Lead Segment":
                    result = launch_call_campaign(
                        campaign_name=self.campaign,
                        recipient_type="Lead Segment",
                        recipient_id=self.recipient,
                        start_datetime=start_datetime,
                        call_campaign_name=self.name
                    )
                elif self.call_campaign_for == "CRM Lead":
                    result = launch_call_campaign(
                        campaign_name=self.campaign,
                        recipient_type="CRM Lead",
                        recipient_id=self.recipient,
                        start_datetime=start_datetime,
                        call_campaign_name=self.name
                    )

                self.db_set('status', 'Launched')
                frappe.db.commit()

                frappe.msgprint(
                    _("Call Campaign {0} launched successfully. "
                    "Scheduled {1} calls across {2} batches starting from {3}.")
                    .format(
                        self.name,
                        result.get('calls_scheduled', 0),
                        result.get('total_schedules', 0),
                        result.get('base_time', 'now')
                    ),
                    indicator="green"
                )
            else:
                frappe.throw(_("Campaign type {0} not supported.").format(self.call_campaign_for))

        except Exception as e:
            error_message = f"Failed to launch call campaign {self.name}: {str(e)}"
            frappe.log_error(
                message=f"{error_message}\n\n{frappe.get_traceback()}",
                title="Call Campaign Save Error"
            )
            self.db_set('status', 'Error')
            frappe.throw(_("Failed to launch campaign. Error: {0}").format(str(e)))

    def on_cancel(self):
        """When campaign is cancelled, update status"""
        try:
            self.db_set('status', 'Cancelled')
            self.db_set('end_date', nowdate())
            frappe.db.commit()
            
            frappe.msgprint(_("Call Campaign cancelled."), indicator="orange")
                
        except Exception as e:
            frappe.log_error(
                message=f"Error cancelling call campaign {self.name}: {str(e)}",
                title="Call Campaign Cancellation Error"
            )
            # Still set status to cancelled even if there's an error
            self.db_set('status', 'Cancelled')
            frappe.db.commit()