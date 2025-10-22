import frappe
from frappe.utils import now_datetime

# In tracker_sync.py

def sync_email_tracker_status():
    """
    Scheduled job to sync Lead Email Tracker status with Email Queue status.
    Also publishes UI updates for Communication timeline.
    """
    try:
        frappe.logger().info("[Tracker Sync] Starting sync job")
        
        # Get all trackers that need Communication UI updates
        trackers = frappe.db.sql("""
            SELECT 
                t.name as tracker_name,
                t.communication,
                t.status as tracker_status,
                eq.status as queue_status,
                eq.error,
                c.reference_doctype,
                c.reference_name
            FROM `tabLead Email Tracker` t
            INNER JOIN `tabEmail Queue` eq ON t.email_queue_status = eq.name
            LEFT JOIN `tabCommunication` c ON t.communication = c.name
            WHERE t.status IN ('Queued', 'Sent')
            AND t.communication IS NOT NULL
        """, as_dict=True)
        
        frappe.logger().info(f"[Tracker Sync] Found {len(trackers)} trackers to check")
        
        updated_count = 0
        
        for tracker in trackers:
            try:
                new_status = None
                
                # Determine if status needs update
                if tracker.queue_status == "Sent" and tracker.tracker_status == "Queued":
                    new_status = "Sent"
                elif tracker.queue_status in ["Error", "Expired", "Cancelled"]:
                    new_status = "Failed"
                
                if new_status and tracker.communication:
                    # Get Communication document
                    comm = frappe.get_doc("Communication", tracker.communication)
                    
                    # Update Communication
                    comm.db_set("status", new_status)
                    comm.db_set("delivery_status", new_status)
                    
                    # Trigger UI updates
                    comm.notify_change("update")
                    
                    frappe.publish_realtime(
                        "list_update",
                        {
                            "doctype": "Communication",
                            "name": tracker.communication,
                            "delivery_status": new_status
                        },
                        after_commit=True
                    )
                    
                    if tracker.reference_doctype and tracker.reference_name:
                        frappe.publish_realtime(
                            "docinfo_update",
                            {
                                "doc": comm.as_dict(),
                                "key": "communications",
                                "action": "update"
                            },
                            doctype=tracker.reference_doctype,
                            docname=tracker.reference_name,
                            after_commit=True
                        )
                    
                    updated_count += 1
                    frappe.logger().info(f"[Tracker Sync] Updated Communication {tracker.communication} to {new_status}")
                    
            except Exception as e:
                frappe.logger().error(f"[Tracker Sync] Error processing tracker {tracker.get('tracker_name')}: {str(e)}")
                continue
        
        if updated_count > 0:
            frappe.db.commit()
            frappe.logger().info(f"[Tracker Sync] Updated {updated_count} communications")
        else:
            frappe.logger().info("[Tracker Sync] No updates needed")
            
    except Exception as e:
        frappe.log_error(
            title="Tracker Sync Job Error",
            message=f"Error: {str(e)}\n{frappe.get_traceback()}"
        )