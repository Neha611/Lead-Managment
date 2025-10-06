import frappe
from frappe.utils import add_days, getdate, now_datetime, datetime
from frappe.utils.background_jobs import enqueue
from frappe.email.doctype.email_template.email_template import get_email_template
from frappe.utils import cint
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
def send_email_to_segment(segment_name, subject, message, sender_email, send_now=False, send_after_datetime=None):
    """
    Send an email to all leads in a segment using Frappe's email system.
    
    :param segment_name: Name of the Lead Segment
    :param subject: Email subject
    :param message: Email body (HTML/text)
    :param sender_email: Email address of sender
    :param send_now: If True, send immediately. If False, queue the email
    :return: List of responses
    """
    # Get default outgoing email account if sender not specified
    if not sender_email:
        sender_email = frappe.db.get_value(
            "Email Account",
            {"default_outgoing": 1},
            "email_id"
        )

    if not sender_email:
        frappe.throw("No sender email specified and no default outgoing account configured")

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

            if send_now:
                # Send immediately using frappe.sendmail
                frappe.sendmail(
                    recipients=[recipient_email],
                    sender=sender_email,
                    subject=subject,
                    message=message,
                    delayed=False,
                    reference_doctype="CRM Lead",
                    reference_name=lead_doc.name
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
                    "email_status": "Sent"
                }).insert(ignore_permissions=True)
                
                status = "sent"
                msg = "Email sent immediately"
                
            else:
                # Create email queue entry
                email_queue = frappe.get_doc({
                    "doctype": "Email Queue",
                    "priority": 1,
                    "status": "Not Sent",
                    "reference_doctype": "CRM Lead",
                    "reference_name": lead_doc.name,
                    "message": message,
                    "sender": sender_email,
                    "send_after": send_after_datetime, 
                    "recipients": [{
                        "recipient": recipient_email
                    }],
                    "subject": subject
                }).insert(ignore_permissions=True)

                # Log email in CRM
                comm = frappe.get_doc({
                    "doctype": "Communication",
                    "communication_type": "Communication",
                    "communication_medium": "Email",
                    "subject": subject,
                    "content": message,
                    "sender": sender_email,
                    "recipients": recipient_email,
                    "status": "Queued",
                    "sent_or_received": "Sent",
                    "reference_doctype": "CRM Lead",
                    "reference_name": item.lead,
                    "email_queue": email_queue.name,
                    "email_status": "Queued"
                }).insert(ignore_permissions=True)
                
                status = "queued"
                msg = "Email added to queue"
                
            frappe.db.commit()

            responses.append({
                "lead": item.lead,
                "email": recipient_email,
                "status": status,
                "message": msg,
                "communication_id": comm.name if comm else None,
                "email_queue_id": email_queue.name if not send_now else None
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
    Reads Campaign's schedule table and enqueues jobs for future sending,
    using both send_after_days and send_after_minutes.
    """
    campaign = frappe.get_doc("Campaign", campaign_name)
    
    if not campaign.campaign_schedules:
        frappe.throw("No schedules defined for this campaign.")

    # Calculate the current date for the base schedule date
    start_date = getdate(now_datetime())

    for row in campaign.campaign_schedules:
        
        # 💡 FIX 1: Convert string values from the DocType row to integers using cint()
        minutes_delay = cint(row.get("send_after_minutes"))
        days_delay = cint(row.get("send_after_days"))
        
        # Calculate the total delay combining days and minutes
        total_delay = datetime.timedelta(
            days=days_delay, 
            minutes=minutes_delay
        )
        
        # Calculate the exact time the job should run
        job_start_time = now_datetime() + total_delay
        
        # Calculate the 'send_on' date for logging/job name
        send_on = getdate(job_start_time)

        enqueue(
            method="crm_override.crm_override.broadcast_utils.send_scheduled_email", # 💡 FIX 2: Use the full method path!
            queue='long',
            # Job name includes the precise execution time
            job_name=f"{campaign_name}-{row.email_template}-{job_start_time.strftime('%Y-%m-%d-%H-%M')}", 
            timeout=600,
            
            # Use the calculated time for the precise delay
            start_after=job_start_time, 
            
            # Parameters passed to send_scheduled_email:
            campaign_name=campaign_name,
            segment_name=segment_name,
            email_template=row.email_template,
            sender_email=sender_email
        )

        frappe.logger().info(
            f"[Campaign Scheduler] Email from template {row.email_template} scheduled for {job_start_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    segment = frappe.get_doc("Lead Segment", segment_name)
    return f"Campaign '{campaign_name}' scheduled for segment '{segment.segmentname}' (ID: {segment.name})"


# 💡 NEW REQUIRED FUNCTION: This is the function the enqueued job calls
def send_scheduled_email(campaign_name, segment_name, email_template, sender_email):
    """
    This function is executed by the background worker after the
    scheduled delay has passed. It calls the existing send_email_to_segment function.
    """
    
    try:
        # 1. Fetch template content
        template = get_email_template(email_template)
        
        # 2. Call the main sending logic immediately (send_now=True)
        send_email_to_segment(
            segment_name=segment_name, 
            subject=template.subject, 
            message=template.response, 
            sender_email=sender_email, 
            send_now=True 
        )
        
    except Exception:
        # Log the failure for debugging
        frappe.log_error(
            title=f"Campaign Send Failure: {campaign_name}", 
            message=frappe.get_traceback()
        )

@frappe.whitelist()
def get_scheduled_emails(lead_email=None):
    """
    Get all scheduled emails for a lead or all leads
    :param lead_email: Optional - specific lead's email to check
    :return: List of scheduled emails with details
    """
    filters = {"status": "Not Sent"}
    if lead_email:
        # Get queue entries for specific email
        recipient_queues = frappe.get_all(
            "Email Queue Recipient",
            filters={"recipient": lead_email},
            fields=["parent"]
        )
        if recipient_queues:
            filters["name"] = ["in", [q.parent for q in recipient_queues]]
    
    email_queue = frappe.get_all(
        "Email Queue",
        filters=filters,
        # 💡 FIX: ADD 'subject' TO THE FIELDS LIST
        fields=["name", "creation", "send_after", "message", "reference_doctype", "reference_name", "subject"]
    )
    
    results = []
    for email in email_queue:
        recipients = frappe.get_all(
            "Email Queue Recipient",
            filters={"parent": email.name},
            fields=["recipient"]
        )
        
        results.append({
            "queue_id": email.name,
            "subject": email.subject, # This line now works!
            "created_on": email.creation,
            "scheduled_time": email.send_after,
            "recipients": [r.recipient for r in recipients],
            "preview": email.message[:200] + "..." if len(email.message) > 200 else email.message
        })
    
    return results