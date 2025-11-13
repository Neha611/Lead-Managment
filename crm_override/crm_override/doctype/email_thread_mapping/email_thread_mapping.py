# Copyright (c) 2025, Neha and contributors
# For license information, please see license.txt

from frappe.model.document import Document
import frappe
from frappe.utils import now_datetime
from typing import Optional


class EmailThreadMapping(Document):
    """
    Email Thread Mapping - Explicit Message-ID to Thread-ID Storage
    """
    pass


def _clean_message_id(msg_id: str) -> str:
    """
    Clean and standardize Message-ID format.
    Ensures consistent format with angle brackets (RFC 5322 compliant).
    Example:
        "abc@xyz.com" -> "<abc@xyz.com>"
        "<abc@xyz.com>" -> "<abc@xyz.com>"
    """
    if not msg_id:
        return ''
    
    # First: strip whitespace and remove any existing brackets
    msg_id = msg_id.strip()
    if msg_id.startswith('<'):
        msg_id = msg_id[1:]
    if msg_id.endswith('>'):
        msg_id = msg_id[:-1]
    
    # Then: add brackets to ensure consistent format
    if msg_id:
        msg_id = f'<{msg_id}>'
    
    return msg_id


def store_message_thread_mapping(message_id: str, thread_id: str, comm_name: str = None):
    """
    Store explicit mapping between Message-ID and Thread-ID

    IMPORTANT: This function silently skips storage if message_id is missing/invalid.
    This ensures Communication creation succeeds even if thread mapping fails.

    Args:
        message_id: Email Message-ID (with or without < >)
        thread_id: Frappe thread identifier
        comm_name: Optional Communication name for reference
    """
    try:
        # Validate inputs before proceeding
        if not message_id or not thread_id:
            frappe.logger().debug(
                f"[Thread Mapping] Skipping - missing data: message_id={bool(message_id)}, thread_id={bool(thread_id)}"
            )
            return None

        # Clean message_id - ensure proper format
        message_id = _clean_message_id(message_id)

        # After cleaning, check if message_id is still valid
        if not message_id or message_id == '<>':
            frappe.logger().debug(
                f"[Thread Mapping] Skipping - invalid message_id after cleaning"
            )
            return None

        # Check if mapping already exists
        existing = frappe.db.exists(
            "Email Thread Mapping",
            {"message_id": message_id}
        )

        if existing:
            frappe.logger().debug(
                f"[Thread Mapping] Already exists: {message_id} -> {thread_id}"
            )
            return existing

        # Create new mapping
        mapping = frappe.get_doc({
            "doctype": "Email Thread Mapping",
            "message_id": message_id,
            "thread_id": thread_id,
            "communication": comm_name,
            "created_at": now_datetime()
        })

        # Insert with all safety flags
        mapping.insert(ignore_permissions=True, ignore_if_duplicate=True)

        print(
            f"[Thread Mapping] ✅ Stored: {message_id} -> {thread_id} (Doc: {mapping.name})"
        )

        return mapping.name

    except frappe.DuplicateEntryError:
        # Silently ignore duplicates
        frappe.logger().debug(f"[Thread Mapping] Duplicate ignored: {message_id}")
        return None

    except frappe.exceptions.ValidationError as ve:
        # Handle validation errors (e.g., missing required fields)
        # This shouldn't happen now with our pre-checks, but catch it anyway
        frappe.logger().warning(
            f"[Thread Mapping] Validation error, skipping: {str(ve)}"
        )
        return None

    except Exception as e:
        # Don't fail email processing if mapping fails
        # Log the error but continue gracefully
        frappe.logger().error(
            f"[Thread Mapping] Failed to store mapping: {str(e)}"
        )
        return None


