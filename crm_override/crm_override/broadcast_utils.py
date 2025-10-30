import frappe
from frappe.utils import add_days, getdate, now_datetime, get_datetime, datetime
from frappe.utils.background_jobs import enqueue
from frappe.email.doctype.email_template.email_template import get_email_template
from frappe.utils import cint
from frappe.email.email_body import get_email
from frappe.email.doctype.email_account.email_account import EmailAccount
from urllib.parse import quote
from frappe.utils import validate_email_address
from crm_override.crm_override.email_tracker import update_tracker_on_email_send, update_tracker_on_email_error
from crm_override.crm_override.email_threading.outbound_email_threading import (
    ensure_communication_has_thread_id,
    add_thread_id_to_outbound_email
)

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

def create_lead_email_tracker(lead_name, email_queue_name=None, communication_name=None, initial_status="Queued"):
    """
    Create Lead Email Tracker entry when email is queued/sent.
    Supports both campaign emails (email_queue_name) and manual emails (communication_name).
    Ensures tracker <-> Communication link is stored.
    """
    try:
        # Log input parameters for debugging
        frappe.logger().info(
            f"[CREATE TRACKER] Lead: {lead_name} | "
            f"Email Queue: {email_queue_name} | "
            f"Communication: {communication_name} | "
            f"Status: {initial_status}"
        )

        filters = {"lead": lead_name}
        if email_queue_name:
            filters["email_queue_status"] = email_queue_name
        if communication_name:
            filters["communication"] = communication_name

        # Check for existing tracker
        existing = frappe.db.exists("Lead Email Tracker", filters)
        if existing:
            tracker = frappe.get_doc("Lead Email Tracker", existing)
            frappe.logger().info(
                f"Tracker already exists: {tracker.name} | "
                f"Communication field: {tracker.communication}"
            )
            return tracker

        # Get lead info
        lead_doc = frappe.get_doc("CRM Lead", lead_name)
        lead_email = getattr(lead_doc, "email", None)
        lead_full_name = getattr(lead_doc, "lead_name", None) or lead_doc.name

        # Prepare data
        tracker_data = {
            "doctype": "Lead Email Tracker",
            "lead": lead_name,
            "lead_name": lead_full_name,
            "email": lead_email,
            "status": initial_status,
            "last_sent_on": frappe.utils.now_datetime() if initial_status == "Sent" else None,
            "resend_count": 0,
            "email_queue_status": email_queue_name,
            "communication": communication_name,  # Make sure this is set
        }

        # Log the data being inserted
        frappe.logger().info(f"[CREATE TRACKER] Data to insert: {tracker_data}")

        tracker = frappe.get_doc(tracker_data)
        tracker.insert(ignore_permissions=True)
        frappe.db.commit()

        # Verify the communication field after insert
        tracker.reload()
        frappe.logger().info(
            f"[CREATE TRACKER SUCCESS] Created tracker: {tracker.name} | "
            f"Communication field after insert: {tracker.communication} | "
            f"Email Queue: {tracker.email_queue_status}"
        )
        
        return tracker

    except Exception as e:
        frappe.log_error(
            title=f"Failed to create Lead Email Tracker for {lead_name}",
            message=f"Email Queue: {email_queue_name}\n"
                   f"Communication: {communication_name}\n"
                   f"Error: {str(e)}\n"
                   f"{frappe.get_traceback()}"
        )
        return None

