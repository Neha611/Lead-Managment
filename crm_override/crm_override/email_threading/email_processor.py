"""
Enhanced Email Processor with Explicit Thread Mapping - FIXED VERSION
Properly uses Email Thread Mapping table for lookups
"""

import frappe
from frappe.utils import now_datetime
from frappe.utils.data import get_datetime
from typing import Dict, Optional
from frappe.model.document import Document

from crm_override.crm_override.doctype.email_thread_mapping.email_thread_mapping import (
    store_message_thread_mapping,
    get_thread_id_from_message_id,
)


def process_incoming_email(normalized_email: Dict) -> Optional[str]:
    """
    Process incoming email and create Communication with proper threading
    """
    try:
        # Get clean message IDs (WITH angle brackets per RFC)
        message_id = _clean_message_id(normalized_email.get('message_id', ''))
        in_reply_to_header = _clean_message_id(normalized_email.get('in_reply_to', ''))
        
        print(
            f"[Email Processor] Processing email\n"
            f"  From: {normalized_email.get('from')}\n"
            f"  Subject: {normalized_email.get('subject')}\n"
            f"  Message-ID: {message_id}\n"
            f"  In-Reply-To Header: {in_reply_to_header}"
        )
        
        # Check if already processed
        if message_id:
            existing = frappe.db.exists("Communication", {"message_id": message_id})
            if existing:
                print(f"[Email Processor] Email already processed: {existing}")
                return existing
        
        # FIXED: Find parent Communication using mapping table FIRST
        parent_comm = None
        parent_comm_name = None
        
        if in_reply_to_header:
            # Look up the Communication that has this Message-ID
            parent_comm_name = frappe.db.get_value(
                "Email Thread Mapping",
                {"message_id": in_reply_to_header},
                "communication"
            )
            
            if parent_comm_name:
                # Now get the full Communication details
                parent_comm = frappe.db.get_value(
                    "Communication",
                    parent_comm_name,
                    ["name", "reference_doctype", "reference_name", "thread_id", "message_id"],
                    as_dict=True
                )
                
                if parent_comm:
                    print(
                        f"[Email Processor] ✅ Found parent via mapping: {parent_comm_name}\n"
                        f"  Parent Message-ID: {parent_comm.message_id}\n"
                        f"  Parent Thread-ID: {parent_comm.thread_id}"
                    )
                else:
                    print(f"[Email Processor] ⚠️ Mapping found but Communication {parent_comm_name} not found")
        
        # FALLBACK: Try to find parent by subject line (for emails with/without In-Reply-To)
        # This handles campaign emails that may not have stored mappings or In-Reply-To headers
        if not parent_comm_name:
            reply_subject = normalized_email.get('subject', '').strip()
            original_subject = None
            
            # Extract base subject from "Re:" or use as-is
            if reply_subject.lower().startswith('re:'):
                # Extract original subject
                original_subject = reply_subject[3:].strip()
            else:
                # Use full subject as fallback (may or may not have "Re:" depending on client)
                original_subject = reply_subject
            
            if original_subject:
                print(f"[Email Processor] 🔍 Fallback: Looking for original subject: '{original_subject}'")
                
                # STEP 1: Try exact match first (most precise)
                fallback_comms = frappe.db.get_all(
                    "Communication",
                    filters={
                        "subject": original_subject,
                        "communication_type": "Communication"
                    },
                    fields=["name", "reference_doctype", "reference_name", "thread_id", "message_id", "creation", "sent_or_received"],
                    order_by="sent_or_received desc, creation desc",  # Prefer outbound/sent emails
                    limit=5
                )
                
                # STEP 2: If no exact match, try matching on the "clean" subject
                # (in case recipient's email client added extra markers)
                if not fallback_comms and reply_subject.lower().startswith('re:'):
                    clean_subject = reply_subject[3:].strip()
                    fallback_comms = frappe.db.get_all(
                        "Communication",
                        filters={
                            "subject": clean_subject,
                            "communication_type": "Communication",
                            "sent_or_received": "Sent"  # Campaign emails we sent
                        },
                        fields=["name", "reference_doctype", "reference_name", "thread_id", "message_id", "creation", "sent_or_received"],
                        order_by="creation desc",
                        limit=5
                    )
                
                if fallback_comms:
                    parent_comm = fallback_comms[0]
                    parent_comm_name = parent_comm["name"]
                    print(
                        f"[Email Processor] ✅ Found parent via subject fallback: {parent_comm_name}\n"
                        f"  Subject: {original_subject}\n"
                        f"  Thread-ID: {parent_comm.get('thread_id')}\n"
                        f"  Reference: {parent_comm.get('reference_doctype')}/{parent_comm.get('reference_name')}"
                    )
                else:
                    print(
                        f"[Email Processor] ⚠️ Fallback failed - no parent found by subject\n"
                        f"  Looking for: {original_subject}"
                    )
        
        # Resolve thread_id using parent or References header or generate new
        thread_id = _resolve_thread_id(
            in_reply_to_header, 
            normalized_email.get('references', []),
            parent_comm
        )
        
        # Determine reference document (inherit from parent or find new)
        reference_doctype, reference_name = _resolve_reference_document(
            normalized_email, parent_comm
        )
        
        # Create Communication
        comm = _create_communication(
            normalized_email=normalized_email,
            thread_id=thread_id,
            parent_comm_name=parent_comm_name,
            reference_doctype=reference_doctype,
            reference_name=reference_name,
            message_id=message_id
        )
        
        if not comm:
            frappe.logger().error("[Email Processor] Failed to create Communication")
            return None
        
        # Store mapping immediately (will be part of the same transaction)
        # Only store if we have both message_id and thread_id
        if message_id and thread_id:
            try:
                store_message_thread_mapping(message_id, thread_id, comm.name)
                print(f"[Email Processor] 📝 Queued mapping: {message_id} -> {thread_id} -> {comm.name}")
            except Exception as mapping_error:
                # Don't fail email processing if mapping storage fails
                frappe.logger().warning(
                    f"[Email Processor] Failed to store mapping, continuing: {str(mapping_error)}"
                )
        elif not message_id:
            print(f"[Email Processor] ⚠️ No Message-ID available, skipping thread mapping")

        # Handle attachments
        _attach_files(comm, normalized_email.get('attachments', []))
        
        # IMPORTANT: Commit ONCE at the end to save everything together
        frappe.db.commit()
        
        print(
            f"[Email Processor] ✅ Successfully processed | "
            f"Communication: {comm.name} | Thread: {thread_id} | "
            f"Parent: {parent_comm_name or 'None'}"
        )
        
        return comm.name
        
    except Exception as e:
        frappe.log_error(
            title="Email Processing Failed",
            message=f"From: {normalized_email.get('from')}\n"
                   f"Subject: {normalized_email.get('subject')}\n"
                   f"Error: {str(e)}\n{frappe.get_traceback()}"
        )
        return None


