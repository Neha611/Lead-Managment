import frappe
from frappe.utils import now_datetime

def update_tracker_on_email_send(email_queue_name):
    """Update tracker + communication when Email Queue moves to Sent."""
    print("update_tracker_on_email_send called")
    try:
        tracker = frappe.db.get_value(
            "Lead Email Tracker",
            {"email_queue_status": email_queue_name},
            ["name", "communication", "email_campaign"],
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
            ["name", "communication", "email_campaign"],
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


def increment_campaign_counter(campaign_name, field_name):
    """Helper function to increment Email Campaign counters."""
    if campaign_name and campaign_name != "None":
        try:
            frappe.db.sql(f"""
                UPDATE `tabEmail Campaign`
                SET {field_name} = {field_name} + 1
                WHERE name = %s
            """, (campaign_name,))
            print(f"[Campaign Counter] Incremented {field_name} for campaign {campaign_name}")
        except Exception as e:
            frappe.log_error(
                title="Campaign Counter Update Error",
                message=f"Campaign: {campaign_name}\nField: {field_name}\nError: {str(e)}"
            )


@frappe.whitelist(allow_guest=True, methods=['GET', 'POST'])
def test_webhook():
    """
    Simple test endpoint to verify link and SendGrid connectivity
    GET: Returns a simple message
    POST: Logs the received data
    """
    if frappe.request.method == 'GET':
        return {
            "status": "success",
            "message": "Webhook endpoint is reachable!",
            "timestamp": str(now_datetime())
        }
    else:
        import json
        try:
            data = frappe.request.data
            frappe.log_error(
                title="SendGrid Webhook Test - Data Received",
                message=f"Raw data:\n{data}\n\nParsed:\n{json.dumps(json.loads(data), indent=2)}"
            )
            return {"status": "success", "message": "Data logged"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


@frappe.whitelist(allow_guest=True, methods=['POST'])
def sendgrid_webhook():
    """
    Receives open/click/delivered events from SendGrid.
    Updates Lead Email Tracker and Communication status.
    """
    try:
        import json
        
        # Get the events from SendGrid
        events = json.loads(frappe.request.data)
        
        print(f"[SendGrid Webhook] Received {len(events)} events")
        
        for event in events:
            event_type = event.get('event')  # 'open', 'click', 'delivered', 'bounce', etc.
            
            print(f"[SendGrid Webhook] Event type: {event_type}")
            print(f"[SendGrid Webhook] Full event data: {json.dumps(event, indent=2)}")
            
            # Get email_queue_name from custom args (unique_args)
            email_queue_name = None
            tracker_name = None
            
            # SendGrid passes custom args in the event
            if event.get('email_queue_name'):
                email_queue_name = event.get('email_queue_name')
            elif event.get('tracker_name'):
                tracker_name = event.get('tracker_name')
            
            # Fallback: try to find by message_id
            if not email_queue_name and not tracker_name:
                # SendGrid includes sg_message_id in format: <message_id>.<filter_id>
                sg_message_id = event.get('sg_message_id', '')
                if sg_message_id:
                    # Remove the filter part (.filter123)
                    message_id = sg_message_id.split('.')[0]
                    
                    # Try to find Email Queue by message_id
                    email_queue_name = frappe.db.get_value(
                        "Email Queue",
                        {"message_id": message_id},
                        "name"
                    )
                    
                    print(f"[SendGrid Webhook] Found Email Queue by message_id: {email_queue_name}")
            
            # Another fallback: find by recipient email + timestamp
            if not email_queue_name and not tracker_name:
                recipient = event.get('email')
                timestamp = event.get('timestamp')
                
                if recipient:
                    # Find recent Email Queue for this recipient
                    email_queue = frappe.db.sql("""
                        SELECT eq.name
                        FROM `tabEmail Queue` eq
                        INNER JOIN `tabEmail Queue Recipient` eqr ON eqr.parent = eq.name
                        WHERE eqr.recipient = %s
                        AND eq.reference_doctype = 'CRM Lead'
                        AND eq.creation > DATE_SUB(NOW(), INTERVAL 7 DAY)
                        ORDER BY eq.creation DESC
                        LIMIT 1
                    """, (recipient,), as_dict=True)
                    
                    if email_queue:
                        email_queue_name = email_queue[0].name
                        print(f"[SendGrid Webhook] Found Email Queue by recipient: {email_queue_name}")
            
            if not email_queue_name and not tracker_name:
                print(f"[SendGrid Webhook] Could not find Email Queue or Tracker for event: {event}")
                continue
            
            print(f"[SendGrid Webhook] Processing {event_type} for Email Queue: {email_queue_name}")
            
            # Find the tracker
            tracker = None
            if tracker_name:
                tracker = frappe.get_doc("Lead Email Tracker", tracker_name)
            elif email_queue_name:
                tracker_data = frappe.db.get_value(
                    "Lead Email Tracker",
                    {"email_queue_status": email_queue_name},
                    ["name", "status", "communication", "email_campaign"],
                    as_dict=True
                )
                if tracker_data:
                    tracker = frappe.get_doc("Lead Email Tracker", tracker_data.name)
            
            if not tracker:
                print(f"[SendGrid Webhook] No tracker found for Email Queue: {email_queue_name}")
                continue
            
            # Update based on event type
            if event_type == 'open':
                # Set opened flag regardless
                tracker.opened = 1
                tracker.opened_at = now_datetime()
                
                # Only update status if currently Sent or Delivered
                # Don't override Clicked status
                if tracker.status in ["Sent", "Delivered", "Queued"]:
                    tracker.status = "Opened"
                
                tracker.save(ignore_permissions=True)
                
                # Increment campaign counter
                increment_campaign_counter(tracker.email_campaign, "opened")
                    
                # Update Communication only if status changed to Opened
                if tracker.communication and tracker.status == "Opened":
                    comm = frappe.get_doc("Communication", tracker.communication)
                    comm.db_set("status", "Opened", update_modified=False)
                    comm.db_set("delivery_status", "Opened", update_modified=False)
                    
                    # Notify UI
                    comm.notify_change("update")
                    frappe.publish_realtime(
                        "list_update",
                        {
                            "doctype": "Communication",
                            "name": tracker.communication,
                            "delivery_status": "Opened"
                        },
                        after_commit=True
                    )
                    
                    # Update timeline
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
                
                print(f"[SendGrid Webhook] Updated tracker {tracker.name} -> Opened")
            
            elif event_type == 'click':
                # Update clicked flag
                tracker.clicked = 1
                if tracker.status not in ["Clicked", "Opened"]:
                    tracker.status = "Clicked"
                tracker.save(ignore_permissions=True)
                
                # Increment campaign counter
                increment_campaign_counter(tracker.email_campaign, "clicked")
                
                if tracker.communication:
                    comm = frappe.get_doc("Communication", tracker.communication)
                    comm.db_set("status", "Clicked", update_modified=False)
                    comm.db_set("delivery_status", "Clicked", update_modified=False)
                    comm.notify_change("update")
                    
                    frappe.publish_realtime(
                        "list_update",
                        {
                            "doctype": "Communication",
                            "name": tracker.communication,
                            "delivery_status": "Clicked"
                        },
                        after_commit=True
                    )
                
                print(f"[SendGrid Webhook] Updated tracker {tracker.name} -> Clicked")
            
            elif event_type == 'delivered':
                # Always set delivered flag
                tracker.delivered = 1
                tracker.last_sent_on = now_datetime()
                
                # Update status only if still Queued (not already Opened/Clicked)
                if tracker.status == "Queued":
                    tracker.status = "Sent"
                
                tracker.save(ignore_permissions=True)
                
                # Increment campaign counter
                increment_campaign_counter(tracker.email_campaign, "delivered")
                
                # Update Communication only if status changed
                if tracker.communication and tracker.status == "Sent":
                    comm = frappe.get_doc("Communication", tracker.communication)
                    comm.db_set("status", "Sent", update_modified=False)
                    comm.db_set("delivery_status", "Sent", update_modified=False)
                    comm.notify_change("update")
                    
                    frappe.publish_realtime(
                        "list_update",
                        {
                            "doctype": "Communication",
                            "name": tracker.communication,
                            "delivery_status": "Sent"
                        },
                        after_commit=True
                    )
                
                print(f"[SendGrid Webhook] Updated tracker {tracker.name} -> Delivered")
            
            elif event_type == 'bounce':
                error_msg = event.get('reason', event.get('type', 'Unknown error'))
                tracker.status = "Bounced"
                tracker.error_message = error_msg
                tracker.save(ignore_permissions=True)
                
                if tracker.communication:
                    comm = frappe.get_doc("Communication", tracker.communication)
                    comm.db_set("status", "Failed", update_modified=False)
                    comm.db_set("delivery_status", "Failed", update_modified=False)
                    comm.notify_change("update")
                    
                    frappe.publish_realtime(
                        "list_update",
                        {
                            "doctype": "Communication",
                            "name": tracker.communication,
                            "delivery_status": "Failed"
                        },
                        after_commit=True
                    )
                
                print(f"[SendGrid Webhook] Updated tracker {tracker.name} -> Bounced")
            
            elif event_type == 'dropped':
                error_msg = event.get('reason', event.get('type', 'Unknown error'))
                tracker.status = "Dropped"
                tracker.error_message = error_msg
                tracker.dropped = 1  # Set dropped flag
                tracker.save(ignore_permissions=True)
                
                # Increment campaign counter
                increment_campaign_counter(tracker.email_campaign, "dropped")
                
                if tracker.communication:
                    comm = frappe.get_doc("Communication", tracker.communication)
                    comm.db_set("status", "Failed", update_modified=False)
                    comm.db_set("delivery_status", "Failed", update_modified=False)
                    comm.notify_change("update")
                    
                    frappe.publish_realtime(
                        "list_update",
                        {
                            "doctype": "Communication",
                            "name": tracker.communication,
                            "delivery_status": "Failed"
                        },
                        after_commit=True
                    )
                
                print(f"[SendGrid Webhook] Updated tracker {tracker.name} -> Dropped")
            
            elif event_type == 'deferred':
                error_msg = event.get('reason', event.get('type', 'Deferred'))
                tracker.status = "Deferred"
                tracker.error_message = error_msg
                tracker.deffered = 1  # Set deferred flag (note: typo in schema 'deffered')
                tracker.save(ignore_permissions=True)
                
                # Increment campaign counter
                increment_campaign_counter(tracker.email_campaign, "deffered")
                
                print(f"[SendGrid Webhook] Updated tracker {tracker.name} -> Deferred")
        
        frappe.db.commit()
        
        # SendGrid expects 200 OK response
        frappe.response.http_status_code = 200
        return {"status": "success", "message": "Events processed"}
        
    except Exception as e:
        frappe.log_error(
            title="SendGrid Webhook Error",
            message=f"Error: {str(e)}\n{frappe.get_traceback()}\nRequest data: {frappe.request.data}"
        )
        frappe.response.http_status_code = 500
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def sync_opens_from_sendgrid():
    """
    Manually sync opens from SendGrid API.
    Can be run as a scheduled job every 5 minutes.
    """
    import requests
    
    # Get API key from Email Account
    api_key = frappe.db.get_value("Email Account", {"default_outgoing": 1}, "password")
    
    # Get recent sent emails (last 24 hours)
    email_queues = frappe.get_all(
        "Email Queue",
        filters={
            "status": "Sent",
            "creation": [">", frappe.utils.add_days(None, -1)]
        },
        fields=["name", "message_id"]
    )
    
    for eq in email_queues:
        if not eq.message_id:
            continue
        
        # Query SendGrid Stats API
        url = f"https://api.sendgrid.com/v3/messages/{eq.message_id}"
        headers = {"Authorization": f"Bearer {api_key}"}
        
        response = requests.get(url, headers=headers)
        if response.ok:
            data = response.json()
            
            # Check if opened
            if data.get("opens_count", 0) > 0:
                # Find tracker
                tracker = frappe.db.get_value(
                    "Lead Email Tracker",
                    {"email_queue_status": eq.name},
                    ["name", "status", "communication", "email_campaign"],
                    as_dict=True
                )
                
                if tracker and tracker.status != "Opened":
                    # Update tracker (same logic as webhook)
                    frappe.db.sql("""
                        UPDATE `tabLead Email Tracker`
                        SET status=%s, opened_at=%s
                        WHERE name=%s
                    """, ("Opened", now_datetime(), tracker.name))
                    
                    # Increment campaign counter
                    increment_campaign_counter(tracker.email_campaign, "opened")
                    
                    if tracker.communication:
                        comm = frappe.get_doc("Communication", tracker.communication)
                        comm.db_set("status", "Opened")
                        comm.db_set("delivery_status", "Opened")
                        comm.notify_change("update")
    
    frappe.db.commit()
    return "Synced"