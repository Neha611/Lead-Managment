import frappe
from frappe.utils import now_datetime
import re
import quopri
from urllib.parse import quote

def on_email_queue_after_insert(doc, method):
    """
    Hook that runs after Email Queue is inserted.
    Only creates trackers for UI/manual emails, NOT for campaign emails.
    """

    # Handle only CRM Lead-related emails
    if doc.reference_doctype != "CRM Lead":
        frappe.logger().info(f"[Hook] Skipping non-CRM Lead email: {doc.name}")
        return

    # --- Skip if tracker already exists (avoids duplicates for campaign emails)
    if frappe.db.exists("Lead Email Tracker", {"email_queue_status": doc.name}):
        frappe.logger().info(f"[Hook] Tracker already exists for Email Queue: {doc.name}, skipping hook")
        return

    # --- Skip if tracker already exists for same Lead + Communication (race condition check)
    if doc.communication:
        if frappe.db.exists("Lead Email Tracker", {
            "lead": doc.reference_name,
            "communication": doc.communication
        }):
            frappe.logger().info(f"[Hook] Tracker already exists for Lead+Communication, skipping hook")
            return

    try:
        from crm_override.crm_override.broadcast_utils import create_lead_email_tracker

        frappe.logger().info(f"[Hook] Creating tracker for UI email: {doc.name}")

        # Create tracker for UI/manual email
        communication_id = doc.communication
        initial_status = "Queued" if doc.status != "Sent" else "Sent"

        tracker = create_lead_email_tracker(
            lead_name=doc.reference_name,
            email_queue_name=doc.name,
            communication_name=communication_id,
            initial_status=initial_status
        )

        if not tracker:
            frappe.logger().warning(f"[Hook] Failed to create tracker for {doc.name}")
            return

        frappe.logger().info(f"[Hook] Lead Email Tracker created: {tracker.name}")

        # --- Update linked Communication (if available)
        if tracker.communication:
            try:
                comm = frappe.get_doc("Communication", tracker.communication)
                new_status = "Queued" if doc.status != "Sent" else "Sent"

                # Update using db_set to avoid hooks
                comm.db_set("status", new_status, update_modified=False)
                comm.db_set("delivery_status", new_status, update_modified=False)
                frappe.logger().info(f"[Hook] Updated Communication {comm.name} -> {new_status}")

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
                    frappe.logger().info(
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
        frappe.logger().info(f"[Hook] Tracker + Communication committed successfully for {doc.name}")

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
                frappe.logger().info(
                    f"[Hook - Before Save] Email Queue {doc.name} status changing: "
                    f"{old_status} -> {doc.status}"
                )
    except Exception as e:
        frappe.logger().error(f"Error in before_save hook: {str(e)}")


def on_email_queue_on_submit(doc, method):
    """
    Called when Email Queue is submitted (after sending).
    Sync message_id to Communication for proper email threading.
    """
    if doc.communication and doc.message_id:
        try:
            frappe.db.set_value(
                "Communication",
                doc.communication,
                "message_id",
                doc.message_id,
                update_modified=False
            )
            frappe.logger().info(
                f"[Message ID Sync] Communication {doc.communication} updated with message_id: {doc.message_id}"
            )
        except Exception as e:
            frappe.logger().error(f"[Message ID Sync] Failed: {str(e)}")