def _clean_message_id(msg_id: str) -> str:
    """
    Clean and standardize Message-ID format
    Always returns format: <id@domain> or empty string
    """
    if not msg_id:
        return ''
    
    msg_id = msg_id.strip()
    
    # Add angle brackets if missing
    if msg_id and not msg_id.startswith('<'):
        msg_id = f'<{msg_id}>'
    if msg_id and not msg_id.endswith('>'):
        msg_id = f'{msg_id}>'
    
    return msg_id


def _resolve_thread_id(
    in_reply_to_header: str, 
    references: list,
    parent_comm: Optional[frappe._dict]
) -> str:
    """
    Resolve thread_id using priority order:
    1. Use parent Communication's thread_id (if found) - MOST RELIABLE
    2. Check In-Reply-To header via mapping table (redundant but safe)
    3. Check References chain via mapping table (fallback)
    4. Generate new thread_id
    
    Args:
        in_reply_to_header: Message-ID from email In-Reply-To header
        references: List of Message-IDs from References header (for fallback)
        parent_comm: Parent Communication doc (if found)
    """
    
    # Method 1: Use parent Communication's thread_id directly (MOST RELIABLE)
    if parent_comm and parent_comm.get('thread_id'):
        print(
            f"[Email Processor] ✅ Using thread_id from parent Communication: {parent_comm.thread_id}"
        )
        return parent_comm.thread_id
    
    # Method 2: Check In-Reply-To header via mapping table (fallback if parent lookup failed)
    if in_reply_to_header:
        thread_id = get_thread_id_from_message_id(in_reply_to_header)
        if thread_id:
            print(
                f"[Email Processor] ✅ Found thread via In-Reply-To mapping: {thread_id}"
            )
            return thread_id
        else:
            frappe.logger().warning(
                f"[Email Processor] ⚠️ In-Reply-To header present but no mapping found: {in_reply_to_header}"
            )
    
    # Method 3: Check References chain (fallback for complex threading)
    # The References header contains all Message-IDs in the conversation
    if references and isinstance(references, list):
        print(f"[Email Processor] Checking {len(references)} references...")
        
        # Check in reverse order (most recent first)
        for ref_id in reversed(references):
            ref_id_clean = _clean_message_id(ref_id)
            if not ref_id_clean:
                continue
            
            thread_id = get_thread_id_from_message_id(ref_id_clean)
            if thread_id:
                print(
                    f"[Email Processor] ✅ Found thread via References mapping: {thread_id}"
                )
                return thread_id
    
    # Method 4: Generate new thread_id
    from crm_override.crm_override.email_threading.outbound_email_threading import generate_thread_id
    new_thread_id = generate_thread_id()
    
    print(
        f"[Email Processor] ✅ New conversation - generated: {new_thread_id}"
    )
    
    return new_thread_id


