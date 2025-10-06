# Copyright (c) 2025, Neha and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate
from crm_override.crm_override.broadcast_utils import send_email_to_segment

class EmailCampaign(Document):
    def validate(self):
        self.validate_campaign()
        
    def validate_campaign(self):
        if not self.campaign_name:
            frappe.throw(_("Campaign is required"))
        if not self.sender:
            frappe.throw(_("Sender is required"))
        if getdate(self.start_date) < getdate(nowdate()):
            frappe.throw(_("Start Date cannot be before today"))

    def on_submit(self):
        if not self.status:
            self.status = "Scheduled"

    def send_campaign(self):
        """Process the campaign and send emails"""
        try:
            if self.email_campaign_for == "Lead Segment":
                segment = frappe.get_doc("Lead Segment", self.recipient)
                
                # Get campaign details for email content
                campaign = frappe.get_doc("Campaign", self.campaign_name)
                
                # Prepare email content
                subject = f"Campaign: {campaign.campaign_name}"
                message = campaign.description
                if not message:
                    message = """
                    <div>
                        <p>Thank you for your interest!</p>
                        <p>This is an automated email from our campaign.</p>
                        <br>
                        <p>Best regards,<br>{sender}</p>
                    </div>
                    """.format(sender=self.sender)
                
                print(f"Processing campaign {self.name} for segment {segment.name}")
                
                # Send email using broadcast utility
                response = send_email_to_segment(
                    segment_name=segment.name,
                    subject=subject,
                    message=message,
                    sender_email=self.sender
                )
                
                # Update campaign status based on response
                if response:
                    successful_sends = sum(1 for r in response if r.get('status') == 'success')
                    print(f"Campaign {self.name} processed. Successful sends: {successful_sends}")
                    
                    self.db_set('status', 'Completed')
                    self.db_set('end_date', nowdate())
                    
                    # Log the campaign completion
                    frappe.log_error(
                        message=f"Campaign {self.name} completed. Successfully sent to {successful_sends} out of {len(response)} recipients.",
                        title="Campaign Complete"
                    )
                    
                return response
                
        except Exception as e:
            frappe.log_error(
                message=f"Failed to process campaign {self.name}: {str(e)}",
                title="Campaign Processing Error"
            )
            raise

def process_email_campaigns():
    """
    Background job to process scheduled email campaigns
    This will be called by a scheduler
    """
    print("Starting email campaign processing...")
    
    campaigns = frappe.get_all(
        "Email Campaign",
        filters={
            "status": "Scheduled",
            "start_date": ["<=", nowdate()]
        }
    )
    
    print(f"Found {len(campaigns)} campaigns to process")
    
    for campaign in campaigns:
        try:
            print(f"\nProcessing campaign: {campaign.name}")
            
            email_campaign = frappe.get_doc("Email Campaign", campaign.name)
            email_campaign.db_set('status', 'In Progress')
            frappe.db.commit()
            
            print(f"Campaign {campaign.name} status set to In Progress")
            
            response = email_campaign.send_campaign()
            
            # Log success
            if response:
                successful = sum(1 for r in response if r.get('status') == 'success')
                print(f"Campaign {campaign.name} completed. Sent {successful} of {len(response)} emails")
            
        except Exception as e:
            error_msg = f"Failed to process campaign {campaign.name}: {str(e)}"
            print(f"Error: {error_msg}")
            
            # Log error and update campaign status
            frappe.log_error(
                message=error_msg,
                title="Campaign Processing Error"
            )
            
            try:
                email_campaign = frappe.get_doc("Email Campaign", campaign.name)
                email_campaign.db_set('status', 'Error')
                frappe.db.commit()
            except:
                print(f"Could not update status for campaign {campaign.name}")
                
    print("\nEmail campaign processing complete")

def setup_scheduler():
    """Setup scheduler events for email campaigns"""
    try:
        # Add a scheduled job for email campaign processing
        if not frappe.db.exists("Scheduled Job Type", "process_email_campaigns"):
            frappe.get_doc({
                "doctype": "Scheduled Job Type",
                "method": "crm_override.crm_override.doctype.email_campaign.email_campaign.process_email_campaigns",
                "frequency": "All",
                "name": "process_email_campaigns",
                "documentation": "Process scheduled email campaigns"
            }).insert()
            
        print("Email campaign scheduler setup complete")
    except Exception as e:
        print(f"Failed to setup scheduler: {str(e)}")
