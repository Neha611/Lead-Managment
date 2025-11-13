"""
Enhanced Outbound Email Threading - FIXED VERSION
Properly handles both in_reply_to and message_id-based lookups
"""

import frappe
from frappe.utils import now_datetime
import uuid

from crm_override.crm_override.doctype.email_thread_mapping.email_thread_mapping import (
    store_message_thread_mapping,
    get_thread_id_from_message_id
)


def generate_thread_id() -> str:
    """Generate unique thread identifier"""
    return f"thread-{uuid.uuid4().hex[:16]}"


def _clean_message_id(msg_id: str) -> str:
    """Clean and standardize Message-ID format"""
    if not msg_id:
        return ''
    
    msg_id = msg_id.strip()
    
    # Add angle brackets if missing
    if msg_id and not msg_id.startswith('<'):
        msg_id = f'<{msg_id}>'
    if msg_id and not msg_id.endswith('>'):
        msg_id = f'{msg_id}>'
    
    return msg_id


def ensure_communication_has_thread_id(comm_doc, method=None):
    """
    Ensure all Communications have thread_id and message_id
    This runs BEFORE insert (before_insert hook)

    IMPORTANT: Only runs for SENT/outbound emails to avoid interfering with
    the email validation flow for incoming emails.

    CRITICAL FIX: Check email headers FIRST before falling back to other methods
    This ensures replies inherit the correct thread even when in_reply_to field isn't set
    """
    try:
        # CRITICAL: Skip for incoming emails - let validation hooks handle them
        sent_or_received = getattr(comm_doc, "sent_or_received", "Sent")
        if sent_or_received == "Received":
            print(f"[Thread ID] Skipping received email - handled by validation flow")
            return
        # Step 0: Generate message_id if not present (for outbound emails)
        if not getattr(comm_doc, "message_id", None) and getattr(comm_doc, "sent_or_received", "") == "Sent":
            # Generate a unique message_id for outbound emails
            import socket
            import time
            hostname = socket.getfqdn()
            timestamp = int(time.time() * 1000)
            import random
            random_part = random.randint(10000, 99999)
            message_id = f"{timestamp}.{random_part}.{random.randint(1000000000000000000, 9999999999999999999)}@{hostname}"
            comm_doc.message_id = message_id
            print(f"[Thread ID] Generated message_id for outbound email: {message_id}")
        
        # Step 1: If already has thread_id, keep it
        if getattr(comm_doc, "thread_id", None):
            print(f"[Thread ID] Already set: {comm_doc.thread_id}")
            return

        sent_or_received = getattr(comm_doc, "sent_or_received", "Sent")
        in_reply_to_comm = getattr(comm_doc, "in_reply_to", None)  # Communication name
        message_id = getattr(comm_doc, "message_id", None)
        reference_doctype = getattr(comm_doc, "reference_doctype", None)
        reference_name = getattr(comm_doc, "reference_name", None)
        
        print(
            f"[Thread ID] Processing {sent_or_received} email\n"
            f"  Subject: {getattr(comm_doc, 'subject', 'N/A')[:50]}\n"
            f"  Message-ID: {message_id}\n"
            f"  In-Reply-To (Communication): {in_reply_to_comm}\n"
            f"  Reference: {reference_doctype}/{reference_name if reference_name else 'None'}"
        )

        # Step 2: CRITICAL - Check email headers for In-Reply-To BEFORE checking in_reply_to field
        # This handles the case where you're replying to an email but the in_reply_to field isn't set
        # Get the email headers if available (for outbound emails, these might be in flags)
        email_in_reply_to = None
        
        # Check if this is being sent via email and has headers
        if hasattr(comm_doc, 'flags') and comm_doc.flags.get('email_headers'):
            email_in_reply_to = comm_doc.flags.get('email_headers', {}).get('In-Reply-To')
            if email_in_reply_to:
                email_in_reply_to = _clean_message_id(email_in_reply_to)
                print(f"[Thread ID] Found In-Reply-To in email headers: {email_in_reply_to}")
        
        # Also check if In-Reply-To is stored in any other way (custom field, etc.)
        if not email_in_reply_to and hasattr(comm_doc, 'email_in_reply_to'):
            email_in_reply_to = _clean_message_id(getattr(comm_doc, 'email_in_reply_to', ''))
        
        # If we have an In-Reply-To header, look up the parent's thread_id via Email Thread Mapping
        if email_in_reply_to:
            print(f"[Thread ID] Looking up parent via In-Reply-To header: {email_in_reply_to}")
            
            # Use the mapping table to find the thread_id
            parent_thread_id = get_thread_id_from_message_id(email_in_reply_to)
            
            if parent_thread_id:
                comm_doc.thread_id = parent_thread_id
                
                # Also try to set in_reply_to field for proper hierarchy
                parent_comm_name = frappe.db.get_value(
                    "Email Thread Mapping",
                    {"message_id": email_in_reply_to},
                    "communication"
                )
                if parent_comm_name and not comm_doc.in_reply_to:
                    comm_doc.in_reply_to = parent_comm_name
                
                print(
                    f"[Thread ID] ✅ Reply detected via email header | "
                    f"Inherited thread: {parent_thread_id}"
                )
                return
            else:
                print(f"[Thread ID] ⚠️ In-Reply-To header present but no thread found: {email_in_reply_to}")

        # Step 3: Check in_reply_to field (Communication name)
        if in_reply_to_comm:
            print(f"[Thread ID] Looking up parent via in_reply_to field: {in_reply_to_comm}")
            
            parent_thread_id = frappe.db.get_value(
                "Communication",
                in_reply_to_comm,
                "thread_id"
            )
            
            if parent_thread_id:
                comm_doc.thread_id = parent_thread_id
                print(
                    f"[Thread ID] ✅ Reply detected via in_reply_to field | "
                    f"Inherited from parent {in_reply_to_comm}: {parent_thread_id}"
                )
                return
            else:
                print(
                    f"[Thread ID] ⚠️ Parent Communication {in_reply_to_comm} "
                    f"found but has no thread_id"
                )

        # Step 4: FALLBACK - Check if replying to a document with existing communications
        subject = getattr(comm_doc, "subject", "")
        
        # Check multiple indicators that this is a reply
        is_reply = False
        
        # Indicator 1: Subject starts with Re: or Fwd:
        if subject:
            subject_lower = subject.strip().lower()
            is_reply = (
                subject_lower.startswith("re:") or 
                subject_lower.startswith("fwd:") or
                subject_lower.startswith("fw:")
            )
        
        # Indicator 2: Check if there's a flag from the client side
        if hasattr(comm_doc, 'flags') and comm_doc.flags.get('is_reply'):
            is_reply = True
        
        if is_reply and reference_doctype and reference_name:
            print(f"[Thread ID] Reply detected - checking for existing thread on {reference_doctype}/{reference_name}")
            
            # Get the most recent Communication on this document
            latest_comm = frappe.db.get_value(
                "Communication",
                {
                    "reference_doctype": reference_doctype,
                    "reference_name": reference_name
                },
                ["name", "thread_id", "creation"],
                order_by="creation desc",
                as_dict=True
            )
            
            if latest_comm and latest_comm.thread_id:
                comm_doc.thread_id = latest_comm.thread_id
                
                # IMPORTANT: Set in_reply_to to create proper hierarchy
                comm_doc.in_reply_to = latest_comm.name
                
                print(
                    f"[Thread ID] ✅ Found existing thread on document | "
                    f"Using thread: {latest_comm.thread_id} from {latest_comm.name}"
                )
                return
            else:
                print(f"[Thread ID] No existing communications found on {reference_doctype}/{reference_name}")
        elif reference_doctype and reference_name:
            print(f"[Thread ID] New conversation (subject: '{subject[:30]}...') - will create new thread")

        # Step 5: Generate new thread_id for new conversation
        comm_doc.thread_id = generate_thread_id()
        print(
            f"[Thread ID] ✅ New conversation | "
            f"Generated: {comm_doc.thread_id}"
        )

    except Exception as e:
        frappe.log_error(
            title="Thread ID Assignment Failed",
            message=f"Communication: {getattr(comm_doc, 'name', 'New')}\n"
                   f"Error: {str(e)}\n{frappe.get_traceback()}"
        )
        # Generate fallback thread_id
        if not getattr(comm_doc, "thread_id", None):
            comm_doc.thread_id = generate_thread_id()


