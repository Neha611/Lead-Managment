import frappe
from frappe.utils import now_datetime

def on_email_queue_after_insert(doc, method):
    """Called when Email Queue is created"""
    frappe.logger().info(f"[Hook] Email Queue created: {doc.name}")

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

def on_email_queue_after_save(doc, method):
    """Called after Email Queue is saved - guaranteed to fire"""
    try:
        # Check if status actually changed
        if hasattr(doc, '_status_changed') and doc._status_changed:
            frappe.logger().info(
                f"[Hook - After Save] Email Queue {doc.name} status changed to: {doc.status}"
            )
            
            if doc.status == "Sent":
                update_tracker_status_direct(doc.name, "Sent")
                
            elif doc.status in ["Error", "Expired", "Cancelled"]:
                error_msg = doc.error or f"Email {doc.status}"
                update_tracker_status_direct(doc.name, "Failed", error_msg)
                
    except Exception as e:
        frappe.log_error(
            title="Lead Email Tracker Auto-Update Error (After Save)",
            message=f"Email Queue: {doc.name}\nError: {str(e)}\n{frappe.get_traceback()}"
        )

def on_email_queue_on_update(doc, method):
    """Called on update - additional hook"""
    try:
        if doc.has_value_changed("status"):
            frappe.logger().info(
                f"[Hook - On Update] Email Queue {doc.name} status: {doc.status}"
            )
            
            if doc.status == "Sent":
                update_tracker_status_direct(doc.name, "Sent")
                
            elif doc.status in ["Error", "Expired", "Cancelled"]:
                error_msg = doc.error or f"Email {doc.status}"
                update_tracker_status_direct(doc.name, "Failed", error_msg)
                
    except Exception as e:
        frappe.log_error(
            title="Lead Email Tracker Auto-Update Error (On Update)",
            message=f"Email Queue: {doc.name}\nError: {str(e)}\n{frappe.get_traceback()}"
        )

def on_email_queue_on_change(doc, method):
    """Called on any change - catches background updates"""
    try:
        if doc.has_value_changed("status"):
            frappe.logger().info(
                f"[Hook - On Change] Email Queue {doc.name} status changed to: {doc.status}"
            )
            
            if doc.status == "Sent":
                update_tracker_status_direct(doc.name, "Sent")
                
            elif doc.status in ["Error", "Expired", "Cancelled"]:
                error_msg = doc.error or f"Email {doc.status}"
                update_tracker_status_direct(doc.name, "Failed", error_msg)
                
    except Exception as e:
        frappe.logger().error(f"Error in on_change hook: {str(e)}")


def update_tracker_status_direct(email_queue_name, status, error_message=None):
    """
    Direct SQL update for tracker status - guaranteed to work even in background jobs.
    Uses SQL to avoid ORM overhead and ensure updates happen.
    """
    try:
        # Check if tracker exists
        tracker = frappe.db.sql("""
            SELECT name, status 
            FROM `tabLead Email Tracker` 
            WHERE email_queue_status = %s
            LIMIT 1
        """, (email_queue_name,), as_dict=True)
        
        if not tracker:
            frappe.logger().warning(
                f"[Tracker Update] No tracker found for Email Queue: {email_queue_name}"
            )
            return
        
        tracker_name = tracker[0].name
        old_status = tracker[0].status
        
        # Don't downgrade status (e.g., Opened -> Sent)
        if old_status == "Opened" and status == "Sent":
            frappe.logger().info(
                f"[Tracker Update] Skipping update - tracker already Opened: {tracker_name}"
            )
            return
        
        # Build update query
        if status == "Sent":
            frappe.db.sql("""
                UPDATE `tabLead Email Tracker`
                SET status = %s, 
                    last_sent_on = %s, 
                    modified = %s,
                    modified_by = %s
                WHERE name = %s
            """, ("Sent", now_datetime(), now_datetime(), frappe.session.user, tracker_name))
            
            frappe.logger().info(
                f"[Tracker Update] ✓ Updated {tracker_name}: {old_status} -> Sent"
            )
            
        elif status == "Failed":
            frappe.db.sql("""
                UPDATE `tabLead Email Tracker`
                SET status = %s,
                    error_message = %s,
                    last_sent_on = %s,
                    modified = %s,
                    modified_by = %s
                WHERE name = %s
            """, ("Failed", error_message, now_datetime(), now_datetime(), 
                  frappe.session.user, tracker_name))
            
            frappe.logger().info(
                f"[Tracker Update] ✓ Updated {tracker_name}: {old_status} -> Failed"
            )
        
        # Commit immediately
        frappe.db.commit()
        
    except Exception as e:
        frappe.log_error(
            title="Direct Tracker Update Error",
            message=f"Email Queue: {email_queue_name}\nStatus: {status}\n"
                    f"Error: {str(e)}\n{frappe.get_traceback()}"
        )