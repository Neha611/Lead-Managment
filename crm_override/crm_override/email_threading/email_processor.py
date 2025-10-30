"""
Central Email Processing Pipeline
Handles all incoming emails regardless of source (IMAP or SendGrid)
"""

import frappe
from frappe.utils import now_datetime, get_datetime
from typing import Dict, Optional
import uuid
from frappe.model.document import Document


def process_incoming_email(normalized_email: Dict) -> Optional[str]:
    """
    Central function to process all incoming emails.
    Handles threading, reply linking, and Communication creation.
    
    Args:
        normalized_email: Standardized email dict from EmailStrategy
        
    Returns:
        Communication name if successful, None otherwise
    """
    try:
        frappe.logger().info(f"[Email Processor] Processing email from: {normalized_email.get('from')}")
        
        # Step 1: Extract or generate thread_id
        thread_id = _resolve_thread_id(normalized_email)
        
        if not thread_id:
            frappe.logger().warning("[Email Processor] Could not resolve thread_id, creating new thread")
            thread_id = _generate_thread_id()
        
        frappe.logger().info(f"[Email Processor] Thread ID: {thread_id}")
        
        # Step 2: Find parent Communication (if reply)
        parent_comm = _find_parent_communication(normalized_email, thread_id)
        
        # Step 3: Determine reference document (CRM Lead, Deal, etc.)
        reference_doctype, reference_name = _resolve_reference_document(
            normalized_email, parent_comm
        )
        
        # Step 4: Create Communication entry
        comm = _create_communication(
            normalized_email=normalized_email,
            thread_id=thread_id,
            parent_comm=parent_comm,
            reference_doctype=reference_doctype,
            reference_name=reference_name
        )
        
        if not comm:
            frappe.logger().error("[Email Processor] Failed to create Communication")
            return None
        
        # Step 5: Handle attachments
        _attach_files(comm, normalized_email.get('attachments', []))
        
        # Step 6: Update Lead Email Tracker if applicable
        _update_tracker_on_reply(comm, normalized_email)
        
        # Step 7: Trigger UI updates
        _trigger_ui_updates(comm)
        
        frappe.db.commit()
        frappe.logger().info(f"[Email Processor] Successfully processed email: {comm.name}")
        
        return comm.name
        
    except Exception as e:
        frappe.log_error(
            title="Email Processing Failed",
            message=f"From: {normalized_email.get('from')}\n"
                   f"Subject: {normalized_email.get('subject')}\n"
                   f"Error: {str(e)}\n"
                   f"{frappe.get_traceback()}"
        )
        return None


def _resolve_thread_id(normalized_email: Dict) -> Optional[str]:
    """
    Extract thread_id from email headers or find from parent emails
    
    Priority:
    1. X-Frappe-Thread-ID header
    2. Find from in_reply_to Communication
    3. Find from references chain
    """
    # Check custom header first
    thread_id = normalized_email.get('thread_id')
    if thread_id:
        return thread_id
    
    # Try to find from in_reply_to
    in_reply_to = normalized_email.get('in_reply_to')
    if in_reply_to:
        parent = frappe.db.get_value(
            "Communication",
            {"message_id": in_reply_to},
            ["name", "thread_id"],
            as_dict=True
        )
        if parent and parent.get('thread_id'):
            return parent.get('thread_id')
    
    # Try references chain
    references = normalized_email.get('references', [])
    if references:
        # Check most recent reference first
        for ref in reversed(references):
            parent = frappe.db.get_value(
                "Communication",
                {"message_id": ref},
                ["name", "thread_id"],
                as_dict=True
            )
            if parent and parent.get('thread_id'):
                return parent.get('thread_id')
    
    return None


def _generate_thread_id() -> str:
    """Generate a unique thread identifier"""
    return f"thread-{uuid.uuid4().hex[:16]}"


