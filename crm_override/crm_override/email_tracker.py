import frappe
from frappe.utils import now_datetime

@frappe.whitelist(allow_guest=True)
def email_tracker(name):
    """
    Called when email tracking pixel is hit.
    Updates Lead Email Tracker to 'Opened' and keeps Email Queue/Communication consistent.
    Returns a 1x1 GIF.
    """
    try:
        frappe.logger().info(f"[crm_override] Pixel hit for Email Queue: {name}")

        # Fetch trackers
        trackers = frappe.get_all(
            "Lead Email Tracker",
            filters={"email_queue_status": name},
            fields=["name", "status"]
        )

        if trackers:
            for tr in trackers:
                if tr.get("status") != "Opened":
                    frappe.db.sql("""
                        UPDATE `tabLead Email Tracker`
                        SET `status`=%s, `opened_at`=%s, `modified`=%s
                        WHERE `name`=%s
                    """, ("Opened", now_datetime(), now_datetime(), tr["name"]))
                    frappe.logger().info(f"[crm_override] Updated tracker {tr['name']} -> Opened")
            frappe.db.commit()
        else:
            frappe.logger().warning(f"[crm_override] No Lead Email Tracker found for Email Queue: {name}")

        # Update Email Queue status via frappe internal helper
        try:
            from frappe.email.doctype.email_queue.email_queue import update_email_status
            update_email_status(name)
        except Exception as e:
            frappe.log_error(
                title="Email Queue Status Update Error",
                message=f"Failed to call update_email_status for {name}: {str(e)}\n{frappe.get_traceback()}"
            )

        # Return 1x1 transparent GIF
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
    """
    Update tracker when Email Queue moves to Sent.
    Uses direct SQL to avoid permission issues.
    """
    try:
        trackers = frappe.get_all(
            "Lead Email Tracker",
            filters={"email_queue_status": email_queue_name},
            fields=["name", "status"]
        )

        for tr in trackers:
            frappe.db.sql("""
                UPDATE `tabLead Email Tracker`
                SET `status`=%s, `last_sent_on`=%s, `modified`=%s
                WHERE `name`=%s
            """, ("Sent", now_datetime(), now_datetime(), tr["name"]))
        frappe.db.commit()

    except Exception as e:
        frappe.log_error(
            title="Tracker Update on Send Error",
            message=f"Email Queue: {email_queue_name}\nError: {str(e)}\n{frappe.get_traceback()}"
        )


def update_tracker_on_email_error(email_queue_name, error_message):
    """
    Update tracker when Email Queue enters Error/Expired/Cancelled.
    """
    try:
        trackers = frappe.get_all(
            "Lead Email Tracker",
            filters={"email_queue_status": email_queue_name},
            fields=["name"]
        )

        for tr in trackers:
            frappe.db.sql("""
                UPDATE `tabLead Email Tracker`
                SET `status`=%s, `error_message`=%s, `last_sent_on`=%s, `modified`=%s
                WHERE `name`=%s
            """, ("Failed", error_message, now_datetime(), now_datetime(), tr["name"]))
        frappe.db.commit()

    except Exception as e:
        frappe.log_error(
            title="Tracker Update on Error",
            message=f"Email Queue: {email_queue_name}\nError: {str(e)}\n{frappe.get_traceback()}"
        )
