import frappe
from frappe.utils import now_datetime

def sync_email_tracker_status():
    """
    Scheduled job to sync Lead Email Tracker status with Email Queue status.
    This ensures trackers are updated even if hooks don't fire.
    Run this every 5-10 minutes via scheduler.
    """
    try:
        frappe.logger().info("[Tracker Sync] Starting sync job")
        
        # Get all trackers that are Queued or Sent but not Opened
        trackers = frappe.get_all(
            "Lead Email Tracker",
            filters={"status": ["in", ["Queued", "Sent"]]},
            fields=["name", "email_queue_status", "status"]
        )
        
        frappe.logger().info(f"[Tracker Sync] Found {len(trackers)} trackers to check")
        
        updated_count = 0
        
        for tracker in trackers:
            try:
                # Get the actual Email Queue status
                email_queue = frappe.db.get_value(
                    "Email Queue",
                    tracker.email_queue_status,
                    ["status", "error"],
                    as_dict=True
                )
                
                if not email_queue:
                    frappe.logger().warning(f"[Tracker Sync] Email Queue {tracker.email_queue_status} not found for tracker {tracker.name}")
                    continue
                
                queue_status = email_queue.get("status")
                
                # Map Email Queue status to Tracker status
                if queue_status == "Sent" and tracker.status == "Queued":
                    frappe.db.set_value(
                        "Lead Email Tracker",
                        tracker.name,
                        {
                            "status": "Sent",
                            "last_sent_on": now_datetime(),
                            "modified": now_datetime()
                        }
                    )
                    updated_count += 1
                    frappe.logger().info(f"[Tracker Sync] Updated {tracker.name} to Sent")
                    
                elif queue_status in ["Error", "Expired", "Cancelled"]:
                    frappe.db.set_value(
                        "Lead Email Tracker",
                        tracker.name,
                        {
                            "status": "Failed",
                            "error_message": email_queue.get("error") or f"Email {queue_status}",
                            "modified": now_datetime()
                        }
                    )
                    updated_count += 1
                    frappe.logger().info(f"[Tracker Sync] Updated {tracker.name} to Failed")
                    
            except Exception as e:
                frappe.logger().error(f"[Tracker Sync] Error processing tracker {tracker.name}: {str(e)}")
                continue
        
        if updated_count > 0:
            frappe.db.commit()
            frappe.logger().info(f"[Tracker Sync] Updated {updated_count} trackers")
        else:
            frappe.logger().info("[Tracker Sync] No updates needed")
            
    except Exception as e:
        frappe.log_error(
            title="Tracker Sync Job Error",
            message=f"Error: {str(e)}\n{frappe.get_traceback()}"
        )