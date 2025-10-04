import frappe

def log_email_in_crm(lead_name, subject, content, sender, recipients):
    """
    Create a Communication doc to log the email in CRM Lead's email tab
    
    Args:
        lead_name (str): The Lead document name (e.g. CRM-LEAD-2025-00134)
        subject (str): Email subject
        content (str): Email body (HTML)
        sender (str): Sender email address
        recipients (list|str): Single recipient email or list of recipient emails
    
    Returns:
        Communication: The created communication document
    """
    try:
        # Convert recipients to comma-separated string if it's a list
        if isinstance(recipients, list):
            recipients = ", ".join(recipients)
            
        # Get the lead's full name for better display
        lead = frappe.get_doc("CRM Lead", lead_name)
        lead_title = f"{lead.first_name or ''} {lead.last_name or ''}".strip() or lead_name
            
        comm = frappe.get_doc({
            "doctype": "Communication",
            "communication_type": "Email",
            "communication_medium": "Email",
            "status": "Linked",
            "subject": subject,
            "content": content,
            "sender": sender,
            "recipients": recipients,
            "reference_doctype": "CRM Lead",
            "reference_name": lead_name,
            "sent_or_received": "Sent",
            "has_attachment": 0,
            "email_status": "Sent",
            "_liked_by": "[]",
            "seen": 0,
            "timeline_links": [{
                "link_doctype": "CRM Lead",
                "link_name": lead_name,
                "link_title": lead_title
            }],
            "timeline_doctype": "CRM Lead",
            "timeline_name": lead_name,
            "timeline_label": "Email Sent",
            "send_notification": True
        })
        
        comm.insert(ignore_permissions=True)
        frappe.db.commit()
        
        print(f"Email logged successfully - Communication ID: {comm.name}")
        return comm
        
    except Exception as e:
        error_msg = f"Failed to log email for lead {lead_name}: {str(e)}"
        print(error_msg)
        frappe.log_error(error_msg, "Email Logging Error")
        return None