def _find_parent_communication(normalized_email: Dict, thread_id: str) -> Optional[frappe._dict]:
    """
    Find parent Communication for reply threading
    """
    in_reply_to = normalized_email.get('in_reply_to')
    
    # First try exact message_id match
    if in_reply_to:
        parent = frappe.db.get_value(
            "Communication",
            {"message_id": in_reply_to},
            ["name", "reference_doctype", "reference_name", "thread_id"],
            as_dict=True
        )
        if parent:
            frappe.logger().info(f"[Email Processor] Found parent by message_id: {parent.name}")
            return parent
    
    # Try finding by thread_id (most recent in thread)
    if thread_id:
        parents = frappe.get_all(
            "Communication",
            filters={"thread_id": thread_id},
            fields=["name", "reference_doctype", "reference_name", "thread_id", "creation"],
            order_by="creation desc",
            limit=1
        )
        if parents:
            frappe.logger().info(f"[Email Processor] Found parent by thread_id: {parents[0].name}")
            return parents[0]
    
    return None


def _resolve_reference_document(normalized_email: Dict, parent_comm: Optional[frappe._dict]) -> tuple:
    """
    Determine which CRM document this email belongs to
    
    Returns: (reference_doctype, reference_name)
    """
    # If this is a reply, inherit from parent
    if parent_comm:
        return (parent_comm.get('reference_doctype'), parent_comm.get('reference_name'))
    
    # Try to find CRM Lead by email address
    sender_email = normalized_email.get('from')
    if sender_email:
        lead = frappe.db.get_value("CRM Lead", {"email": sender_email}, "name")
        if lead:
            frappe.logger().info(f"[Email Processor] Found CRM Lead: {lead}")
            return ("CRM Lead", lead)
    
    # Could extend to search other doctypes (Contact, Deal, etc.)
    
    frappe.logger().warning("[Email Processor] No reference document found")
    return (None, None)


def _create_communication(
    normalized_email: Dict,
    thread_id: str,
    parent_comm: Optional[frappe._dict],
    reference_doctype: Optional[str],
    reference_name: Optional[str]
) -> Optional[Document]:
    """
    Create Communication document from normalized email
    """
    try:
        # Determine content (prefer HTML, fallback to text)
        content = normalized_email.get('body_html') or normalized_email.get('body_text') or ''
        
        comm_data = {
            "doctype": "Communication",
            "communication_type": "Communication",
            "communication_medium": "Email",
            "sent_or_received": "Received",
            "subject": normalized_email.get('subject', '(No Subject)'),
            "sender": normalized_email.get('from'),
            "recipients": ', '.join(normalized_email.get('to', [])),
            "cc": ', '.join(normalized_email.get('cc', [])),
            "content": content,
            "text_content": normalized_email.get('body_text', ''),
            "status": "Open",
            "delivery_status": "Received",
            "message_id": normalized_email.get('message_id'),
            "in_reply_to": normalized_email.get('in_reply_to'),
            "thread_id": thread_id,
            "email_status": "Open",
            "received_at": normalized_email.get('date', now_datetime()),
        }
        
        # Add reference if available
        if reference_doctype and reference_name:
            comm_data["reference_doctype"] = reference_doctype
            comm_data["reference_name"] = reference_name
        
        # Create Communication
        comm = frappe.get_doc(comm_data)
        comm.insert(ignore_permissions=True)
        
        frappe.logger().info(
            f"[Email Processor] Created Communication: {comm.name} | "
            f"Thread: {thread_id} | "
            f"Parent: {parent_comm.name if parent_comm else 'None'}"
        )
        
        return comm
        
    except Exception as e:
        frappe.log_error(
            title="Communication Creation Failed",
            message=f"Error: {str(e)}\n{frappe.get_traceback()}"
        )
        return None


