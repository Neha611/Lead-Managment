import frappe
from frappe.utils import now_datetime
import re
import quopri
from urllib.parse import quote
from crm_override.crm_override.email_threading.outbound_email_threading import ensure_communication_has_thread_id

def on_email_queue_after_insert(doc, method):
    """
    Hook that runs after Email Queue is inserted.
    Creates trackers for ALL CRM Lead emails (UI and campaign).
    """

    # Handle only CRM Lead-related emails
    if doc.reference_doctype != "CRM Lead":
        print(f"[Hook] Skipping non-CRM Lead email: {doc.name}")
        return

    # ✅ ROBUST CHECK: Skip if tracker already exists with this email_queue_name
    existing_tracker = frappe.db.exists("Lead Email Tracker", {"email_queue_status": doc.name})
    if existing_tracker:
        print(f"[Hook] Tracker already exists for Email Queue: {doc.name}, skipping hook")
        return

    try:
        from crm_override.crm_override.broadcast_utils import create_lead_email_tracker

        print(f"[Hook] Creating tracker for email: {doc.name}")

        # Create tracker for email
        communication_id = doc.communication
        campaign_id = getattr(doc, 'email_campaign', None)  # Safely get campaign_id
        initial_status = "Queued" if doc.status != "Sent" else "Sent"

        tracker = create_lead_email_tracker(
            lead_name=doc.reference_name,
            email_queue_name=doc.name,
            communication_name=communication_id,
            initial_status=initial_status,
            campaign_id=campaign_id
        )

        if not tracker:
            frappe.logger().warning(f"[Hook] Failed to create tracker for {doc.name}")
            return

        print(f"[Hook] Lead Email Tracker created: {tracker.name}")

        # --- Update linked Communication (if available)
        if tracker.communication:
            try:
                comm = frappe.get_doc("Communication", tracker.communication)
                new_status = "Queued" if doc.status != "Sent" else "Sent"

                # Update using db_set to avoid hooks
                comm.db_set("status", new_status, update_modified=False)
                comm.db_set("delivery_status", new_status, update_modified=False)

                try:
                    thread_id = ensure_communication_has_thread_id(comm)
                    if not comm.thread_id:
                        comm.db_set("thread_id", thread_id, update_modified=False)
                        print(f"[Threading] Assigned new thread_id {thread_id} to Communication {comm.name}")

                    from email import message_from_string
                    if doc.message:
                        msg = message_from_string(doc.message)
                        msg['X-Frappe-Thread-ID'] = comm.thread_id
                        doc.message = msg.as_string()
                        doc.save(ignore_permissions=True)
                        frappe.db.commit()
                        print(
                            f"[Threading] Added X-Frappe-Thread-ID header ({comm.thread_id}) to Email Queue {doc.name}"
                        )
                    else:
                        frappe.logger().warning(f"[Threading] Email Queue {doc.name} has empty message; skipping header injection")

                except Exception as thread_error:
                    frappe.log_error(
                        title="Thread Header Injection Failed",
                        message=f"Email Queue: {doc.name}\nError: {str(thread_error)}\n{frappe.get_traceback()}"
                    )
                    frappe.logger().error(f"[Threading] Failed to add thread header: {str(thread_error)}")
                
                print(f"[Hook] Updated Communication {comm.name} -> {new_status}")

                # Trigger real-time UI updates
                comm.notify_change("update")

                frappe.publish_realtime(
                    "list_update",
                    {
                        "doctype": "Communication",
                        "name": comm.name,
                        "delivery_status": new_status
                    },
                    after_commit=True
                )

                if comm.reference_doctype and comm.reference_name:
                    frappe.publish_realtime(
                        "docinfo_update",
                        {
                            "doc": comm.as_dict(),
                            "key": "communications",
                            "action": "update"
                        },
                        doctype=comm.reference_doctype,
                        docname=comm.reference_name,
                        after_commit=True
                    )
                    print(
                        f"[Hook] Published docinfo update for {comm.reference_doctype} / {comm.reference_name}"
                    )

            except Exception as comm_error:
                frappe.logger().error(f"[Hook] Failed to update Communication for {tracker.name}: {str(comm_error)}")
                frappe.log_error(
                    title="Hook Communication Update Failed",
                    message=f"Tracker: {tracker.name}\nError: {str(comm_error)}\n{frappe.get_traceback()}"
                )

        # ✅ Atomic commit for both tracker + communication updates
        frappe.db.commit()
        print(f"[Hook] Tracker + Communication committed successfully for {doc.name}")

    except Exception as e:
        frappe.log_error(
            title=f"Failed to create Lead Email Tracker for {doc.name}",
            message=f"{str(e)}\n{frappe.get_traceback()}"
        )
        frappe.logger().error(f"[Hook] Exception while processing {doc.name}: {str(e)}")


