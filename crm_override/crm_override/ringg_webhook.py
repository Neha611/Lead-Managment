import frappe
import json
from frappe.utils import now_datetime

@frappe.whitelist(allow_guest=True)
def ringg_webhook():
    """
    Webhook endpoint to receive payloads from Ringg AI.
    Handles all four webhook types:
    1. call_completed
    2. recording_completed
    3. platform_analysis_completed
    4. client_analysis_completed
    """
    try:
        # Get the incoming payload
        payload = frappe.local.form_dict
        
        # If payload is empty, try reading from request data
        if not payload:
            payload = json.loads(frappe.request.get_data())
        
        # Log the entire payload for debugging
        frappe.logger().info(f"[Ringg Webhook] Received payload: {json.dumps(payload, indent=2)}")
        
        # Print payload to console (visible in bench console)
        print("=" * 80)
        print("[RINGG WEBHOOK] RECEIVED PAYLOAD:")
        print(json.dumps(payload, indent=2))
        print("=" * 80)
        
        # Extract event type
        event_type = payload.get("event_type")
        
        if not event_type:
            frappe.log_error(
                message=f"No event_type in payload: {json.dumps(payload)}",
                title="[Ringg] Missing Event Type"
            )
            return {"status": "error", "message": "Missing event_type"}
        
        # Route to appropriate handler based on event type
        if event_type == "call_completed":
            frappe.logger().info(f"[Ringg Webhook] Processing call_completed event")
            handle_call_completed(payload)
        elif event_type == "recording_completed":
            handle_recording_completed(payload)
        elif event_type == "platform_analysis_completed":
            handle_platform_analysis_completed(payload)
        elif event_type == "client_analysis_completed":
            handle_client_analysis_completed(payload)
        else:
            frappe.log_error(
                message=f"Unknown event_type: {event_type}\nPayload: {json.dumps(payload)}",
                title="[Ringg] Unknown Event Type"
            )
        
        frappe.db.commit()
        return {"status": "success", "event_type": event_type}
        
    except Exception as e:
        frappe.log_error(
            message=frappe.get_traceback(),
            title="[Ringg] Webhook Processing Error"
        )
        return {"status": "error", "message": str(e)}


def handle_call_completed(payload):
    """
    Handle call_completed webhook.
    Creates/updates CRM Call Log with call completion details.
    
    Payload structure:
    {
        "event_type": "call_completed",
        "call_id": "string",
        "agent_id": "string",
        "workspace_id": "string",
        "call_duration": number,
        "call_type": "outbound/inbound",
        "status": "completed/failed/retry",
        "to_number": "string",
        "from_number": "string",
        "transcript": [...],
        "custom_args_values": {},
        "retry_count": number
    }
    """
    try:
        print("\n[CALL COMPLETED WEBHOOK]")
        print(json.dumps(payload, indent=2))
        
        call_id = payload.get("call_id")
        
        if not call_id:
            frappe.log_error(
                message=f"Missing call_id in payload: {json.dumps(payload)}",
                title="[Ringg] Call Completed - Missing Call ID"
            )
            return
        
        # Get or create call log
        if frappe.db.exists("CRM Call Log", call_id):
            call_log = frappe.get_doc("CRM Call Log", call_id)
            print(f"[Ringg] Updating existing call log: {call_id}")
        else:
            call_log = frappe.new_doc("CRM Call Log")
            call_log.id = call_id
        #set lead name using reference doctype
        callee_name=payload.get('custom_args_values').get("callee_name")
        lead=frappe.db.get_value("CRM Lead", {"mobile_no": payload.get("to_number"), "first_name":callee_name})
        if lead:
            call_log.reference_doctype="CRM Lead"
            call_log.reference_docname=lead
            print(f"[Ringg] Linked Call Log to CRM Lead: {lead}")
        # Update basic call details
        call_log.telephony_medium = "Manual"
        call_log.type = "Outgoing" if payload.get("call_type") == "outbound" else "Incoming"
        call_log.from_number = payload.get("from_number", "")
        call_log.to = payload.get("to_number", "")
        call_log.agent_id = payload.get("agent_id", "")
        print(f"[Ringg] Call Type: {call_log.type}, From: {call_log.from_number}, To: {call_log.to}")
        # Update call status
        status_mapping = {
            "completed": "Completed",
            "failed": "Failed",
            "retry": "Queued"
        }
        call_log.status = status_mapping.get(payload.get("status", "").lower(), "Completed")
        print(f"[Ringg] Call Status set to: {call_log.status}")
        # Update duration (convert to HH:MM:SS format for Duration field)
        call_duration = payload.get("call_duration", 0)
        if call_duration:
            call_log.duration = call_duration  # Store in seconds
        print(f"[Ringg] Call Duration set to: {call_log.duration} seconds")
        # Update transcript
        transcript = payload.get("transcript", [])
        if transcript:
            transcript_text = ""
            for turn in transcript:
                if "bot" in turn.keys():
                    message = turn.get("bot", "")
                    transcript_text += f"BOT: {message}\n"
                elif "user" in turn.keys():
                    message = turn.get("user", "")
                    transcript_text += f"USER: {message}\n"
            call_log.transcript = transcript_text.strip()
        print(f"[Ringg] Transcript updated.")
        # Update timestamps
        if not call_log.start_time:
            call_log.start_time = now_datetime()
        call_log.end_time = now_datetime()
        print(f"[Ringg] Start Time: {call_log.start_time}, End Time: {call_log.end_time}")
        # Save call log
        if call_log.is_new():
            print(f"[Ringg] Inserting new call log into database.")
            call_log.insert(ignore_permissions=True)
            print(f"[Ringg] New call log inserted with ID: {call_log.name}")
        else:
            print(f"[Ringg] Saving updates to existing call log.")
            call_log.save(ignore_permissions=True)
        frappe.db.commit()
        print(f"[Ringg] Successfully processed call_completed for: {call_id}")
        
    except Exception as e:
        frappe.log_error(
            message=f"Error in handle_call_completed:\n{frappe.get_traceback()}\nPayload: {json.dumps(payload)}",
            title="[Ringg] Call Completed Handler Error"
        )


