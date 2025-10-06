import frappe
from frappe.utils import add_days, getdate, now_datetime
from frappe.utils.background_jobs import enqueue
from frappe.email.doctype.email_template.email_template import get_email_template

def create_lead_segment(segmentname, lead_names, description=None):
    """
    Create a Lead Segment with selected leads.
    :param segmentname: Name of the segment
    :param lead_names: List of Lead names (IDs)
    :param description: Optional description
    :return: Lead Segment doc
    """
    segment = frappe.get_doc({
        "doctype": "Lead Segment",
        "segmentname": segmentname,
        "description": description or "",
        "leads": [
            {"lead": lead} for lead in lead_names
        ]
    })
    segment.insert()
    frappe.db.commit()
    return segment


def send_email_to_segment(segment_name, subject, message, sender_email):
    """
    Send an email to all leads in a segment using Frappe's sendmail (via SMTP)
    and log the communication in CRM.
    
    :param segment_name: Name of the Lead Segment
    :param subject: Email subject
    :param message: Email body (HTML/text)
    :param sender_email: Email address of sender (must match a configured Email Account in Frappe)
    :return: List of responses
    """
    segment = frappe.get_doc("Lead Segment", segment_name)
    lead_items = segment.leads
    responses = []

    for item in lead_items:
        try:
            lead_doc = frappe.get_doc("CRM Lead", item.lead)
            recipient_email = getattr(lead_doc, "email", None)

            if not recipient_email:
                responses.append({
                    "lead": item.lead,
                    "status": "skipped",
                    "message": "Lead has no email address"
                })
                continue

            # Send email using Frappe's email engine
            frappe.sendmail(
                recipients=[recipient_email],
                sender=sender_email,
                subject=subject,
                message=message,
                delayed=False  # send immediately; set True to queue
            )

            # Log email in CRM
            comm = frappe.get_doc({
                "doctype": "Communication",
                "communication_type": "Communication",
                "communication_medium": "Email",
                "subject": subject,
                "content": message,
                "sender": sender_email,
                "recipients": recipient_email,
                "status": "Linked",               
                "sent_or_received": "Sent",       
                "reference_doctype": "CRM Lead",  
                "reference_name": item.lead,
            })
            comm.insert(ignore_permissions=True)
            frappe.db.commit()

            responses.append({
                "lead": item.lead,
                "email": recipient_email,
                "status": "success",
                "message": "Email sent via SMTP and logged",
                "communication_id": comm.name if comm else None
            })

        except Exception as e:
            frappe.log_error(title="Email Sending Error", message=frappe.get_traceback())
            responses.append({
                "lead": item.lead,
                "email": recipient_email if 'recipient_email' in locals() else None,
                "status": "error",
                "message": str(e)
            })

    return responses


@frappe.whitelist()
def launch_campaign(campaign_name: str, segment_name: str, sender_email: str):
    """
    Public method to launch the campaign:
    Reads Campaign's schedule table and enqueues jobs for future sending
    """
    campaign = frappe.get_doc("Campaign", campaign_name)
    if not campaign.campaign_schedules:
        frappe.throw("No schedules defined for this campaign.")

    # You can add start_date to Campaign if you want
    start_date = getdate(now_datetime())

    for row in campaign.campaign_schedules:
        send_on = add_days(start_date, row.send_after_days)

        enqueue(
            method=send_scheduled_email,
            queue='long',
            job_name=f"{campaign_name}-{row.email_template}-{send_on}",
            timeout=600,
            campaign_name=campaign_name,
            segment_name=segment_name,
            email_template=row.email_template,
            sender_email=sender_email
        )

        frappe.logger().info(
            f"[Campaign Scheduler] Email from template {row.email_template} scheduled for {send_on}"
        )

    return f"Campaign {campaign_name} scheduled successfully!"


def send_scheduled_email(campaign_name, segment_name, email_template, sender_email):
    """
    Worker method — sends emails for the given schedule when the job runs.
    Uses Email Queue to properly queue and send emails.
    """
    segment = frappe.get_doc("Lead Segment", segment_name)
    
    for item in segment.leads:
        lead = frappe.get_doc("CRM Lead", item.lead)
        recipient_email = lead.email

        if not recipient_email:
            frappe.logger().warning(f"[Campaign] Lead {lead.name} has no email; skipped.")
            continue

        try:
            # Get email template and render with lead data
            email_content = get_email_template(email_template, lead.as_dict())
            subject = email_content.get('subject', 'No Subject')
            message = email_content.get('message', '')

            # Create Email Queue entry with proper settings
            email_queue = frappe.get_doc({
                "doctype": "Email Queue",
                "sender": sender_email,
                "reference_doctype": "Campaign",
                "reference_name": campaign_name,
                "message": message,
                "add_unsubscribe_link": 0,  # Disable unsubscribe to avoid errors
                "recipients": [{
                    "recipient": recipient_email
                }]
            })
            email_queue.insert(ignore_permissions=True)
            
            frappe.logger().info(
                f"[Campaign] Email '{subject}' queued for {recipient_email} (campaign: {campaign_name})"
            )

        except Exception as e:
            frappe.log_error(
                title=f"Campaign Email Error - {campaign_name}",
                message=f"Failed to queue email for {recipient_email}: {str(e)}\n{frappe.get_traceback()}"
            )
            continue

    # Commit all email queue entries
    frappe.db.commit()
    
    # Trigger email sending
    try:
        from frappe.email.queue import flush
        flush()
        frappe.db.commit()
        frappe.logger().info(f"[Campaign] Email queue flushed for campaign {campaign_name}")
    except Exception as e:
        frappe.log_error(
            title=f"Campaign Email Flush Error - {campaign_name}",
            message=f"Failed to flush email queue: {str(e)}\n{frappe.get_traceback()}"
        )