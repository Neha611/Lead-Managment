import frappe
from frappe.utils import add_days, getdate, now_datetime, get_datetime, datetime
from frappe.utils.background_jobs import enqueue
from frappe.email.doctype.email_template.email_template import get_email_template
from frappe.utils import cint
from frappe.email.email_body import get_email
from frappe.email.doctype.email_account.email_account import EmailAccount
from frappe.utils import validate_email_address
from crm_override.crm_override.email_tracker import update_tracker_on_email_send, update_tracker_on_email_error

def create_lead_segment(segmentname, lead_names, description=None):
    """
    Create a Lead Segment with selected leads.
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


# In broadcast_utils.py, update inject_tracking_pixel:

def inject_tracking_pixel(message, email_queue_name):
    """Safely inject tracking pixel at the end of the HTML body."""
    if not message:
        return message

    # ✅ Use Cloudflare Tunnel URL (no warning page!)
    base_url = "https://carbon-containing-plymouth-having.trycloudflare.com"
    
    tracking_url = f"{base_url}/api/method/crm_override.crm_override.email_tracker.email_tracker?name={email_queue_name}"
    
    pixel = f'<img src="{tracking_url}" width="1" height="1" style="display:none;" alt=""/>'

    message = str(message)
    lower = message.lower()

    if "</body>" in lower:
        idx = lower.rfind("</body>")
        return message[:idx] + pixel + message[idx:]
    elif "</html>" in lower:
        idx = lower.rfind("</html>")
        return message[:idx] + pixel + message[idx:]
    else:
        return f"<html><body>{message}{pixel}</body></html>"

def create_lead_email_tracker(lead_name, email_queue_name, initial_status="Queued"):
    """
    Create Lead Email Tracker entry when email is queued/sent
    """
    try:
        existing = frappe.db.exists(
            "Lead Email Tracker",
            {"email_queue_status": email_queue_name}
        )
        
        if existing:
            frappe.logger().info(f"Tracker already exists for {email_queue_name}")
            return frappe.get_doc("Lead Email Tracker", existing)
        
        lead_doc = frappe.get_doc("CRM Lead", lead_name)
        lead_email = getattr(lead_doc, "email", None)
        lead_full_name = getattr(lead_doc, "lead_name", None) or getattr(lead_doc, "name", None)
        
        tracker = frappe.get_doc({
            "doctype": "Lead Email Tracker",
            "lead": lead_name,
            "lead_name": lead_full_name,
            "email": lead_email,
            "email_queue_status": email_queue_name,
            "status": initial_status,
            "last_sent_on": now_datetime() if initial_status == "Sent" else None,
            "resend_count": 0
        })
        tracker.insert(ignore_permissions=True)
        frappe.db.commit()
        frappe.logger().info(f"Created tracker for {lead_name} with status: {initial_status}")
        return tracker
    except Exception as e:
        frappe.log_error(
            title=f"Failed to create Lead Email Tracker for {lead_name}",
            message=f"Email Queue: {email_queue_name}\nError: {str(e)}\n{frappe.get_traceback()}"
        )
        return None


def update_tracker_status(email_queue_name, status, error_message=None):
    """
    Helper function to update tracker status
    """
    try:
        trackers = frappe.get_all(
            "Lead Email Tracker",
            filters={"email_queue_status": email_queue_name},
            fields=["name"]
        )
        
        for tr in trackers:
            update_dict = {
                "status": status,
                "modified": now_datetime()
            }
            
            if status == "Sent":
                update_dict["last_sent_on"] = now_datetime()
            
            if error_message:
                update_dict["error_message"] = error_message
            
            frappe.db.set_value("Lead Email Tracker", tr["name"], update_dict)
        
        frappe.db.commit()
        frappe.logger().info(f"Updated tracker status to {status} for {email_queue_name}")
        
    except Exception as e:
        frappe.log_error(
            title="Tracker Status Update Error",
            message=f"Email Queue: {email_queue_name}\nStatus: {status}\nError: {str(e)}\n{frappe.get_traceback()}"
        )


@frappe.whitelist()
def send_email_to_segment(segment_name, subject, message, sender_email=None, send_now=False, send_after_datetime=None):
    """
    Send an email to all leads in a segment using Frappe's email system with tracking.
    Supports any sender (Admin, Sales, etc.), dynamic templates, and email tracking.
    """
    # --- STEP 1: Resolve a valid sender email ---
    if not sender_email or "@" not in sender_email:
        user_email = frappe.db.get_value("User", sender_email, "email")
        if user_email:
            sender_email = user_email

    if not sender_email or "@" not in sender_email:
        sender_email = frappe.db.get_value("Email Account", {"default_outgoing": 1}, "email_id")

    if sender_email and "@" in sender_email:
        try:
            email_account = EmailAccount.find_outgoing(match_by_email=sender_email)
            if email_account:
                sender_email = email_account.email_id
        except Exception:
            sender_email = frappe.db.get_value("Email Account", {"default_outgoing": 1}, "email_id")

    try:
        validate_email_address(sender_email, throw=True)
    except Exception:
        frappe.throw(f"Invalid sender email resolved: {sender_email}")

    frappe.logger().info(f"[Email Debug] Final sender email: {sender_email}")

    # --- STEP 2: Fetch leads in the segment ---
    segment = frappe.get_doc("Lead Segment", segment_name)
    lead_items = segment.leads
    if not lead_items:
        return {"segment_id": segment.name, "segment_name": segment.segmentname, "results": []}

    if send_after_datetime and isinstance(send_after_datetime, str):
        send_after_datetime = get_datetime(send_after_datetime)

    responses = []

    # --- STEP 3: Iterate over leads and send emails ---
    for item in lead_items:
        try:
            lead_doc = frappe.get_doc("CRM Lead", item.lead)
            recipient_email = getattr(lead_doc, "email", None)
            if not recipient_email:
                responses.append({
                    "lead": item.lead,
                    "status": "skipped",
                    "message": "Lead has no email"
                })
                continue

            # Prepare context for template rendering
            template_context = {
                "lead_name": getattr(lead_doc, "lead_name", "") or getattr(lead_doc, "name", ""),
                "company_name": getattr(lead_doc, "company_name", ""),
                "email": recipient_email,
                "mobile_no": getattr(lead_doc, "mobile_no", ""),
                "sender_signature": frappe.get_value("User", frappe.session.user, "full_name") or sender_email,
            }

            rendered_subject = frappe.render_template(subject, template_context)
            rendered_message = frappe.render_template(message, template_context)

            # Build MIME email (text + HTML)
            mime_message = get_email(
                subject=rendered_subject,
                content=rendered_message,
                recipients=[recipient_email],
                sender=sender_email
            )

            # --- FIXED: Always provide unsubscribe_method ---
            default_unsubscribe_method = "/api/method/frappe.email.queue.unsubscribe"

            # Create Email Queue entry
            email_queue = frappe.get_doc({
                "doctype": "Email Queue",
                "priority": 1,
                "status": "Not Sent",
                "reference_doctype": "CRM Lead",
                "reference_name": lead_doc.name,
                "message": mime_message.as_string(),
                "sender": sender_email,
                "send_after": send_after_datetime if not send_now else now_datetime(),
                "recipients": [{"recipient": recipient_email}],
                "subject": rendered_subject,
                "send_html_email": 1,
                "content_type": "multipart/alternative",
                "unsubscribe_method": default_unsubscribe_method,  # ✅ FIXED
            }).insert(ignore_permissions=True)
            frappe.db.commit()

            # Create tracker for this email
            tracker = create_lead_email_tracker(lead_doc.name, email_queue.name, initial_status="Queued")
            message_with_pixel = inject_tracking_pixel(rendered_message, email_queue.name)

            # Update Email Queue with pixelized message (still valid HTML)
            mime_message = get_email(
                subject=rendered_subject,
                content=message_with_pixel,
                recipients=[recipient_email],
                sender=sender_email
            )
            email_queue.message = mime_message.as_string()
            email_queue.save(ignore_permissions=True)
            frappe.db.commit()

            status = "scheduled"
            msg = "Email queued successfully with tracking"

            # Send immediately if requested
            if send_now:
                try:
                    email_queue.reload()
                    email_queue.send()
                    status = "sent"
                    msg = "Email sent immediately with tracking"
                except Exception as send_error:
                    status = "error"
                    msg = f"Failed to send email: {str(send_error)}"
                    frappe.log_error(
                        title="Email Send Failed",
                        message=f"Lead: {lead_doc.name}\nEmail: {recipient_email}\nError: {str(send_error)}"
                    )

            # Create Communication log
            comm = frappe.get_doc({
                "doctype": "Communication",
                "communication_type": "Communication",
                "communication_medium": "Email",
                "subject": rendered_subject,
                "content": message_with_pixel,
                "sender": sender_email,
                "recipients": recipient_email,
                "status": "Linked",
                "sent_or_received": "Sent",
                "reference_doctype": "CRM Lead",
                "reference_name": item.lead,
                "email_queue": email_queue.name,
                "email_status": "Open" if status in ["scheduled", "sent"] else "Error"
            }).insert(ignore_permissions=True)
            frappe.db.commit()

            responses.append({
                "lead": item.lead,
                "email": recipient_email,
                "status": status,
                "message": msg,
                "communication_id": comm.name,
                "email_queue_id": email_queue.name,
                "tracker_id": tracker.name,
                "scheduled_time": str(send_after_datetime) if send_after_datetime else str(now_datetime())
            })

        except Exception as e:
            frappe.log_error(title="Email Sending Error", message=frappe.get_traceback())
            responses.append({
                "lead": item.lead,
                "email": getattr(lead_doc, "email", None),
                "status": "error",
                "message": str(e)
            })

    return {"segment_id": segment.name, "segment_name": segment.segmentname, "results": responses}

@frappe.whitelist()
def launch_campaign(campaign_name: str, recipient_type: str, recipient_id: str, sender_email: str, start_datetime=None):
    """
    Launch the campaign for either a segment or individual lead.
    
    Args:
        campaign_name: Name of the Campaign
        recipient_type: Either 'Lead Segment' or 'CRM Lead'
        recipient_id: ID of the segment or lead
        sender_email: Sender's email address
        start_datetime: Optional start time for the campaign
    """
    frappe.logger().info(f"[Launch Campaign] Starting: {campaign_name} for {recipient_type}: {recipient_id}")
    
    campaign = frappe.get_doc("Campaign", campaign_name)
    
    if not campaign.campaign_schedules:
        frappe.throw("No schedules defined for this campaign.")

    leads_to_process = []
    
    # Handle both segment and individual lead cases
    if recipient_type == "Lead Segment":
        segment = frappe.get_doc("Lead Segment", recipient_id)
        if not segment.leads or len(segment.leads) == 0:
            frappe.throw(f"Lead Segment '{segment.segmentname}' has no leads.")
        leads_to_process = [{"lead": item.lead} for item in segment.leads]
        segment_name = segment.name
        
    elif recipient_type == "CRM Lead":
        lead = frappe.get_doc("CRM Lead", recipient_id)
        leads_to_process = [{"lead": lead.name}]
        # Create a temporary segment for the individual lead
        segment_name = f"TEMP-{lead.name}-{frappe.utils.random_string(8)}"
        temp_segment = frappe.get_doc({
            "doctype": "Lead Segment",
            "segmentname": segment_name,
            "description": f"Temporary segment for lead {lead.name}",
            "leads": [{"lead": lead.name}]
        }).insert(ignore_permissions=True)
        segment_name = temp_segment.name
    else:
        frappe.throw(f"Invalid recipient type: {recipient_type}")
    
    frappe.logger().info(f"[Launch Campaign] Processing {len(leads_to_process)} leads")
    
    if start_datetime:
        if isinstance(start_datetime, str):
            base_time = get_datetime(start_datetime)
        else:
            base_time = start_datetime
    else:
        base_time = now_datetime()
    
    scheduled_count = 0
    total_emails = 0
    errors = []
    schedule_details = []
    
    for row in campaign.campaign_schedules:
        try:
            minutes_delay = cint(row.get("send_after_minutes", 0))
            days_delay = cint(row.get("send_after_days", 0))
            
            total_delay = datetime.timedelta(
                days=days_delay, 
                minutes=minutes_delay
            )
            
            send_time = base_time + total_delay
            
            template_doc = frappe.get_doc("Email Template", row.email_template)
            
            result = send_email_to_segment(
                segment_name=segment_name,
                subject=template_doc.subject,
                message=template_doc.response or template_doc.message or "",
                sender_email=sender_email,
                send_now=False,
                send_after_datetime=send_time
            )
            
            scheduled_in_batch = 0
            errors_in_batch = []
            
            for r in result.get('results', []):
                total_emails += 1
                if r['status'] in ['scheduled', 'not sent', 'queued']:
                    scheduled_count += 1
                    scheduled_in_batch += 1
                elif r['status'] == 'error':
                    errors_in_batch.append(f"Lead {r.get('lead')}: {r.get('message')}")
            
            if errors_in_batch:
                errors.extend(errors_in_batch)
            
            schedule_details.append({
                "template": row.email_template,
                "send_time": send_time.strftime('%Y-%m-%d %H:%M:%S'),
                "delay": f"{days_delay}d {minutes_delay}m",
                "emails_scheduled": scheduled_in_batch,
                "errors": errors_in_batch
            })
            
        except Exception as e:
            error_msg = f"Template '{row.email_template}': {str(e)}"
            errors.append(error_msg)
            frappe.log_error(
                title=f"Campaign Schedule Error: {campaign_name}",
                message=f"Failed to schedule '{row.email_template}': {str(e)}\n{frappe.get_traceback()}"
            )
            continue

    # Clean up temporary segment if created for individual lead
    if recipient_type == "CRM Lead" and segment_name.startswith("TEMP-"):
        frappe.delete_doc("Lead Segment", segment_name, ignore_permissions=True)

    if scheduled_count == 0:
        error_detail = "\n".join(errors) if errors else "No specific errors logged"
        frappe.throw(f"Failed to schedule any emails. Errors:\n{error_detail}")
    
    return {
        "message": f"Campaign '{campaign_name}' scheduled successfully",
        "recipient_type": recipient_type,
        "recipient_id": recipient_id,
        "emails_scheduled": scheduled_count,
        "total_emails": total_emails,
        "total_schedules": len(campaign.campaign_schedules),
        "base_time": base_time.strftime('%Y-%m-%d %H:%M:%S'),
        "schedule_details": schedule_details,
        "errors": errors if errors else None
    }


@frappe.whitelist()
def get_scheduled_emails(lead_email=None, campaign_name=None):
    """
    Get all scheduled emails for a lead or all leads.
    """
    filters = {"status": "Not Sent"}

    if lead_email:
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
            "email_account": email.account,
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


# ============================================================
# CRUD Operations for Lead Segments
# ============================================================

@frappe.whitelist()
def get_segment_leads(segment_name):
    """
    Get all leads in a segment with their details.

    :param segment_name: Name of the Lead Segment
    :return: List of leads with details
    """
    segment = frappe.get_doc("Lead Segment", segment_name)
    leads_data = []

    for item in segment.leads:
        try:
            lead = frappe.get_doc("CRM Lead", item.lead)
            leads_data.append({
                "name": lead.name,
                "lead_name": lead.lead_name,
                "email": lead.email,
                "mobile_no": lead.mobile_no,
                "status": lead.status,
                "image": lead.image if hasattr(lead, 'image') else None
            })
        except Exception as e:
            frappe.log_error(
                title=f"Failed to fetch lead {item.lead}",
                message=str(e)
            )
            continue

    return leads_data


@frappe.whitelist()
def add_lead_to_segment(segment_name, lead_name):
    """
    Add a lead to a segment.

    :param segment_name: Name of the Lead Segment
    :param lead_name: Name of the Lead to add
    :return: Success message
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

    return {
        "message": f"Lead {lead_name} added to segment successfully"
    }