def handle_recording_completed(payload):
    """
    Handle recording_completed webhook.
    Updates call log with recording URL and duration.
    
    Payload structure:
    {
        "event_type": "recording_completed",
        "call_id": "string",
        "agent_id": "string",
        "workspace_id": "string",
        "recording_url": "string",
        "recording_duration": number
    }
    """
    try:
        print("\n[RECORDING COMPLETED WEBHOOK]")
        print(json.dumps(payload, indent=2))
        
        call_id = payload.get("call_id")
        
        if not call_id:
            frappe.log_error(
                message=f"Missing call_id in payload: {json.dumps(payload)}",
                title="[Ringg] Recording Completed - Missing Call ID"
            )
            return
        
        # Get or create call log
        if not frappe.db.exists("CRM Call Log", call_id):
            frappe.logger().warning(f"[Ringg] Call log {call_id} not found, creating new one")
            call_log = frappe.new_doc("CRM Call Log")
            call_log.id = call_id
            call_log.telephony_medium = "Manual"
            call_log.type = "Outgoing"
            call_log.status = "Completed"
        else:
            call_log = frappe.get_doc("CRM Call Log", call_id)
        
        # Update recording details
        call_log.recording_url = payload.get("recording_url", "")
        call_log.recording_duration = str(payload.get("recording_duration", 0))
        
        # Save call log
        if call_log.is_new():
            call_log.insert(ignore_permissions=True)
        else:
            call_log.save(ignore_permissions=True)
        
        frappe.logger().info(f"[Ringg] Successfully processed recording_completed for: {call_id}")
        
    except Exception as e:
        frappe.log_error(
            message=f"Error in handle_recording_completed:\n{frappe.get_traceback()}\nPayload: {json.dumps(payload)}",
            title="[Ringg] Recording Completed Handler Error"
        )