def _attach_files(comm: Document, attachments: list):
    """
    Attach files to Communication document
    """
    if not attachments:
        return
    
    for attachment in attachments:
        try:
            file_doc = frappe.get_doc({
                "doctype": "File",
                "file_name": attachment.get('filename'),
                "attached_to_doctype": "Communication",
                "attached_to_name": comm.name,
                "content": attachment.get('content'),
                "decode": False,
                "is_private": 1
            })
            file_doc.save(ignore_permissions=True)
            
            frappe.logger().info(f"[Email Processor] Attached file: {attachment.get('filename')}")
            
        except Exception as e:
            frappe.log_error(
                title="File Attachment Failed",
                message=f"Communication: {comm.name}\n"
                       f"File: {attachment.get('filename')}\n"
                       f"Error: {str(e)}"
            )


def _update_tracker_on_reply(comm: Document, normalized_email: Dict):
    """
    Update Lead Email Tracker when lead replies to campaign email
    """
    try:
        # Only process if this is linked to a CRM Lead
        if comm.reference_doctype != "CRM Lead":
            return
        
        lead_name = comm.reference_name
        
        # Find tracker for this lead in the same thread
        tracker = frappe.db.get_value(
            "Lead Email Tracker",
            {
                "lead": lead_name,
                "communication": ["!=", ""]  # Must have sent email
            },
            ["name", "communication"],
            as_dict=True
        )
        
        if not tracker:
            frappe.logger().info(f"[Email Processor] No tracker found for lead {lead_name}")
            return
        
        # Check if original email is in same thread
        original_comm = frappe.get_value("Communication", tracker.communication, "thread_id")
        
        if original_comm == comm.thread_id:
            # This is a reply to our campaign email!
            frappe.db.set_value(
                "Lead Email Tracker",
                tracker.name,
                {
                    "status": "Replied",
                    "replied_on": now_datetime()
                },
                update_modified=False
            )
            
            frappe.logger().info(f"[Email Processor] Updated tracker {tracker.name} -> Replied")
            
    except Exception as e:
        frappe.log_error(
            title="Tracker Update Failed",
            message=f"Communication: {comm.name}\n"
                   f"Error: {str(e)}"
        )


def _trigger_ui_updates(comm: Document):
    """
    Publish realtime updates for UI refresh
    """
    try:
        # Notify Communication list
        frappe.publish_realtime(
            "list_update",
            {
                "doctype": "Communication",
                "name": comm.name
            },
            after_commit=True
        )
        
        # Update timeline on reference document
        if comm.reference_doctype and comm.reference_name:
            frappe.publish_realtime(
                "docinfo_update",
                {
                    "doc": comm.as_dict(),
                    "key": "communications",
                    "action": "add"
                },
                doctype=comm.reference_doctype,
                docname=comm.reference_name,
                after_commit=True
            )
            
            frappe.logger().info(f"[Email Processor] Published UI updates for {comm.name}")
            
    except Exception as e:
        frappe.logger().error(f"[Email Processor] UI update failed: {str(e)}")


@frappe.whitelist(allow_guest=True)
def handle_sendgrid_webhook():
    """
    Webhook endpoint for SendGrid Inbound Parse
    URL: /api/method/crm_override.crm_override.email_processor.handle_sendgrid_webhook
    """
    try:
        # Get POST data from SendGrid
        from frappe import request
        raw_data = request.form.to_dict()
        
        frappe.logger().info("[SendGrid Webhook] Received email")
        
        # Use SendGrid strategy
        from crm_override.crm_override.email_threading.email_strategy import SendGridStrategy
        strategy = SendGridStrategy()
        normalized_email = strategy.normalize_email(raw_data)
        
        if not normalized_email:
            return {"status": "error", "message": "Failed to normalize email"}
        
        # Process email
        comm_name = process_incoming_email(normalized_email)
        
        if comm_name:
            return {"status": "success", "communication": comm_name}
        else:
            return {"status": "error", "message": "Failed to process email"}
            
    except Exception as e:
        frappe.log_error(
            title="SendGrid Webhook Error",
            message=f"Error: {str(e)}\n{frappe.get_traceback()}"
        )
        return {"status": "error", "message": str(e)}