@frappe.whitelist()
def remove_lead_from_segment(segment_name, lead_name):
    """
    Remove a lead from a segment.

    :param segment_name: Name of the Lead Segment
    :param lead_name: Name of the Lead to remove
    :return: Success message
    """
    segment = frappe.get_doc("Lead Segment", segment_name)

    # Find and remove the lead
    for item in segment.leads:
        if item.lead == lead_name:
            segment.remove(item)
            break

    segment.save(ignore_permissions=True)
    frappe.db.commit()

    return {
        "message": f"Lead {lead_name} removed from segment successfully"
    }


@frappe.whitelist()
def get_lead_segment(name):
    """
    Get a specific Lead Segment with all details.

    :param name: Name of the Lead Segment
    :return: Lead Segment document
    """
    segment = frappe.get_doc("Lead Segment", name)
    return {
        "name": segment.name,
        "segmentname": segment.segmentname,
        "description": segment.description,
        "creation": segment.creation,
        "modified": segment.modified,
        "leads": [{"lead": item.lead} for item in segment.leads]
    }


@frappe.whitelist()
def update_lead_segment(name, segmentname=None, description=None, leads=None):
    """
    Update a Lead Segment.

    :param name: Name of the Lead Segment to update
    :param segmentname: New segment name (optional)
    :param description: New description (optional)
    :param leads: New list of lead names (optional)
    :return: Updated Lead Segment
    """
    import json

    segment = frappe.get_doc("Lead Segment", name)

    if segmentname:
        segment.segmentname = segmentname

    if description is not None:
        segment.description = description

    if leads is not None:
        # Handle JSON string input
        if isinstance(leads, str):
            leads = json.loads(leads)

        # Clear existing leads and add new ones
        segment.leads = []
        for lead in leads:
            segment.append("leads", {"lead": lead})

    segment.save(ignore_permissions=True)
    frappe.db.commit()

    return {
        "name": segment.name,
        "segmentname": segment.segmentname,
        "description": segment.description,
        "leads": [{"lead": item.lead} for item in segment.leads]
    }