def get_thread_id_from_message_id(message_id: str) -> Optional[str]:
    """
    Fast lookup: Get Thread-ID from Message-ID
    
    Args:
        message_id: Email Message-ID (with or without < >)
        
    Returns:
        thread_id if found, None otherwise
    """
    try:
        if not message_id:
            return None
        
        # Clean message_id
        message_id = _clean_message_id(message_id)
        
        # Try mapping table first (fastest - O(1) lookup)
        thread_id = frappe.db.get_value(
            "Email Thread Mapping",
            {"message_id": message_id},
            "thread_id"
        )
        
        if thread_id:
            frappe.logger().debug(
                f"[Thread Mapping] Found via mapping: {message_id} -> {thread_id}"
            )
            return thread_id
        
        # Fallback: Check Communication table (backwards compatibility)
        # This handles old emails before mapping table existed
        comm_data = frappe.db.get_value(
            "Communication",
            {"message_id": message_id},
            ["thread_id", "name"],
            as_dict=True
        )
        
        if comm_data and comm_data.thread_id:
            frappe.logger().debug(
                f"[Thread Mapping] Found via Communication: {message_id} -> {comm_data.thread_id}"
            )
            # NOTE: Thread mapping storage disabled - just return the thread_id
            return comm_data.thread_id
        
        return None
        
    except Exception as e:
        frappe.logger().error(
            f"[Thread Mapping] Lookup failed for {message_id}: {str(e)}"
        )
        return None


def get_or_create_thread_id(message_id: str, in_reply_to: str = None) -> str:
    """
    Get existing thread_id or create new one
    
    Logic:
    1. If in_reply_to exists, lookup its thread_id
    2. If message_id already mapped, return its thread_id
    3. Otherwise, generate new thread_id
    
    Args:
        message_id: Current email's Message-ID
        in_reply_to: Parent email's Message-ID (if reply)
        
    Returns:
        thread_id (existing or new)
    """
    from crm_override.crm_override.email_threading.outbound_email_threading import generate_thread_id
    
    try:
        # Clean inputs
        message_id = _clean_message_id(message_id) if message_id else ''
        in_reply_to = _clean_message_id(in_reply_to) if in_reply_to else ''
        
        # Case 1: This is a reply - inherit parent's thread_id
        if in_reply_to:
            parent_thread_id = get_thread_id_from_message_id(in_reply_to)
            if parent_thread_id:
                print(
                    f"[Thread Mapping] Reply detected: inheriting {parent_thread_id}"
                )
                return parent_thread_id
        
        # Case 2: Check if this message_id already has a thread_id
        if message_id:
            existing_thread_id = get_thread_id_from_message_id(message_id)
            if existing_thread_id:
                return existing_thread_id
        
        # Case 3: New conversation - generate new thread_id
        new_thread_id = generate_thread_id()
        print(
            f"[Thread Mapping] New conversation: generated {new_thread_id}"
        )
        
        return new_thread_id
        
    except Exception as e:
        frappe.logger().error(f"[Thread Mapping] Error: {str(e)}")
        return generate_thread_id()


def cleanup_old_mappings(days: int = 90):
    """
    Clean up old thread mappings to prevent table bloat
    Keep mappings for the last N days only
    
    Should be run as a scheduled job (weekly/monthly)
    """
    try:
        cutoff_date = frappe.utils.add_days(now_datetime(), -days)
        
        result = frappe.db.sql("""
            DELETE FROM `tabEmail Thread Mapping`
            WHERE created_at < %s
        """, cutoff_date)
        
        frappe.db.commit()
        
        count = result[0][0] if result else 0
        print(
            f"[Thread Mapping] Cleaned up {count} mappings older than {days} days"
        )
        
        return {"success": True, "deleted": count}
        
    except Exception as e:
        frappe.log_error(
            title="Thread Mapping Cleanup Failed",
            message=f"Error: {str(e)}\n{frappe.get_traceback()}"
        )
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def debug_thread_mapping(message_id: str):
    """
    Debug helper to check thread mapping for a message_id
    """
    try:
        message_id = _clean_message_id(message_id)
        
        # Check mapping table
        mapping = frappe.db.get_value(
            "Email Thread Mapping",
            {"message_id": message_id},
            ["name", "thread_id", "communication", "created_at"],
            as_dict=True
        )
        
        # Check Communication table
        comm = frappe.db.get_value(
            "Communication",
            {"message_id": message_id},
            ["name", "thread_id", "subject", "sent_or_received", "in_reply_to"],
            as_dict=True
        )
        
        # Find all communications in same thread
        thread_comms = []
        if mapping and mapping.thread_id:
            thread_comms = frappe.get_all(
                "Communication",
                filters={"thread_id": mapping.thread_id},
                fields=["name", "subject", "message_id", "creation", "sent_or_received"],
                order_by="creation asc"
            )
        
        return {
            "message_id": message_id,
            "mapping": mapping,
            "communication": comm,
            "thread_communications": thread_comms,
            "thread_count": len(thread_comms)
        }
        
    except Exception as e:
        return {
            "error": str(e),
            "traceback": frappe.get_traceback()
        }