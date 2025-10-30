"""
Outbound Email Threading
Ensures all outbound emails include X-Frappe-Thread-ID header for reply tracking
"""

import frappe
from frappe.utils import now_datetime
import uuid


def generate_thread_id() -> str:
    """Generate unique thread identifier"""
    return f"thread-{uuid.uuid4().hex[:16]}"


def add_thread_id_to_outbound_email(email_queue_doc, method=None):
    """
    Hook to add X-Frappe-Thread-ID header to outbound emails.
    Ensures that all outbound emails from the same broadcast/segment share one thread_id.
    """
    try:
        thread_id = None

        # --- Case 1: Reuse thread_id from linked Communication ---
        if getattr(email_queue_doc, "communication", None):
            comm = frappe.get_doc("Communication", email_queue_doc.communication)
            thread_id = getattr(comm, "thread_id", None)

        # --- Case 2: Try reusing from other queued emails of same campaign/segment ---
        if not thread_id and getattr(email_queue_doc, "reference_name", None):
            existing_thread = frappe.db.get_value(
                "Email Queue",
                {
                    "reference_doctype": email_queue_doc.reference_doctype,
                    "reference_name": email_queue_doc.reference_name,
                    "thread_id": ["!=", ""],
                },
                "thread_id",
            )
            if existing_thread:
                thread_id = existing_thread

        # --- Case 3: Fallback: generate new thread_id ---
        if not thread_id:
            thread_id = generate_thread_id()

            # Save thread_id to Communication (if any)
            if getattr(email_queue_doc, "communication", None):
                frappe.db.set_value(
                    "Communication",
                    email_queue_doc.communication,
                    "thread_id",
                    thread_id,
                    update_modified=False,
                )

        # --- Save thread_id to Email Queue for reuse ---
        frappe.db.set_value(
            "Email Queue",
            email_queue_doc.name,
            "thread_id",
            thread_id,
            update_modified=False,
        )

        # --- Inject into email headers ---
        _inject_thread_id_header(email_queue_doc, thread_id)

        frappe.logger().info(
            f"[Thread ID] Added/Reused for Email Queue {email_queue_doc.name}: {thread_id}"
        )
        return thread_id

    except Exception as e:
        frappe.log_error(
            title="Thread ID Addition Failed",
            message=f"Email Queue: {email_queue_doc.name}\nError: {str(e)}\n{frappe.get_traceback()}",
        )
        return None


def _inject_thread_id_header(email_queue_doc, thread_id: str):
    """
    Inject X-Frappe-Thread-ID into email message headers
    """
    try:
        from email.parser import Parser
        from email import policy
        
        # Parse existing message
        if not email_queue_doc.message:
            frappe.logger().warning(f"[Thread ID] No message in Email Queue {email_queue_doc.name}")
            return
        
        # Parse message
        msg = Parser(policy=policy.default).parsestr(email_queue_doc.message)
        
        # Add custom header
        msg['X-Frappe-Thread-ID'] = thread_id
        
        # Also add as custom SMTP header for SendGrid
        if frappe.conf.get('email_inbound_source', 'imap').lower() == 'sendgrid':
            import json
            
            # Get existing X-SMTPAPI or create new
            smtpapi = msg.get('X-SMTPAPI', '{}')
            try:
                smtpapi_data = json.loads(smtpapi)
            except:
                smtpapi_data = {}
            
            # Add to unique_args
            if 'unique_args' not in smtpapi_data:
                smtpapi_data['unique_args'] = {}
            
            smtpapi_data['unique_args']['thread_id'] = thread_id
            
            # Update header
            msg.replace_header('X-SMTPAPI', json.dumps(smtpapi_data))
        
        # Update email queue message
        email_queue_doc.message = msg.as_string()
        
    except Exception as e:
        frappe.log_error(
            title="Thread ID Header Injection Failed",
            message=f"Thread ID: {thread_id}\n"
                   f"Error: {str(e)}"
        )


def ensure_communication_has_thread_id(comm_doc, method=None):
    """
    Ensure all Communications from the same Email Queue share the same thread_id.
    """
    try:
        # if already has thread_id, skip
        if getattr(comm_doc, "thread_id", None):
            return

        # Try linking by Email Queue
        if comm_doc.email_queue:
            existing_thread_id = frappe.db.get_value(
                "Email Queue",
                comm_doc.email_queue,
                "thread_id"
            )
            if existing_thread_id:
                comm_doc.thread_id = existing_thread_id
                return

        # Try linking by parent Communication (in case of reply)
        if comm_doc.in_reply_to:
            parent_thread_id = frappe.db.get_value(
                "Communication",
                {"message_id": comm_doc.in_reply_to},
                "thread_id"
            )
            if parent_thread_id:
                comm_doc.thread_id = parent_thread_id
                return

        # Try linking by subject (last resort)
        existing_thread_id = frappe.db.get_value(
            "Communication",
            {"subject": comm_doc.subject, "sent_or_received": comm_doc.sent_or_received},
            "thread_id"
        )
        if existing_thread_id:
            comm_doc.thread_id = existing_thread_id
            return

        # Otherwise generate a new one
        comm_doc.thread_id = generate_thread_id()

    except Exception:
        frappe.log_error(frappe.get_traceback(), "ensure_communication_has_thread_id failed")



def update_broadcast_utils_with_threading():
    """
    Helper function that shows how to integrate threading into existing broadcast_utils.py
    
    Add these changes to broadcast_utils.py:
    
    1. Import at top:
       from crm_override.crm_override.outbound_email_threading import (
           generate_thread_id, 
           add_thread_id_to_outbound_email,
           ensure_communication_has_thread_id
       )
    
    2. In send_email_to_segment(), after creating Communication:
       # Ensure thread_id is set
       ensure_communication_has_thread_id(comm)
       comm.save(ignore_permissions=True)
    
    3. In send_email_to_segment(), after creating Email Queue, before setting message:
       # Add thread_id to email headers
       add_thread_id_to_outbound_email(email_queue)
    
    4. The MIME message building section already includes X-SMTPAPI,
       so the thread_id will be automatically added to custom_args
    """
    pass