@frappe.whitelist()
def send_email_to_segment(segment_name=None, lead_name=None, subject=None, message=None, sender_email=None, send_now=False, send_after_datetime=None):
    """
    Send an email to leads with proper SendGrid tracking and status updates
    """
    # --- STEP 1: Validate input ---
    if not segment_name and not lead_name:
        frappe.throw("Either segment_name or lead_name must be provided.")

    # --- STEP 2: Resolve sender email ---
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

    # --- STEP 3: Determine recipients ---
    leads = []
    segment_id = None
    segment_label = None

    if segment_name:
        if not frappe.db.exists("Lead Segment", segment_name):
            frappe.throw(f"Lead Segment '{segment_name}' not found")
        segment = frappe.get_doc("Lead Segment", segment_name)
        if not segment.leads:
            return {"segment_id": segment.name, "segment_name": segment.segmentname, "results": []}
        leads = [item.lead for item in segment.leads]
        segment_id = segment.name
        segment_label = segment.segmentname
    elif lead_name:
        if not frappe.db.exists("CRM Lead", lead_name):
            frappe.throw(f"CRM Lead '{lead_name}' not found")
        leads = [lead_name]
        segment_label = f"Single Lead: {lead_name}"

    if send_after_datetime and isinstance(send_after_datetime, str):
        send_after_datetime = get_datetime(send_after_datetime)

    responses = []

    # --- STEP 4: Send emails ---
    for lead_id in leads:
        try:
            lead_doc = frappe.get_doc("CRM Lead", lead_id)
            recipient_email = getattr(lead_doc, "email", None)
            if not recipient_email:
                responses.append({
                    "lead": lead_id,
                    "status": "skipped",
                    "message": "Lead has no email"
                })
                continue

            # Template context
            ctx = {
                "lead_name": getattr(lead_doc, "lead_name", "") or lead_doc.name,
                "company_name": getattr(lead_doc, "company_name", ""),
                "email": recipient_email,
                "mobile_no": getattr(lead_doc, "mobile_no", ""),
                "sender_signature": frappe.get_value("User", frappe.session.user, "full_name") or sender_email,
            }

            rendered_subject = frappe.render_template(subject, ctx)
            rendered_message = frappe.render_template(message, ctx)

            # ✅ STEP 4.1: Create Email Queue FIRST (without message)
            email_queue = frappe.get_doc({
                "doctype": "Email Queue",
                "priority": 1,
                "status": "Not Sent",
                "reference_doctype": "CRM Lead",
                "reference_name": lead_doc.name,
                "message": "",  # Will be set after tracker creation
                "sender": sender_email,
                "send_after": send_after_datetime if not send_now else now_datetime(),
                "recipients": [{"recipient": recipient_email}],
                "subject": rendered_subject,
                "send_html_email": 1,
                "content_type": "multipart/alternative",
                "unsubscribe_method": "/api/method/frappe.email.queue.unsubscribe",
            }).insert(ignore_permissions=True)
            frappe.db.commit()

            frappe.logger().info(f"[Campaign] Created Email Queue: {email_queue.name}")
            add_thread_id_to_outbound_email(email_queue)

            # ✅ STEP 4.2: Create tracker IMMEDIATELY (BEFORE Communication)
            tracker = create_lead_email_tracker(
                lead_doc.name,
                email_queue_name=email_queue.name,
                communication_name=None,  # Communication doesn't exist yet
                initial_status="Queued"
            )
            
            if not tracker:
                frappe.logger().error(f"[Campaign] Failed to create tracker for Email Queue: {email_queue.name}")
            else:
                frappe.logger().info(f"[Campaign] Tracker created: {tracker.name}")

            # ✅ STEP 4.3: Build MIME message with SendGrid custom args
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
            import json
            
            msg = MIMEMultipart('alternative')
            msg['Subject'] = rendered_subject
            msg['From'] = sender_email
            msg['To'] = recipient_email
            
            # Add custom args for SendGrid webhook
            custom_args = {
                "email_queue_name": email_queue.name,
                "lead_name": lead_doc.name,
                "tracker_name": tracker.name if tracker else None
            }
            
            msg.add_header('X-SMTPAPI', json.dumps({
                "unique_args": custom_args,
                "category": ["crm_campaign_email"]
            }))
            
            frappe.logger().info(f"[Campaign] Custom args: {json.dumps(custom_args)}")
            
            # Add HTML content
            html_part = MIMEText(rendered_message, 'html')
            msg.attach(html_part)
            
            # Update Email Queue with MIME message
            email_queue.message = msg.as_string()
            email_queue.save(ignore_permissions=True)
            frappe.db.commit()

            initial_delivery_status = "" if not send_now and send_after_datetime else "Sending"
            
            comm = frappe.get_doc({
                "doctype": "Communication",
                "communication_type": "Communication",
                "communication_medium": "Email",
                "subject": rendered_subject,
                "content": rendered_message,
                "sender": sender_email,
                "recipients": recipient_email,
                "status": "Linked",  
                "delivery_status": initial_delivery_status,  
                "sent_or_received": "Sent",
                "reference_doctype": "CRM Lead",
                "reference_name": lead_id,
                "email_queue": email_queue.name,
                "email_status": "Open"
            })
            ensure_communication_has_thread_id(comm)
            comm.insert(ignore_permissions=True)

            if not send_now and send_after_datetime:
                frappe.db.set_value("Communication", comm.name, "delivery_status", "Queued", update_modified=False)
                frappe.db.set_value("Communication", comm.name, "status", "Queued", update_modified=False)
                frappe.db.commit()
                frappe.logger().info(f"[Campaign] Force-set Communication {comm.name} -> Queued")
            frappe.db.commit()

            frappe.logger().info(f"[Campaign] Created Communication: {comm.name} with status: {comm.status}, delivery_status: {comm.delivery_status}")

            # ✅ STEP 4.5: Link Communication to tracker
            if tracker:
                frappe.db.set_value(
                    "Lead Email Tracker",
                    tracker.name,
                    "communication",
                    comm.name,
                    update_modified=False
                )
                frappe.db.commit()
                frappe.logger().info(f"[Campaign] Linked tracker {tracker.name} to communication {comm.name}")

            # ✅ STEP 4.6: Sync message_id if available
            if email_queue.message_id:
                frappe.db.set_value(
                    "Communication",
                    comm.name,
                    "message_id",
                    email_queue.message_id,
                    update_modified=False
                )
                frappe.db.commit()

            status = "scheduled"
            msg_text = "Email queued successfully with tracking"

            # ✅ STEP 4.7: Send immediately if requested
            if send_now:
                try:
                    email_queue.reload()
                    email_queue.send()
                    status = "sent"
                    msg_text = "Email sent immediately with tracking"
                    
                    # ✅ Update tracker status to "Sent"
                    if tracker:
                        frappe.db.set_value(
                            "Lead Email Tracker",
                            tracker.name,
                            "status",
                            "Sent",
                            update_modified=False
                        )
                        frappe.db.set_value(
                            "Lead Email Tracker",
                            tracker.name,
                            "last_sent_on",
                            now_datetime(),
                            update_modified=False
                        )
                    
                    # ✅ Update Communication status to "Sent"
                    frappe.db.set_value(
                        "Communication",
                        comm.name,
                        "status",
                        "Sent",
                        update_modified=False
                    )
                    frappe.db.set_value(
                        "Communication",
                        comm.name,
                        "delivery_status",
                        "Sent",
                        update_modified=False
                    )
                    frappe.db.commit()
                    
                except Exception as send_error:
                    status = "error"
                    msg_text = f"Failed to send email: {str(send_error)}"
                    
                    # ✅ Update status to Error on failure
                    if tracker:
                        frappe.db.set_value(
                            "Lead Email Tracker",
                            tracker.name,
                            "status",
                            "Error",
                            update_modified=False
                        )
                    
                    frappe.db.set_value(
                        "Communication",
                        comm.name,
                        "delivery_status",
                        "Error",
                        update_modified=False
                    )
                    frappe.db.commit()
                    
                    frappe.log_error(
                        title="Email Send Failed",
                        message=f"Lead: {lead_doc.name}\nEmail: {recipient_email}\nError: {str(send_error)}"
                    )
            
            try:
                print("[Campaign] Triggering UI updates for Communication:", comm.name)
                comm_doc = frappe.get_doc("Communication", tracker.communication)
                comm_doc.notify_change("update")
                
                # ✅ Get current delivery_status from DB
                current_delivery_status = frappe.db.get_value("Communication", comm.name, "delivery_status")
                
                frappe.publish_realtime(
                    "list_update",
                    {
                        "doctype": "Communication",
                        "name": comm.name,
                        "delivery_status": "Queued" if not send_now and send_after_datetime else current_delivery_status
                    },
                    after_commit=True
                )
                
                if comm_doc.reference_doctype and comm_doc.reference_name:
                    frappe.publish_realtime(
                        "docinfo_update",
                        {
                            "doc": comm_doc.as_dict(),
                            "key": "communications",
                            "action": "update"
                        },
                        doctype=comm_doc.reference_doctype,
                        docname=comm_doc.reference_name,
                        after_commit=True
                    )
                    frappe.logger().info(f"[Campaign] Published timeline update for {comm_doc.reference_name}")
            except Exception as ui_error:
                frappe.logger().error(f"[Campaign] Failed to trigger UI updates: {str(ui_error)}")
            
            frappe.db.commit()
            
            responses.append({
                "lead": lead_id,
                "email": recipient_email,
                "status": status,
                "message": msg_text,
                "communication_id": comm.name,
                "email_queue_id": email_queue.name,
                "tracker_id": tracker.name if tracker else None,
                "scheduled_time": str(send_after_datetime) if send_after_datetime else str(now_datetime())
            })

        except Exception as e:
            frappe.log_error(title="Email Sending Error", message=frappe.get_traceback())
            responses.append({
                "lead": lead_id,
                "email": getattr(lead_doc, "email", None),
                "status": "error",
                "message": str(e)
            })

    return {
        "segment_id": segment_id,
        "segment_name": segment_label,
        "results": responses
    }