def on_email_queue_before_save(doc, method):
    """Called before Email Queue is saved - catches ALL updates"""
    try:
        # Store the old status before it changes
        if not doc.is_new():
            old_status = frappe.db.get_value("Email Queue", doc.name, "status")
            if old_status and old_status != doc.status:
                doc._status_changed = True
                doc._old_status = old_status
                print(
                    f"[Hook - Before Save] Email Queue {doc.name} status changing: "
                    f"{old_status} -> {doc.status}"
                )
    except Exception as e:
        frappe.logger().error(f"Error in before_save hook: {str(e)}")


def on_email_queue_on_submit(doc, method):
    """
    Called when Email Queue is submitted (after sending).
    Sync message_id to Communication and Email Thread Mapping for proper email threading.
    
    This is CRITICAL for campaign email threading!
    """
    try:
        print(f"\n[Email Queue On Submit] Processing Email Queue: {doc.name}")
        print(f"  Communication: {doc.communication}")
        print(f"  Message-ID: {doc.message_id}")
        print(f"  Status: {doc.status}")
        
        # Check if we have both communication and message_id
        if not doc.communication:
            print(f"[Email Queue On Submit] ⚠️ No communication linked, skipping")
            return
        
        if not doc.message_id:
            print(f"[Email Queue On Submit] ⚠️ No message_id in Email Queue, checking if it needs to be generated")
            return
        
        # ✅ Update Communication with the actual message_id that was sent
        comm_data = frappe.db.get_value(
            "Communication",
            doc.communication,
            ["name", "thread_id", "message_id"],
            as_dict=True
        )
        
        if not comm_data:
            print(f"[Email Queue On Submit] ⚠️ Communication {doc.communication} not found")
            return
        
        print(f"[Email Queue On Submit] Found Communication: {comm_data.name}")
        print(f"  Current message_id: {comm_data.message_id}")
        print(f"  Current thread_id: {comm_data.thread_id}")
        
        # Update Communication with the actual message_id if not already set
        if not comm_data.message_id or comm_data.message_id != doc.message_id:
            frappe.db.set_value(
                "Communication",
                doc.communication,
                "message_id",
                doc.message_id,
                update_modified=False
            )
            frappe.db.commit()
            print(f"[Email Queue On Submit] ✅ Updated Communication {doc.communication} with message_id: {doc.message_id}")
        else:
            print(f"[Email Queue On Submit] ℹ️ Communication already has correct message_id")
        
        # NOTE: Thread mapping storage is disabled
        # Just log for debugging
        if comm_data.thread_id:
            print(f"[Email Queue On Submit] Thread ID: {comm_data.thread_id}")
        else:
            print(f"[Email Queue On Submit] ⚠️ Communication has no thread_id")
            
    except Exception as e:
        print(f"[Email Queue On Submit] ❌ ERROR: {str(e)}")
        frappe.log_error(
            title="Email Queue On Submit Failed",
            message=f"Email Queue: {doc.name}\nCommunication: {doc.communication}\nError: {str(e)}\n{frappe.get_traceback()}"
        )