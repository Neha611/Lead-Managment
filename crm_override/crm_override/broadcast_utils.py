import frappe
from crm_override.crm_override.email_utils import log_email_in_crm


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
