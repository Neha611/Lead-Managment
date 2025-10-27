import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate, get_datetime, now_datetime
from crm_override.crm_override.broadcast_utils import launch_campaign

class EmailCampaign(Document):
    @staticmethod
    def default_list_data():
        columns = [
            {
                "label": "Campaign Name",
                "type": "Link",
                "key": "campaign_name",
                "options": "Campaign",
                "width": "14rem",
            },
            {
                "label": "Recipient",
                "type": "Data",
                "key": "recipient",
                "width": "14rem",
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
            "campaign_name",
            "email_campaign_for",
            "recipient",
            "sender",
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
        if not self.campaign_name:
            frappe.throw(_("Campaign is required"))
        if not self.sender:
            frappe.throw(_("Sender is required"))
        if not self.recipient:
            frappe.throw(_("Recipient (Lead Segment) is required"))
        # UTM Campaign is optional, do not throw if missing
        # if hasattr(self, 'utm_campaign') and not self.utm_campaign:
        #     frappe.msgprint(_("UTM Campaign is not set. Proceeding without UTM tracking."), indicator="orange")
        # Allow start_date to be today or in the future
        if self.start_date and getdate(self.start_date) < getdate(nowdate()):
            if self.is_new() or self.docstatus == 0:
                frappe.msgprint(
                    _("Start Date is in the past. Campaign will be scheduled immediately."),
                    indicator="orange"
                )

    def after_insert(self):
    # Trigger campaign immediately after creation
        self.launch_if_ready()

    def on_update(self):
        # Optional: launch again if updated later and not launched yet
        if not self.status or self.status not in ["Launched", "Error"]:
            self.launch_if_ready()

    def launch_if_ready(self):
        """Handles scheduling logic previously in on_submit"""
        try:
            self.status = self.status or "Scheduled"

            # Support both Lead Segment and CRM Lead
            if self.email_campaign_for in ["Lead Segment", "CRM Lead"]:
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
                    f"[Email Campaign] Launching campaign '{self.name}' for {self.email_campaign_for} '{self.recipient}' at {start_datetime}"
                )

                # Launch campaign
                if self.email_campaign_for == "Lead Segment":
                    result = launch_campaign(
                        campaign_name=self.campaign_name,
                        recipient_type="Lead Segment",
                        recipient_id=self.recipient,
                        sender_email=self.sender,
                        start_datetime=start_datetime
                    )
                elif self.email_campaign_for == "CRM Lead":
                    result = launch_campaign(
                        campaign_name=self.campaign_name,
                        recipient_type="CRM Lead",
                        recipient_id=self.recipient,
                        sender_email=self.sender,
                        start_datetime=start_datetime
                    )

                self.db_set('status', 'Launched')
                frappe.db.commit()

                frappe.msgprint(
                    _("Campaign {0} launched successfully. "
                    "Scheduled {1} emails across {2} batches starting from {3}.")
                    .format(
                        self.name,
                        result.get('emails_scheduled', 0),
                        result.get('total_schedules', 0),
                        result.get('base_time', 'now')
                    ),
                    indicator="green"
                )
            else:
                frappe.throw(_("Campaign type {0} not supported.").format(self.email_campaign_for))

        except Exception as e:
            error_message = f"Failed to launch campaign {self.name}: {str(e)}"
            frappe.log_error(message=f"{error_message}\n\n{frappe.get_traceback()}",
                            title="Email Campaign Save Error")
            self.db_set('status', 'Error')
            frappe.throw(_("Failed to launch campaign. Error: {0}").format(str(e)))
    def on_cancel(self):
        """
        When campaign is cancelled, cancel all scheduled emails.
        """
        try:
            # Import here to avoid circular dependency
            from crm_override.crm_override.broadcast_utils import cancel_scheduled_emails, get_scheduled_emails
            
            # Get all scheduled emails for this campaign's segment
            segment = frappe.get_doc("Lead Segment", self.recipient)
            
            # Get emails from all leads in segment
            lead_emails = []
            for lead_item in segment.leads:
                lead_doc = frappe.get_doc("CRM Lead", lead_item.lead)
                if hasattr(lead_doc, 'email') and lead_doc.email:
                    lead_emails.append(lead_doc.email)
            
            # Cancel scheduled emails for each lead
            total_cancelled = 0
            for email in lead_emails:
                result = cancel_scheduled_emails(lead_email=email)
                total_cancelled += result.get('cancelled_count', 0)
            
            self.db_set('status', 'Cancelled')
            frappe.db.commit()
            
            if total_cancelled > 0:
                frappe.msgprint(
                    _("Campaign cancelled. {0} scheduled emails were cancelled.").format(total_cancelled),
                    indicator="orange"
                )
            else:
                frappe.msgprint(_("Campaign cancelled."), indicator="orange")
                
        except Exception as e:
            frappe.log_error(
                message=f"Error cancelling scheduled emails for campaign {self.name}: {str(e)}",
                title="Campaign Cancellation Error"
            )
            # Still set status to cancelled even if email cancellation fails
            self.db_set('status', 'Cancelled')
            frappe.db.commit()