def after_communication_insert(comm_doc, method=None):
    """
    After Communication created - placeholder for future thread mapping
    This runs AFTER insert (after_insert hook)

    IMPORTANT: Only runs for SENT/outbound emails to avoid interfering with
    the email validation flow for incoming emails.

    NOTE: Thread mapping storage is currently disabled to prevent validation errors
    during bulk email ingestion from sources that don't have Message-IDs
    """
    try:
        # CRITICAL: Skip for incoming emails - let validation hooks handle them
        sent_or_received = getattr(comm_doc, "sent_or_received", "Sent")
        if sent_or_received == "Received":
            frappe.logger().debug(f"[Thread ID] Skipping received email - handled by validation flow")
            return

        message_id = getattr(comm_doc, "message_id", None)
        thread_id = getattr(comm_doc, "thread_id", None)

        # Log for debugging purposes only - no mapping storage
        if message_id and thread_id:
            frappe.logger().debug(
                f"[Thread ID] Communication created | "
                f"Message-ID: {message_id} | Thread: {thread_id}"
            )
        else:
            if not message_id:
                frappe.logger().debug(
                    f"[Thread ID] Communication {comm_doc.name} has no message_id"
                )
            if not thread_id:
                frappe.logger().debug(
                    f"[Thread ID] Communication {comm_doc.name} has no thread_id"
                )

    except Exception as e:
        frappe.logger().error(
            f"[Thread ID] After insert hook failed for {getattr(comm_doc, 'name', 'Unknown')}: {str(e)}"
        )