def handle_platform_analysis_completed(payload):
    """
    Handle platform_analysis_completed webhook.
    Updates call log with Ringg AI's built-in analysis.
    
    Payload structure:
    {
        "event_type": "platform_analysis_completed",
        "call_id": "string",
        "agent_id": "string",
        "workspace_id": "string",
        "analysis_data": {
            "key_points": [...],
            "summary": "string",
            "classification": "string",
            "sentiment": "positive/negative/neutral",
            "call_disconnect_reason": "string"
            action_itmes: [...],
        }
    }
    """
    try:
        print("\n[PLATFORM ANALYSIS COMPLETED WEBHOOK]")
        print(json.dumps(payload, indent=2))
        
        call_id = payload.get("call_id")
        
        if not call_id:
            frappe.log_error(
                message=f"Missing call_id in payload: {json.dumps(payload)}",
                title="[Ringg] Platform Analysis - Missing Call ID"
            )
            return
        
        # Get or create call log
        if not frappe.db.exists("CRM Call Log", call_id):
            frappe.logger().warning(f"[Ringg] Call log {call_id} not found for platform analysis")
            call_log = frappe.new_doc("CRM Call Log")
            call_log.id = call_id
            call_log.telephony_medium = "Manual"
            call_log.type = "Outgoing"
            call_log.status = "Completed"
        else:
            call_log = frappe.get_doc("CRM Call Log", call_id)
        
        # Extract analysis data
        analysis_data = payload.get("analysis_data", {})
        
        # Update summary
        if analysis_data.get("summary"):
            call_log.summary = analysis_data["summary"]
        
        # Update key points
        if analysis_data.get("key_points"):
            key_points = analysis_data["key_points"]
            if isinstance(key_points, list):
                call_log.key_points = "\n".join([f"• {point}" for point in key_points])
            else:
                call_log.key_points = str(key_points)
        
        # Update classification
        if analysis_data.get("classification"):
            call_log.classification = analysis_data["classification"]
        
        # Update sentiment
        if analysis_data.get("sentiment"):
            call_log.sentiment = analysis_data["sentiment"]
        
        # Update call disconnect reason
        if analysis_data.get("call_disconnect_reason"):
            call_log.call_disconnect_reason = analysis_data["call_disconnect_reason"]

        # Update action items
        if analysis_data.get("action_items"):
            action_items = analysis_data["action_items"]
            if isinstance(action_items, list):
                call_log.action_items = "\n".join([f"• {item}" for item in action_items])
            else:
                call_log.action_items = str(action_items)
        
        # Save call log
        if call_log.is_new():
            call_log.insert(ignore_permissions=True)
        else:
            call_log.save(ignore_permissions=True)
        
        frappe.logger().info(f"[Ringg] Successfully processed platform_analysis_completed for: {call_id}")
        
    except Exception as e:
        frappe.log_error(
            message=f"Error in handle_platform_analysis_completed:\n{frappe.get_traceback()}\nPayload: {json.dumps(payload)}",
            title="[Ringg] Platform Analysis Handler Error"
        )


def handle_client_analysis_completed(payload):
    """
    Handle client_analysis_completed webhook.
    Updates call log with custom analysis based on configured prompts.
    
    Payload structure:
    {
        "event_type": "client_analysis_completed",
        "call_id": "string",
        "agent_id": "string",
        "workspace_id": "string",
        "analysis_data": {
            "lead_quality": "string",
            "intent_score": "string",
            "next_action": "string",
            "purchase_probability": "string",
            "customer_segment": "string",
            "satisfaction_score": "string"
        }
    }
    """
    try:
        print("\n[CLIENT ANALYSIS COMPLETED WEBHOOK]")
        print(json.dumps(payload, indent=2))
        
        call_id = payload.get("call_id")
        
        if not call_id:
            frappe.log_error(
                message=f"Missing call_id in payload: {json.dumps(payload)}",
                title="[Ringg] Client Analysis - Missing Call ID"
            )
            return
        
        # Get or create call log
        if not frappe.db.exists("CRM Call Log", call_id):
            frappe.logger().warning(f"[Ringg] Call log {call_id} not found for client analysis")
            call_log = frappe.new_doc("CRM Call Log")
            call_log.id = call_id
            call_log.telephony_medium = "Manual"
            call_log.type = "Outgoing"
            call_log.status = "Completed"
        else:
            call_log = frappe.get_doc("CRM Call Log", call_id)
        
        # Extract analysis data
        analysis_data = payload.get("analysis_data", {})
        
        # Clear existing client analysis entries
        call_log.client_analysis = []
        
        # Map analysis data to Call Analysis Data child table fields
        analysis_row = call_log.append("client_analysis", {})
        
        # Map each field from the payload to the child table
        if analysis_data.get("lead_quality"):
            analysis_row.lead_quality = str(analysis_data["lead_quality"])
        
        if analysis_data.get("intent_score"):
            analysis_row.intent_score = str(analysis_data["intent_score"])
        
        if analysis_data.get("next_action"):
            analysis_row.next_action = str(analysis_data["next_action"])
        
        if analysis_data.get("purchase_probability"):
            analysis_row.purchase_probability = str(analysis_data["purchase_probability"])
        
        if analysis_data.get("customer_segment"):
            analysis_row.customer_segment = str(analysis_data["customer_segment"])
        
        if analysis_data.get("satisfaction_score"):
            analysis_row.satisfaction_score = str(analysis_data["satisfaction_score"])
        
        # Log the mapped data
        frappe.logger().info(f"[Ringg] Mapped client analysis data: {analysis_data}")
        
        # Save call log
        if call_log.is_new():
            call_log.insert(ignore_permissions=True)
        else:
            call_log.save(ignore_permissions=True)
        
        frappe.logger().info(f"[Ringg] Successfully processed client_analysis_completed for: {call_id}")
        
    except Exception as e:
        frappe.log_error(
            message=f"Error in handle_client_analysis_completed:\n{frappe.get_traceback()}\nPayload: {json.dumps(payload)}",
            title="[Ringg] Client Analysis Handler Error"
        )