@frappe.whitelist()
def delete_lead_segment(name):
    """
    Delete a Lead Segment.

    :param name: Name of the Lead Segment to delete
    :return: Success message
    """
    frappe.delete_doc("Lead Segment", name, ignore_permissions=True)
    frappe.db.commit()

    return {
        "message": f"Lead Segment '{name}' deleted successfully"
    }


# ============================================================
# CRUD Operations for Campaigns
# ============================================================

@frappe.whitelist()
def create_campaign(campaign_name, description=None):
    """
    Create a new Campaign.

    :param campaign_name: Name of the Campaign
    :param description: Optional description
    :return: Campaign document
    """
    campaign = frappe.get_doc({
        "doctype": "Campaign",
        "campaign_name": campaign_name,
        "description": description or ""
    })
    campaign.insert(ignore_permissions=True)
    frappe.db.commit()

    return {
        "name": campaign.name,
        "campaign_name": campaign.campaign_name,
        "description": campaign.description
    }


@frappe.whitelist()
def list_campaigns():
    """
    Return all campaigns with details.
    """
    return frappe.get_all(
        "Campaign",
        fields=["name", "campaign_name", "description", "creation", "modified"],
        order_by="creation desc"
    )


@frappe.whitelist()
def get_campaign(name):
    """
    Get a specific Campaign with all details including schedules.

    :param name: Name of the Campaign
    :return: Campaign document
    """
    campaign = frappe.get_doc("Campaign", name)

    schedules = []
    for schedule in campaign.campaign_schedules:
        schedules.append({
            "email_template": schedule.email_template,
            "send_after_days": schedule.send_after_days,
            "send_after_minutes": schedule.send_after_minutes,
            "idx": schedule.idx
        })

    return {
        "name": campaign.name,
        "campaign_name": campaign.campaign_name,
        "description": campaign.description,
        "creation": campaign.creation,
        "modified": campaign.modified,
        "campaign_schedules": schedules
    }


