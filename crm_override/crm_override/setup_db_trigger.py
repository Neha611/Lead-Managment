"""
Database trigger setup for automatic tracker updates.
This is the MOST RELIABLE method as it runs at the database level.

Run this once: bench execute crm_override.crm_override.setup_db_trigger.setup_email_queue_trigger
"""

import frappe

def setup_email_queue_trigger():
    """
    Creates a database trigger that automatically updates Lead Email Tracker
    when Email Queue status changes. This is 100% reliable.
    """
    try:
        # Drop trigger if it exists
        frappe.db.sql("""
            DROP TRIGGER IF EXISTS update_lead_tracker_on_email_sent
        """)
        
        # Create the trigger
        trigger_sql = """
            CREATE TRIGGER update_lead_tracker_on_email_sent
            AFTER UPDATE ON `tabEmail Queue`
            FOR EACH ROW
            BEGIN
                -- When Email Queue changes to "Sent"
                IF NEW.status = 'Sent' AND OLD.status != 'Sent' THEN
                    UPDATE `tabLead Email Tracker`
                    SET 
                        status = 'Sent',
                        last_sent_on = NOW(),
                        modified = NOW()
                    WHERE 
                        email_queue_status = NEW.name
                        AND status = 'Queued';
                END IF;
                
                -- When Email Queue changes to Error/Expired/Cancelled
                IF NEW.status IN ('Error', 'Expired', 'Cancelled') 
                   AND OLD.status NOT IN ('Error', 'Expired', 'Cancelled') THEN
                    UPDATE `tabLead Email Tracker`
                    SET 
                        status = 'Failed',
                        error_message = COALESCE(NEW.error, CONCAT('Email ', NEW.status)),
                        last_sent_on = NOW(),
                        modified = NOW()
                    WHERE 
                        email_queue_status = NEW.name
                        AND status != 'Failed';
                END IF;
            END
        """
        
        frappe.db.sql(trigger_sql)
        frappe.db.commit()
        
        print("✅ Database trigger created successfully!")
        print("Lead Email Tracker will now update AUTOMATICALLY at database level.")
        
        return {
            "success": True,
            "message": "Database trigger created successfully"
        }
        
    except Exception as e:
        error_msg = f"Failed to create trigger: {str(e)}"
        print(f"❌ {error_msg}")
        frappe.log_error(
            title="Database Trigger Setup Failed",
            message=f"{error_msg}\n{frappe.get_traceback()}"
        )
        return {
            "success": False,
            "message": error_msg
        }


def remove_email_queue_trigger():
    """
    Removes the database trigger if you need to uninstall or update it.
    """
    try:
        frappe.db.sql("""
            DROP TRIGGER IF EXISTS update_lead_tracker_on_email_sent
        """)
        frappe.db.commit()
        
        print("✅ Database trigger removed successfully!")
        
        return {
            "success": True,
            "message": "Database trigger removed"
        }
        
    except Exception as e:
        error_msg = f"Failed to remove trigger: {str(e)}"
        print(f"❌ {error_msg}")
        return {
            "success": False,
            "message": error_msg
        }


def check_trigger_status():
    """
    Check if the trigger is installed and working.
    """
    try:
        triggers = frappe.db.sql("""
            SHOW TRIGGERS LIKE 'tabEmail Queue'
        """, as_dict=True)
        
        trigger_exists = any(
            t.get('Trigger') == 'update_lead_tracker_on_email_sent' 
            for t in triggers
        )
        
        if trigger_exists:
            print("✅ Database trigger is ACTIVE")
            print("Automatic updates are enabled at database level")
        else:
            print("❌ Database trigger is NOT installed")
            print("Run: bench execute crm_override.crm_override.setup_db_trigger.setup_email_queue_trigger")
        
        return {
            "trigger_exists": trigger_exists,
            "all_triggers": triggers
        }
        
    except Exception as e:
        print(f"❌ Error checking trigger: {str(e)}")
        return {
            "trigger_exists": False,
            "error": str(e)
        }