def _resolve_reference_document(
    normalized_email: Dict, 
    parent_comm: Optional[frappe._dict]
) -> tuple:
    """
    Determine which CRM document this email belongs to
    """
    # Inherit from parent if this is a reply
    if parent_comm:
        ref_doctype = parent_comm.get('reference_doctype')
        ref_name = parent_comm.get('reference_name')
        if ref_doctype and ref_name:
            print(
                f"[Email Processor] Using reference from parent: {ref_doctype}/{ref_name}"
            )
            return (ref_doctype, ref_name)
    
    # Try to find CRM Lead by email
    sender_email = normalized_email.get('from')
    if sender_email:
        lead = frappe.db.get_value("CRM Lead", {"email": sender_email}, "name")
        if lead:
            print(f"[Email Processor] Found CRM Lead: {lead}")
            return ("CRM Lead", lead)
    
    frappe.logger().warning("[Email Processor] No reference document found")
    return (None, None)


def _create_communication(
    normalized_email: Dict,
    thread_id: str,
    parent_comm_name: Optional[str],
    reference_doctype: Optional[str],
    reference_name: Optional[str],
    message_id: str
) -> Optional[Document]:
    """
    Create Communication document from normalized email
    
    IMPORTANT: parent_comm_name is the Communication.name (like "COMM-2024-00001")
               NOT the Message-ID from the email header
    """
    try:
        content = normalized_email.get('body_html') or normalized_email.get('body_text') or ''
        raw_date = normalized_email.get('date') or now_datetime()
        try:
            # If it's a string, parse it
            comm_date = get_datetime(raw_date)
        except Exception:
            comm_date = now_datetime()

        # Strip timezone if present
        if getattr(comm_date, "tzinfo", None) is not None:
            comm_date = comm_date.replace(tzinfo=None)

        # Force into MySQL-compatible format
        comm_date_str = comm_date.strftime("%Y-%m-%d %H:%M:%S")
        
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
            "delivery_status": "Sent",
            "message_id": message_id or None,
            "in_reply_to": parent_comm_name,
            "thread_id": thread_id,
            "email_status": "Open",
            "communication_date": comm_date_str
        }
        
        # Add reference document
        if reference_doctype and reference_name:
            comm_data["reference_doctype"] = reference_doctype
            comm_data["reference_name"] = reference_name
        
        comm = frappe.get_doc(comm_data)
        comm.insert(ignore_permissions=True)
        
        print(
            f"[Email Processor] Created Communication: {comm.name}\n"
            f"  Thread: {thread_id}\n"
            f"  Message-ID: {message_id}\n"
            f"  In-Reply-To (Communication): {parent_comm_name or 'None'}"
        )
        
        return comm
        
    except Exception as e:
        frappe.log_error(
            title="Communication Creation Failed",
            message=f"Subject: {normalized_email.get('subject')}\n"
                   f"Error: {str(e)}\n{frappe.get_traceback()}"
        )
        return None


def _attach_files(comm: Document, attachments: list):
    """Attach files to Communication"""
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
            
        except Exception as e:
            frappe.log_error(
                title="File Attachment Failed",
                message=f"Communication: {comm.name}\n"
                       f"File: {attachment.get('filename')}\n"
                       f"Error: {str(e)}"
            )