@frappe.whitelist()
def get_thread_emails(thread_id):
    """
    Get all emails in a thread, ordered by creation date
    """
    try:
        emails = frappe.get_all(
            "Communication",
            filters={"thread_id": thread_id},
            fields=[
                "name", "subject", "sender", "recipients", 
                "content", "sent_or_received", "creation",
                "message_id", "in_reply_to"
            ],
            order_by="creation asc"
        )
        
        return emails
        
    except Exception as e:
        frappe.logger().error(f"[Thread ID] Get thread emails failed: {str(e)}")
        return []


@frappe.whitelist()
def debug_communication_threading(comm_name):
    """
    Debug helper - shows complete thread hierarchy
    """
    try:
        comm = frappe.get_doc("Communication", comm_name)
        
        # Check mapping
        message_id = getattr(comm, "message_id", None)
        mapping_info = None
        if message_id:
            from crm_override.crm_override.doctype.email_thread_mapping.email_thread_mapping import debug_thread_mapping
            mapping_info = debug_thread_mapping(message_id)
        
        # Find parent (in_reply_to is Communication name)
        parent = None
        if comm.in_reply_to:
            parent = frappe.db.get_value(
                "Communication",
                comm.in_reply_to,
                ["name", "thread_id", "subject", "message_id"],
                as_dict=True
            )
        
        # Find all in thread
        thread_comms = frappe.get_all(
            "Communication",
            filters={"thread_id": comm.thread_id},
            fields=["name", "subject", "sent_or_received", "creation", "message_id", "in_reply_to"],
            order_by="creation asc"
        )
        
        # Find children (Communications that reply to this one)
        children = frappe.get_all(
            "Communication",
            filters={"in_reply_to": comm.name},
            fields=["name", "subject", "thread_id", "sent_or_received"],
            order_by="creation asc"
        )
        
        return {
            "communication": {
                "name": comm.name,
                "subject": comm.subject,
                "thread_id": comm.thread_id,
                "message_id": message_id,
                "in_reply_to": comm.in_reply_to,
                "sent_or_received": comm.sent_or_received,
                "reference_doctype": comm.reference_doctype,
                "reference_name": comm.reference_name
            },
            "mapping": mapping_info,
            "parent": parent,
            "children": children,
            "thread_emails": thread_comms,
            "thread_count": len(thread_comms)
        }
        
    except Exception as e:
        return {
            "error": str(e),
            "traceback": frappe.get_traceback()
        }


@frappe.whitelist()
def get_email_thread_tree(thread_id):
    """
    Get hierarchical view of email thread
    Builds a tree structure showing reply relationships
    """
    try:
        # Get all Communications in thread
        comms = frappe.get_all(
            "Communication",
            filters={"thread_id": thread_id},
            fields=["name", "subject", "sender", "sent_or_received", "creation", "in_reply_to"],
            order_by="creation asc"
        )
        
        # Build tree structure
        comm_map = {c['name']: {**c, 'replies': []} for c in comms}
        root_emails = []
        
        for comm in comms:
            if comm['in_reply_to'] and comm['in_reply_to'] in comm_map:
                # This is a reply, add to parent's replies
                comm_map[comm['in_reply_to']]['replies'].append(comm_map[comm['name']])
            else:
                # This is a root email (no parent)
                root_emails.append(comm_map[comm['name']])
        
        return {
            "thread_id": thread_id,
            "total_emails": len(comms),
            "tree": root_emails
        }
        
    except Exception as e:
        return {
            "error": str(e),
            "traceback": frappe.get_traceback()
        }