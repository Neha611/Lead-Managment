import frappe
from frappe import _
import base64
import email
from email import policy

from crm_override.crm_override.broadcast_utils import send_email_to_segment, create_lead_segment as create_segment
from frappe.email.receive import InboundMail

@frappe.whitelist()
def broadcast_to_segment(segment_name, subject, message, sender_email):
    """
    API endpoint to send broadcast email to all leads in a segment.
    Accessible to authenticated users with appropriate roles.
    """
    # Check if user has Sales User or higher role
    roles = frappe.get_roles(frappe.session.user)
    if not any(role in roles for role in ['Sales User', 'Sales Manager', 'System Manager']):
        frappe.throw(_("You must have Sales User or higher role to send emails"), frappe.PermissionError)
    
    # Check document level permissions
    if not frappe.has_permission("Lead Segment", "email"):
        frappe.throw(_("You don't have permission to send emails to segments"), frappe.PermissionError)
        
    # Check if user owns or has access to this segment
    segment = frappe.get_doc("Lead Segment", segment_name)
    if not (frappe.session.user == "Administrator" or segment.owner == frappe.session.user):
        frappe.throw(_("You can only send emails to segments that you own"), frappe.PermissionError)
        
    if not (segment_name and subject and message and sender_email):
        frappe.throw(_("All parameters are required."))
    return send_email_to_segment(segment_name, subject, message, sender_email)

def create_lead(first_name, email, company_name=None, last_name=None):
    """Helper function to create a new lead"""
    # Check if user has Sales User or higher role
    roles = frappe.get_roles(frappe.session.user)
    if not any(role in roles for role in ['Sales User', 'Sales Manager', 'System Manager']):
        frappe.throw(_("You must have Sales User or higher role to create leads"), frappe.PermissionError)
        
    if not frappe.has_permission("CRM Lead", "create"):
        frappe.throw(_("You don't have permission to create leads"), frappe.PermissionError)
        
    lead = frappe.get_doc({
        "doctype": "CRM Lead",
        "first_name": first_name,
        "last_name": last_name or "",
        "email": email,
        "company_name": company_name or "Not Specified"
    })
    lead.insert()
    return lead.name

@frappe.whitelist()
def create_lead_segment(segmentname, leads_data=None, lead_names=None, description=None):
    """
    API endpoint to create a lead segment from either:
    1. lead_names: List of existing lead IDs
    2. leads_data: List of dictionaries with lead information to create/use leads
    
    For leads_data, each item should be a dict with:
    {
        "first_name": "Name",
        "email": "email@example.com",
        "last_name": "Last Name",  # optional
        "company_name": "Company"   # optional
    }
    """
    # Check permissions for both lead and segment creation
    if not frappe.has_permission("Lead Segment", "create"):
        frappe.throw(_("You don't have permission to create segments"), frappe.PermissionError)
        
    if not segmentname:
        frappe.throw(_("Segment name is required."))
    
    if not (leads_data or lead_names):
        frappe.throw(_("Either leads_data or lead_names must be provided."))
    
    final_lead_names = []
    
    # If lead_names is provided, use them directly
    if lead_names:
        if isinstance(lead_names, list):
            final_lead_names = lead_names
        else:
            frappe.throw(_("lead_names must be a list of lead IDs"))
    
    # If leads_data is provided, process it
    if leads_data:
        if isinstance(leads_data, list):
            for lead_data in leads_data:
                # Check if lead already exists
                existing_lead = frappe.get_list(
                    "CRM Lead",
                    filters={"email": lead_data.get("email")},
                    fields=["name"]
                )
                
                if existing_lead:
                    final_lead_names.append(existing_lead[0].name)
                else:
                    # Create new lead
                    lead_name = create_lead(
                        first_name=lead_data.get("first_name"),
                        last_name=lead_data.get("last_name"),
                        email=lead_data.get("email"),
                        company_name=lead_data.get("company_name")
                    )
                    final_lead_names.append(lead_name)
        else:
            frappe.throw(_("leads_data must be a list of lead information dictionaries"))
    
    # Create segment with the leads
    segment = create_segment(segmentname, final_lead_names, description)
    
    return {
        "name": segment.name,
        "segmentname": segment.segmentname,
        "leads": final_lead_names
    }