@frappe.whitelist()
def launch_campaign(campaign_name: str, recipient_type: str, recipient_id: str, sender_email: str, start_datetime=None):
    """
    Launch the campaign for either a Lead Segment or individual CRM Lead.
    Handles scheduling, delays, and email template rendering.
    """
    frappe.logger().info(f"[Launch Campaign] Starting: {campaign_name} for {recipient_type}: {recipient_id}")
    
    campaign = frappe.get_doc("Campaign", campaign_name)
    if not campaign.campaign_schedules:
        frappe.throw("No schedules defined for this campaign.")

    # Base start time
    base_time = get_datetime(start_datetime) if start_datetime else now_datetime()

    scheduled_count = 0
    total_emails = 0
    errors = []
    schedule_details = []

    # Validate recipient type once
    if recipient_type not in ["Lead Segment", "CRM Lead"]:
        frappe.throw(f"Invalid recipient type: {recipient_type}")

    # Loop over each schedule in the campaign
    for row in campaign.campaign_schedules:
        try:
            days_delay = cint(row.get("send_after_days", 0))
            minutes_delay = cint(row.get("send_after_minutes", 0))
            total_delay = datetime.timedelta(days=days_delay, minutes=minutes_delay)
            send_time = base_time + total_delay

            template_doc = frappe.get_doc("Email Template", row.email_template)

            # Call send_email_to_segment() for either segment or single lead
            kwargs = {
                "subject": template_doc.subject,
                "message": template_doc.response or template_doc.message or "",
                "sender_email": sender_email,
                "send_now": False,
                "send_after_datetime": send_time
            }

            if recipient_type == "Lead Segment":
                kwargs["segment_name"] = recipient_id
            else:  # CRM Lead
                kwargs["lead_name"] = recipient_id

            result = send_email_to_segment(**kwargs)

            # Count scheduled emails and errors
            scheduled_in_batch = 0
            errors_in_batch = []

            for r in result.get("results", []):
                total_emails += 1
                if r["status"] in ["scheduled", "not sent", "queued"]:
                    scheduled_count += 1
                    scheduled_in_batch += 1
                elif r["status"] == "error":
                    errors_in_batch.append(f"Lead {r.get('lead')}: {r.get('message')}")

            if errors_in_batch:
                errors.extend(errors_in_batch)

            schedule_details.append({
                "template": row.email_template,
                "send_time": send_time.strftime("%Y-%m-%d %H:%M:%S"),
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
        "base_time": base_time.strftime("%Y-%m-%d %H:%M:%S"),
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