@frappe.whitelist()
def update_campaign(name, campaign_name=None, description=None):
    """
    Update a Campaign.

    :param name: Name of the Campaign to update
    :param campaign_name: New campaign name (optional)
    :param description: New description (optional)
    :return: Updated Campaign
    """
    campaign = frappe.get_doc("Campaign", name)

    if campaign_name:
        campaign.campaign_name = campaign_name

    if description is not None:
        campaign.description = description

    campaign.save(ignore_permissions=True)
    frappe.db.commit()

    return {
        "name": campaign.name,
        "campaign_name": campaign.campaign_name,
        "description": campaign.description
    }


@frappe.whitelist()
def delete_campaign(name):
    """
    Delete a Campaign.

    :param name: Name of the Campaign to delete
    :return: Success message
    """
    frappe.delete_doc("Campaign", name, ignore_permissions=True)
    frappe.db.commit()

    return {
        "message": f"Campaign '{name}' deleted successfully"
    }


# ============================================================
# CRUD Operations for Email Campaigns
# ============================================================

@frappe.whitelist()
def create_email_campaign(campaign_name, subject, message, sender_email=None):
    """
    Create a new Email Campaign.

    :param campaign_name: Name of the Email Campaign
    :param subject: Email subject
    :param message: Email body
    :param sender_email: Sender email (optional)
    :return: Email Campaign document
    """
    email_campaign = frappe.get_doc({
        "doctype": "Email Campaign",
        "campaign_name": campaign_name,
        "subject": subject,
        "message": message,
        "sender_email": sender_email or ""
    })
    email_campaign.insert(ignore_permissions=True)
    frappe.db.commit()

    return {
        "name": email_campaign.name,
        "campaign_name": email_campaign.campaign_name,
        "subject": email_campaign.subject,
        "message": email_campaign.message,
        "sender_email": email_campaign.sender_email
    }