def find_existing_lead_for_email(sender_email):
    """
    Check if email exists in CRM Lead, Non Lead, or Query.
    Returns: (doctype, name) or (None, None)

    Args:
        sender_email: Email address to check

    Returns:
        tuple: (doctype_name, record_name) if found, else (None, None)
    """
    if not sender_email:
        return (None, None)

    # Check CRM Lead
    crm_lead = frappe.db.get_value("CRM Lead", {"email": sender_email}, "name")
    if crm_lead:
        return ("CRM Lead", crm_lead)

    # Check Non Lead
    non_lead = frappe.db.get_value("Non Lead", {"email": sender_email}, "name")
    if non_lead:
        return ("Non Lead", non_lead)

    # Check Query
    query = frappe.db.get_value("Query", {"email": sender_email}, "name")
    if query:
        return ("Query", query)

    return (None, None)


def extract_sender_from_raw_email(raw_email):
    """
    Extract sender email from raw email bytes.

    Args:
        raw_email: Raw email bytes

    Returns:
        str: Sender email address
    """
    try:
        # Parse email using email library
        msg = email.message_from_bytes(raw_email, policy=policy.default)
        from_header = msg.get('From', '')

        # Extract email from "Name <email@example.com>" format
        if '<' in from_header and '>' in from_header:
            sender_email = from_header.split('<')[1].split('>')[0].strip()
        else:
            sender_email = from_header.strip()

        return sender_email.lower()
    except Exception as e:
        frappe.logger().error(f"Failed to extract sender from raw email: {str(e)}")
        return None


@frappe.whitelist(allow_guest=True)
def ingest_emails_batch(emails_data, email_account_name=None):
    """
    API endpoint to ingest a batch of emails through Frappe's email pipeline.

    Args:
        emails_data: List of email dictionaries, each containing:
            - raw_email: Base64-encoded raw email message
            - uid: Unique identifier for this email (optional)
        email_account_name: Name of Email Account to use (optional, defaults to first active account)

    Returns:
        Dictionary with batch processing results
    """
    # Get email account
    if not email_account_name:
        accounts = frappe.get_all(
            "Email Account",
            filters={"enable_incoming": 1},
            fields=["name", "email_id"],
            limit=1
        )
        if not accounts:
            frappe.throw("No active Email Account found. Please enable incoming email for an account.")
        email_account_name = accounts[0].name

    email_account = frappe.get_doc("Email Account", email_account_name)

    # Process emails
    results = []
    processed = 0
    skipped = 0
    errors = 0

    for idx, email_data in enumerate(emails_data):
        try:
            # Decode raw email
            raw_email = base64.b64decode(email_data.get("raw_email"))
            uid = email_data.get("uid", -(idx + 1))

            # Extract sender email to check for existing lead
            sender_email = extract_sender_from_raw_email(raw_email)

            # Check if sender exists in any category
            existing_doctype, existing_name = find_existing_lead_for_email(sender_email)

            # Create InboundMail object
            inbound_mail = InboundMail(
                content=raw_email,
                email_account=email_account,
                uid=uid,
                seen_status=1,
                append_to=None  # Don't auto-create Leads
            )

            # Set flags based on existing lead
            if existing_doctype and existing_name:
                # Known contact - skip validation, link directly
                inbound_mail.flags.skip_validation = True
                inbound_mail.flags.link_to_doctype = existing_doctype
                inbound_mail.flags.link_to_name = existing_name
            else:
                # New contact - validate immediately
                inbound_mail.flags.skip_validation = False
                inbound_mail.flags.validate_immediately = True

            # Get email details for logging
            subject = inbound_mail.subject[:50] if inbound_mail.subject else "No Subject"
            sender = inbound_mail.from_email

            try:
                # Process through pipeline
                communication = inbound_mail.process()
                processed += 1

                result = {
                    "status": "success",
                    "sender": sender,
                    "subject": subject,
                    "communication": communication.name if communication else None
                }

                # Check AI validation result if available
                if hasattr(communication, 'flags') and hasattr(communication.flags, 'ai_validation_result'):
                    result["ai_validation"] = communication.flags.ai_validation_result

                results.append(result)

            except Exception as process_error:
                error_msg = str(process_error)
                if "SentEmailInInboxError" in error_msg or "same as recipient" in error_msg:
                    skipped += 1
                    results.append({
                        "status": "skipped",
                        "sender": sender,
                        "subject": subject,
                        "reason": "Sent by same account"
                    })
                else:
                    errors += 1
                    results.append({
                        "status": "error",
                        "sender": sender,
                        "subject": subject,
                        "error": error_msg[:200]
                    })

        except Exception as e:
            errors += 1
            results.append({
                "status": "error",
                "error": str(e)[:200]
            })

    # Commit changes
    frappe.db.commit()

    return {
        "total": len(emails_data),
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
        "results": results
    }