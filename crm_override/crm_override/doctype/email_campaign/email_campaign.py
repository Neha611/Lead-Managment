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
        
        # Allow start_date to be today or in the future
        # Don't validate against past if it's being submitted now
        if self.start_date and getdate(self.start_date) < getdate(nowdate()):
            # Only throw error if document is new or not being submitted
            if self.is_new() or self.docstatus == 0:
                frappe.msgprint(
                    _("Start Date is in the past. Campaign will be scheduled immediately."),
                    indicator="orange"
                )

    def on_submit(self):
        """
        Automatically launches the campaign when the document is submitted.
        Schedules emails based on the campaign schedule and start_date.
        """
        # Set status to Scheduled upon submission
        if not self.status:
            self.status = "Scheduled"
            
        try:
            if self.email_campaign_for == "Lead Segment":
                # Determine the base start time for scheduling
                if self.start_date:
                    # If start_date is provided, use it as the base time
                    start_datetime = get_datetime(self.start_date)
                    
                    # If start_date is in the past, use current time instead
                    if start_datetime < now_datetime():
                        frappe.msgprint(
                            _("Start date is in the past. Using current time instead."),
                            indicator="orange"
                        )
                        start_datetime = now_datetime()
                else:
                    # If no start_date, schedule immediately
                    start_datetime = now_datetime()
                
                frappe.logger().info(
                    f"[Email Campaign] Launching campaign '{self.name}' "
                    f"for segment '{self.recipient}' starting at {start_datetime}"
                )
                
                # Launch the campaign with the calculated start time
                result = launch_campaign(
                    campaign_name=self.campaign_name,
                    segment_name=self.recipient,
                    sender_email=self.sender,
                    start_datetime=start_datetime
                )
                
                # Update the Email Campaign status to 'Launched'
                self.db_set('status', 'Launched')
                frappe.db.commit()

                # Show success message with details
                message = _(
                    "Campaign {0} launched successfully. "
                    "Scheduled {1} emails across {2} batches starting from {3}."
                ).format(
                    self.name,
                    result.get('emails_scheduled', 0),
                    result.get('total_schedules', 0),
                    result.get('base_time', 'now')
                )
                
                frappe.msgprint(message, indicator="green")
                
                # Log schedule details
                if result.get('schedule_details'):
                    schedule_info = "\n".join([
                        f"- {d['template']}: {d['send_time']} ({d['delay']}) - {d['emails_scheduled']} emails"
                        for d in result['schedule_details']
                    ])
                    frappe.logger().info(
                        f"[Email Campaign] Schedule details for {self.name}:\n{schedule_info}"
                    )
                    
            else:
                frappe.throw(
                    _("Campaign type {0} not supported for automatic scheduling.").format(
                        self.email_campaign_for
                    )
                )

        except Exception as e:
            error_message = f"Failed to launch and schedule campaign {self.name}: {str(e)}"
            frappe.log_error(
                message=f"{error_message}\n\n{frappe.get_traceback()}",
                title="Automatic Campaign Scheduling Error"
            )
            
            # Set status to Error if scheduling fails
            self.db_set('status', 'Error')
            frappe.db.commit()
            
            # Show error to user
            frappe.throw(
                _("Failed to launch campaign. Error: {0}").format(str(e))
            )

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