@frappe.whitelist()
def list_email_campaigns():
    """
    Return all email campaigns with details.
    """
    return frappe.get_all(
        "Email Campaign",
        fields=["name", "campaign_name", "subject", "sender_email", "creation", "modified"],
        order_by="creation desc"
    )


@frappe.whitelist()
def get_email_campaign(name):
    """
    Get a specific Email Campaign with all details.

    :param name: Name of the Email Campaign
    :return: Email Campaign document
    """
    email_campaign = frappe.get_doc("Email Campaign", name)

    return {
        "name": email_campaign.name,
        "campaign_name": email_campaign.campaign_name,
        "subject": email_campaign.subject,
        "message": email_campaign.message,
        "sender_email": email_campaign.sender_email,
        "creation": email_campaign.creation,
        "modified": email_campaign.modified
    }


@frappe.whitelist()
def update_email_campaign(name, campaign_name=None, subject=None, message=None, sender_email=None):
    """
    Update an Email Campaign.

    :param name: Name of the Email Campaign to update
    :param campaign_name: New campaign name (optional)
    :param subject: New subject (optional)
    :param message: New message (optional)
    :param sender_email: New sender email (optional)
    :return: Updated Email Campaign
    """
    email_campaign = frappe.get_doc("Email Campaign", name)

    if campaign_name:
        email_campaign.campaign_name = campaign_name

    if subject:
        email_campaign.subject = subject

    if message:
        email_campaign.message = message

    if sender_email is not None:
        email_campaign.sender_email = sender_email

    email_campaign.save(ignore_permissions=True)
    frappe.db.commit()

    return {
        "name": email_campaign.name,
        "campaign_name": email_campaign.campaign_name,
        "subject": email_campaign.subject,
        "message": email_campaign.message,
        "sender_email": email_campaign.sender_email
    }


@frappe.whitelist()
def delete_email_campaign(name):
    """
    Delete an Email Campaign.

    :param name: Name of the Email Campaign to delete
    :return: Success message
    """
    frappe.delete_doc("Email Campaign", name, ignore_permissions=True)
    frappe.db.commit()

    return {
        "message": f"Email Campaign '{name}' deleted successfully"
    }