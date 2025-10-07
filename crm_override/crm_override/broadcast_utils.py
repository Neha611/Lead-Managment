import frappe
from frappe.utils import add_days, getdate, now_datetime, get_datetime, datetime
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
    :param send_after_datetime: DateTime to send the email (string or datetime object)
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

    # Convert send_after_datetime to datetime object if it's a string
    if send_after_datetime and isinstance(send_after_datetime, str):
        send_after_datetime = get_datetime(send_after_datetime)

    for item in lead_items:
        recipient_email = None
        comm = None
        email_queue = None
        
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
                # Create email queue entry with scheduled time
                email_queue = frappe.get_doc({
                    "doctype": "Email Queue",
                    "priority": 1,
                    "status": "Not Sent",
                    "reference_doctype": "CRM Lead",
                    "reference_name": lead_doc.name,
                    "message": message,
                    "sender": sender_email,
                    "send_after": send_after_datetime if send_after_datetime else now_datetime(),
                    "recipients": [{
                        "recipient": recipient_email
                    }],
                    "subject": subject
                }).insert(ignore_permissions=True)

                # Log email in CRM as Queued
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
                    "email_queue": email_queue.name,
                    "email_status": "Open"
                }).insert(ignore_permissions=True)
                
                status = "not sent"
                msg = f"Email scheduled for {send_after_datetime}" if send_after_datetime else "Email added to queue"
                
            frappe.db.commit()

            responses.append({
                "lead": item.lead,
                "email": recipient_email,
                "status": status,
                "message": msg,
                "communication_id": comm.name if comm else None,
                "email_queue_id": email_queue.name if email_queue else None,
                "scheduled_time": str(send_after_datetime) if send_after_datetime else None
            })

        except Exception as e:
            frappe.log_error(title="Email Sending Error", message=frappe.get_traceback())
            responses.append({
                "lead": item.lead,
                "email": recipient_email if recipient_email else None,
                "status": "error",
                "message": str(e)
            })

    return {
        "segment_id": segment.name,
        "segment_name": segment.segmentname,
        "results": responses
    }


@frappe.whitelist()
def launch_campaign(campaign_name: str, segment_name: str, sender_email: str, start_datetime=None):
    """
    Launch the campaign by reading Campaign's schedule table and scheduling emails.
    Supports both send_after_days and send_after_minutes for flexible scheduling.
    Uses Email Queue's send_after field directly.
    
    :param campaign_name: Name of the Campaign DocType
    :param segment_name: Name of the Lead Segment
    :param sender_email: Email address of the sender
    :param start_datetime: Base datetime to calculate schedules from (defaults to now)
    :return: Success message with campaign details
    """
    campaign = frappe.get_doc("Campaign", campaign_name)
    
    if not campaign.campaign_schedules:
        frappe.throw("No schedules defined for this campaign.")

    segment = frappe.get_doc("Lead Segment", segment_name)
    scheduled_count = 0
    total_emails = 0
    
    # Use provided start_datetime or current time as base
    if start_datetime:
        if isinstance(start_datetime, str):
            base_time = get_datetime(start_datetime)
        else:
            base_time = start_datetime
    else:
        base_time = now_datetime()
    
    schedule_details = []
    
    for row in campaign.campaign_schedules:
        try:
            # Convert string values to integers
            minutes_delay = cint(row.get("send_after_minutes", 0))
            days_delay = cint(row.get("send_after_days", 0))
            
            # Calculate the total delay combining days and minutes
            total_delay = datetime.timedelta(
                days=days_delay, 
                minutes=minutes_delay
            )
            
            # Calculate the exact time the emails should be sent
            send_time = base_time + total_delay
            
            # Get template content
            template_doc = frappe.get_doc("Email Template", row.email_template)
            
            # Use send_email_to_segment with send_after_datetime
            result = send_email_to_segment(
                segment_name=segment_name,
                subject=template_doc.subject,
                message=template_doc.response,
                sender_email=sender_email,
                send_now=False,  # Don't send immediately
                send_after_datetime=send_time  # Schedule for calculated time
            )
            
            # Count successful schedules
            scheduled_in_batch = 0
            for r in result.get('results', []):
                total_emails += 1
                if r['status'] in ['not sent', 'sent']:
                    scheduled_count += 1
                    scheduled_in_batch += 1
            
            schedule_details.append({
                "template": row.email_template,
                "send_time": send_time.strftime('%Y-%m-%d %H:%M:%S'),
                "delay": f"{days_delay}d {minutes_delay}m",
                "emails_scheduled": scheduled_in_batch
            })
            
            frappe.logger().info(
                f"[Campaign Scheduler] Email template '{row.email_template}' scheduled for "
                f"{send_time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"(+{days_delay}d {minutes_delay}m from {base_time.strftime('%Y-%m-%d %H:%M:%S')})"
            )
            
        except Exception as e:
            frappe.log_error(
                title=f"Campaign Schedule Error: {campaign_name}",
                message=f"Failed to schedule email template '{row.email_template}': {str(e)}\n{frappe.get_traceback()}"
            )
            continue

    if scheduled_count == 0:
        frappe.throw("Failed to schedule any emails. Check error logs for details.")
    
    return {
        "message": f"Campaign '{campaign_name}' scheduled successfully",
        "segment_name": segment.segmentname,
        "segment_id": segment.name,
        "emails_scheduled": scheduled_count,
        "total_emails": total_emails,
        "total_schedules": len(campaign.campaign_schedules),
        "base_time": base_time.strftime('%Y-%m-%d %H:%M:%S'),
        "schedule_details": schedule_details
    }


