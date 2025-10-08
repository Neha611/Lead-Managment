import frappe
from apps.frappe.frappe.email.queue import update_email_status

@frappe.whitelist(allow_guest=True)
def email_tracker(name):
    """
    Called when email pixel is hit
    """
    # Update Frappe Email Queue status
    update_email_status(name)
    
    email_queue = frappe.get_doc("Email Queue", name)
    
    if email_queue.reference_doctype == "CRM Lead":
        # Update custom Lead Email Tracker
        tracker = frappe.get_all(
            "Lead Email Tracker", 
            filters={"email_queue": name}, 
            limit=1
        )
        if tracker:
            frappe.db.set_value("Lead Email Tracker", tracker[0].name, "status", "Opened")
    
    return "OK"
