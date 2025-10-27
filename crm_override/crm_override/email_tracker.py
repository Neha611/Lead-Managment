import frappe
from frappe.utils import now_datetime


@frappe.whitelist(allow_guest=True)
def email_tracker(name):
    """
    Called when email tracking pixel is hit.
    Updates Lead Email Tracker, Communication, and Email Queue.
    Returns a 1x1 GIF.
    """
    try:
        frappe.logger().info(f"[crm_override] Pixel hit for Email Queue: {name}")

        # --- Fetch tracker ---
        tracker = frappe.db.get_value(
            "Lead Email Tracker",
            {"email_queue_status": name},
            ["name", "status", "communication"],
            as_dict=True,
        )

        if tracker:
            if tracker.status != "Opened":
                # Update Lead Email Tracker
                frappe.db.sql("""
                    UPDATE `tabLead Email Tracker`
                    SET status=%s, opened_at=%s, modified=%s
                    WHERE name=%s
                """, ("Opened", now_datetime(), now_datetime(), tracker.name))

                # Update Communication if linked - USE PROPER METHOD
                if tracker.communication:
                    # Get the Communication document
                    comm = frappe.get_doc("Communication", tracker.communication)
                    
                    # Use db_set which triggers notify_update automatically
                    comm.db_set("status", "Opened")
                    comm.db_set("delivery_status", "Opened")
                    print(f"Updated Communication {comm.name} status to Opened")
                    print(comm.as_dict())
                    
                    # CRITICAL: Trigger notify_change for timeline/activity updates
                    comm.notify_change("update")
                    
                    # Also publish realtime event for list view updates
                    frappe.publish_realtime(
                        "list_update",
                        {
                            "doctype": "Communication",
                            "name": tracker.communication,
                            "delivery_status" : "Opened"
                        },
                        after_commit=True
                    )
                    
                    # Update reference document's timeline if exists
                    if comm.reference_doctype and comm.reference_name:
                        print("timeline update")
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

                frappe.db.commit()
                frappe.logger().info(f"[crm_override] Updated tracker {tracker.name} and communication {tracker.communication} -> Opened")
        else:
            frappe.logger().warning(f"[crm_override] No Lead Email Tracker found for Email Queue: {name}")

        # --- Update Email Queue status via Frappe helper ---
        try:
            # Fetch Email Queue doc and update status
            email_queue = frappe.get_doc("Email Queue", name)
            email_queue.db_set("status", "Sent")  # or "Opened"
            frappe.db.commit()
        except Exception as e:
            frappe.log_error(
                title="Email Queue Status Update Error",
                message=f"Failed to update Email Queue status for {name}: {str(e)}\n{frappe.get_traceback()}"
            )


        # --- Return 1x1 transparent GIF ---
        frappe.response.type = "image/gif"
        frappe.response.filecontent = (
            b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00'
            b'\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00'
            b'\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02'
            b'\x44\x01\x00\x3b'
        )

    except Exception as e:
        frappe.log_error(
            title="Email Tracker Error (crm_override)",
            message=f"Error tracking email {name}: {str(e)}\n{frappe.get_traceback()}"
        )

    return ""


def update_tracker_on_email_send(email_queue_name):
    """Update tracker + communication when Email Queue moves to Sent."""
    print("update_tracker_on_email_send called")
    try:
        tracker = frappe.db.get_value(
            "Lead Email Tracker",
            {"email_queue_status": email_queue_name},
            ["name", "communication"],
            as_dict=True,
        )

        if tracker:
            frappe.db.sql("""
                UPDATE `tabLead Email Tracker`
                SET status=%s, last_sent_on=%s, modified=%s
                WHERE name=%s
            """, ("Sent", now_datetime(), now_datetime(), tracker.name))

            if tracker.communication:
                # Get the Communication document
                comm = frappe.get_doc("Communication", tracker.communication)
                
                # Use db_set instead of raw SQL
                comm.db_set("status", "Sent")
                comm.db_set("delivery_status", "Sent")
                print(f"Updated Communication {comm.name} status to Sent")
                print(comm.as_dict())
                # Trigger notify_change for timeline updates
                comm.notify_change("update")
                
                # Publish realtime event
                frappe.publish_realtime(
                    "list_update",
                    {
                        "doctype": "Communication",
                        "name": tracker.communication,
                        "delivery_status" : "Sent"
                    },
                    after_commit=True
                )
                if comm.reference_doctype and comm.reference_name:
                        print("timeline update")
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

            frappe.db.commit()

    except Exception as e:
        frappe.log_error(
            title="Tracker Update on Send Error",
            message=f"Email Queue: {email_queue_name}\nError: {str(e)}\n{frappe.get_traceback()}"
        )


def update_tracker_on_email_error(email_queue_name, error_message):
    """Update tracker + communication when Email Queue enters Error/Expired/Cancelled."""
    try:
        tracker = frappe.db.get_value(
            "Lead Email Tracker",
            {"email_queue_status": email_queue_name},
            ["name", "communication"],
            as_dict=True,
        )

        if tracker:
            frappe.db.sql("""
                UPDATE `tabLead Email Tracker`
                SET status=%s, error_message=%s, last_sent_on=%s, modified=%s
                WHERE name=%s
            """, ("Failed", error_message, now_datetime(), now_datetime(), tracker.name))

            if tracker.communication:
                # Get the Communication document
                comm = frappe.get_doc("Communication", tracker.communication)
                
                # Use db_set instead of raw SQL
                comm.db_set("status", "Failed")
                comm.db_set("delivery_status", "Failed")
                print(f"Updated Communication {comm.name} status to Failed")
                print(comm.as_dict())
                # Trigger notify_change for timeline updates
                comm.notify_change("update")
                
                # Publish realtime event
                frappe.publish_realtime(
                    "list_update",
                    {
                        "doctype": "Communication",
                        "name": tracker.communication,
                        "delivery_status" : "Failed"
                    },
                    after_commit=True
                )

            frappe.db.commit()

    except Exception as e:
        frappe.log_error(
            title="Tracker Update on Error",
            message=f"Email Queue: {email_queue_name}\nError: {str(e)}\n{frappe.get_traceback()}"
        )