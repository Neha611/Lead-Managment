import frappe
from frappe.utils import add_days, getdate, now_datetime, datetime
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

@frappe.whitelist()
def list_lead_segments():
    """
    Return all lead segments with both ID and human-readable name.
    """
    return frappe.get_all(
        "Lead Segment",
        fields=["name", "segmentname", "creation"],
        order_by="creation desc"
    )


@frappe.whitelist()
def get_segment_leads(segment_name):
    """
    Get all leads in a segment with their details.
    :param segment_name: Name (ID) of the Lead Segment
    :return: List of lead details
    """
    segment = frappe.get_doc("Lead Segment", segment_name)
    leads = []

    for item in segment.leads:
        try:
            lead = frappe.get_doc("CRM Lead", item.lead)
            leads.append({
                "name": lead.name,
                "lead_name": lead.lead_name or lead.name,
                "email": lead.email,
                "mobile_no": lead.mobile_no,
                "status": lead.status,
                "organization": lead.organization,
                "image": lead.image,
            })
        except Exception as e:
            frappe.log_error(
                title=f"Error fetching lead {item.lead}",
                message=frappe.get_traceback()
            )
            continue

    return leads


@frappe.whitelist()
def add_lead_to_segment(segment_name, lead_name):
    """
    Add a lead to a segment.
    :param segment_name: Name (ID) of the Lead Segment
    :param lead_name: Name (ID) of the CRM Lead to add
    :return: Success status
    """
    segment = frappe.get_doc("Lead Segment", segment_name)

    # Check if lead already exists in segment
    for item in segment.leads:
        if item.lead == lead_name:
            frappe.throw(f"Lead {lead_name} is already in this segment")

    # Add the lead
    segment.append("leads", {"lead": lead_name})
    segment.save(ignore_permissions=True)
    frappe.db.commit()

    return {"success": True, "message": "Lead added to segment"}


@frappe.whitelist()
def remove_lead_from_segment(segment_name, lead_name):
    """
    Remove a lead from a segment.
    :param segment_name: Name (ID) of the Lead Segment
    :param lead_name: Name (ID) of the CRM Lead to remove
    :return: Success status
    """
    segment = frappe.get_doc("Lead Segment", segment_name)

    # Find and remove the lead
    lead_found = False
    for item in segment.leads:
        if item.lead == lead_name:
            segment.remove(item)
            lead_found = True
            break

    if not lead_found:
        frappe.throw(f"Lead {lead_name} not found in this segment")

    segment.save(ignore_permissions=True)
    frappe.db.commit()

    return {"success": True, "message": "Lead removed from segment"}


@frappe.whitelist()
def send_email_to_segment(segment_name, subject, message):
    """
    Send an email to all leads in a segment using Frappe's sendmail (via SMTP)
    and log the communication in CRM.

    :param segment_name: Name of the Lead Segment
    :param subject: Email subject
    :param message: Email body (HTML/text)
    :return: List of responses
    """
    # Get default outgoing email account
    sender_email = frappe.db.get_value(
        "Email Account",
        {"default_outgoing": 1},
        "email_id"
    )

    if not sender_email:
        frappe.throw("No default outgoing email account configured")

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

    return {
        "segment_id": segment.name,
        "segment_name": segment.segmentname,
        "results": responses
    }


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
            start_after=now_datetime() + datetime.timedelta(days=row.send_after_days),
            campaign_name=campaign_name,
            segment_name=segment_name,
            email_template=row.email_template,
            sender_email=sender_email
        )

        frappe.logger().info(
            f"[Campaign Scheduler] Email from template {row.email_template} scheduled for {send_on}"
        )

    segment = frappe.get_doc("Lead Segment", segment_name)
    return f"Campaign '{campaign_name}' scheduled for segment '{segment.segmentname}' (ID: {segment.name})"


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
                "add_unsubscribe_link": 0, 
                "recipients": [{
                    "recipient": recipient_email
                }]
            })
            email_queue.insert(ignore_permissions=True)
            
            frappe.logger().info(
                f"[Campaign] Email '{subject}' queued for {recipient_email} "
                f"(campaign: {campaign_name}, segment: {segment.segmentname})"
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