@frappe.whitelist()
def get_scheduled_emails(lead_email=None, campaign_name=None):
    """
    Get all scheduled emails for a lead or all leads.

    :param lead_email: Optional - specific lead's email to check
    :param campaign_name: Optional - filter by campaign
    :return: List of scheduled emails with details
    """
    filters = {"status": "Not Sent"}

    # If a specific lead email is provided, get queue entries linked to it
    if lead_email:
        recipient_queues = frappe.get_all(
            "Email Queue Recipient",
            filters={"recipient": lead_email},
            fields=["parent"]
        )
        if recipient_queues:
            filters["name"] = ["in", [q.parent for q in recipient_queues]]

    # ✅ Always define email_queue — not just when lead_email is given
    email_queue = frappe.get_all(
        "Email Queue",
        filters=filters,
        fields=[
            "name",
            "creation",
            "send_after",
            "sender",
            "message",
            "status",
            "error",
            "message_id",
            "reference_doctype",
            "reference_name",
            "communication",
            "priority",
            "email_account"
        ],
        order_by="send_after asc"
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
            "sender": email.sender,
            "status": email.status,
            "error": email.error,
            "message_id": email.message_id,
            "created_on": email.creation,
            "scheduled_time": email.send_after,
            "reference_doctype": email.reference_doctype,
            "reference_name": email.reference_name,
            "communication": email.communication,
            "priority": email.priority,
            "email_account": email.email_account,
            "recipients": [r.recipient for r in recipients],
            "preview": (email.message[:200] + "...") if email.message and len(email.message) > 200 else email.message
        })

    return results


@frappe.whitelist()
def cancel_scheduled_emails(queue_ids=None, lead_email=None):
    """
    Cancel scheduled emails by queue IDs or for a specific lead.
    
    :param queue_ids: List of Email Queue IDs to cancel (JSON string or list)
    :param lead_email: Email of lead whose scheduled emails should be cancelled
    :return: Number of cancelled emails
    """
    import json
    
    # Handle JSON string input for queue_ids
    if queue_ids and isinstance(queue_ids, str):
        try:
            queue_ids = json.loads(queue_ids)
        except:
            pass
    
    if not queue_ids and not lead_email:
        frappe.throw("Please provide either queue_ids or lead_email")
    
    filters = {"status": "Not Sent"}
    
    if lead_email:
        recipient_queues = frappe.get_all(
            "Email Queue Recipient",
            filters={"recipient": lead_email},
            fields=["parent"]
        )
        if recipient_queues:
            filters["name"] = ["in", [q.parent for q in recipient_queues]]
    elif queue_ids:
        filters["name"] = ["in", queue_ids]
    
    email_queues = frappe.get_all("Email Queue", filters=filters, pluck="name")
    
    cancelled_count = 0
    for queue_id in email_queues:
        try:
            queue_doc = frappe.get_doc("Email Queue", queue_id)
            queue_doc.status = "Cancelled"
            queue_doc.save(ignore_permissions=True)
            cancelled_count += 1
        except Exception as e:
            frappe.log_error(
                title=f"Failed to cancel Email Queue: {queue_id}",
                message=str(e)
            )
    
    frappe.db.commit()
    
    return {
        "message": f"Cancelled {cancelled_count} scheduled email(s)",
        "cancelled_count": cancelled_count
    }