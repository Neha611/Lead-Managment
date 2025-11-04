import frappe
import csv
import io
import requests
import json as JSON
from frappe import _
from frappe.utils import add_to_date, now_datetime, get_datetime

RINGG_API_UPLOAD_URL = "https://prod-api.ringg.ai/ca/api/v0/campaign/save"
RINGG_API_CAMPAIGN_URL = "https://prod-api.ringg.ai/ca/api/v0/campaign/start"

# -------------------------------------------------------------------------
# 📧 HELPER: Fetch API credentials
# -------------------------------------------------------------------------

def get_ringg_settings():
    """Fetch Ringg AI Settings (api_key, default_caller_id, default_agent_id)."""
    agent_name="AI Call"
    settings = frappe.db.get_value(
        "Ringg AI Settings",
        {"agent_name": agent_name},
        ["name", "api_key", "agent_id", "caller_id"],
        as_dict=True,
    )
    caller_id_value = None
    if settings.get("caller_id"):
        # Fetch the actual caller_id value from the linked Caller UUID doctype
        caller_doc = frappe.db.get_value(
            "Caller UUID",
            settings.get("caller_id"),
            "caller_uuid",
            as_dict=False
        )
        caller_id_value = caller_doc
    return {
        "api_key": settings.get("api_key"),
        "agent_id": settings.get("agent_id"),   
        "caller_id": caller_id_value,
    }

# -------------------------------------------------------------------------
# 🧩 MAIN: Launch Call Campaign (similar to email campaign)
# -------------------------------------------------------------------------

def launch_call_campaign(campaign_name, recipient_type, recipient_id, start_datetime, call_campaign_name):
    """
    Main function to launch a call campaign.
    Similar structure to launch_campaign() for email campaigns.
    
    Args:
        campaign_name: Name of the Campaign
        recipient_type: "Lead Segment" or "CRM Lead"
        recipient_id: ID of Lead Segment or CRM Lead
        start_datetime: When to start the campaign
        call_campaign_name: Name of the Call Campaign document
    
    Returns:
        dict: {
            'calls_scheduled': int,
            'total_schedules': int,
            'base_time': str
        }
    """
    try:
        # Get campaign schedules
        campaign_doc = frappe.get_doc("Campaign", campaign_name)
        schedules = frappe.get_all(
            "Campaign Call Schedule",
            filters={
                "parent": campaign_name,
                "parenttype": "Campaign",
                "parentfield": "campaign_schedules_call"
            },
            fields=["name", "send_after_days", "send_after_minutes"],
            order_by="send_after_days, send_after_minutes"
        )

        if not schedules:
            frappe.logger().warning(f"[Ringg] No call schedules found for campaign {campaign_name}")
            # Trigger immediately if no schedules
            schedules = [{"send_after_days": 0, "send_after_minutes": 0, "name": "immediate"}]

        # Get leads based on recipient type
        leads = get_leads_for_recipient(recipient_type, recipient_id, campaign_name)

        if not leads:
            frappe.logger().warning(f"[Ringg] No leads found for {recipient_type}: {recipient_id}")
            return {
                'calls_scheduled': 0,
                'total_schedules': 0,
                'base_time': str(start_datetime)
            }

        # Filter leads with valid mobile numbers
        valid_leads = [lead for lead in leads if lead.get('mobile_no')]
        
        if not valid_leads:
            frappe.logger().warning(f"[Ringg] No leads with valid mobile numbers")
            return {
                'calls_scheduled': 0,
                'total_schedules': 0,
                'base_time': str(start_datetime)
            }

        total_calls = 0
        total_schedules = len(schedules)

        # Schedule calls for each schedule
        for schedule in schedules:
            delay_seconds = (
                (schedule.get("send_after_days") or 0) * 86400 +
                (schedule.get("send_after_minutes") or 0) * 60
            )

            scheduled_time = add_to_date(start_datetime, seconds=delay_seconds)

            # Enqueue the bulk call job
            frappe.enqueue(
                method="crm_override.crm_override.broadcast_calls_utils.trigger_bulk_call_job",
                queue='long',
                job_name=f"ringg_bulk_call_{call_campaign_name}_{schedule.get('name')}",
                call_campaign_name=call_campaign_name,
                leads=valid_leads,
                campaign_name=campaign_name,
                schedule_name=schedule.get('name'),
                enqueue_after_commit=True,
                at_front=(delay_seconds == 0),  # Priority for immediate calls
                timeout=600
            )

            total_calls += len(valid_leads)

            frappe.logger().info(
                f"[Ringg] Scheduled {len(valid_leads)} calls for {campaign_name} "
                f"at {scheduled_time} (delay: {delay_seconds}s)"
            )

        return {
            'calls_scheduled': total_calls,
            'total_schedules': total_schedules,
            'base_time': str(start_datetime)
        }

    except Exception as e:
        frappe.log_error(
            message=frappe.get_traceback(),
            title=f"[Ringg] Failed to launch call campaign"
        )
        raise


# -------------------------------------------------------------------------
# 🧩 Get Leads for Recipient
# -------------------------------------------------------------------------

def get_leads_for_recipient(recipient_type, recipient_id, campaign_name):
    """Get leads based on recipient type."""
    leads = []
    
    if recipient_type == "CRM Lead":
        # Single lead
        lead_doc = frappe.get_doc("CRM Lead", recipient_id)
        leads = [{
            "name": lead_doc.name,
            "lead_name": lead_doc.lead_name or lead_doc.name,
            "mobile_no": lead_doc.mobile_no,
            "first_name": lead_doc.first_name,
            "last_name": lead_doc.last_name
        }]
    
    elif recipient_type == "Lead Segment":
        # Get leads from segment
        segment = frappe.get_doc("Lead Segment", recipient_id)
        
        if hasattr(segment, 'leads') and segment.leads:
            lead_names = [item.lead for item in segment.leads]
            
            leads = frappe.get_all(
                "CRM Lead",
                filters={"name": ["in", lead_names]},
                fields=["name", "lead_name", "mobile_no", "first_name", "last_name"]
            )
    
    return leads


# -------------------------------------------------------------------------
# 🧩 Trigger Bulk Call Job (Background Job)
# -------------------------------------------------------------------------

def format_for_ringg(datetime_str):
    """Convert frappe datetime → DD:MM:YYYY, HH:MM format"""
    dt = get_datetime(datetime_str or now_datetime())
    return dt.strftime("%d/%m/%Y, %H:%M")

def trigger_bulk_call_job(call_campaign_name, leads, campaign_name, schedule_name):
    """
    Background job to trigger bulk call via Ringg API.
    This runs in the background worker.
    """
    try:
        call_campaign = frappe.get_doc("Call Campaign", call_campaign_name)
        
        start_time = format_for_ringg(call_campaign.start_date)
        end_time = format_for_ringg(call_campaign.end_date) if call_campaign.end_date else None
        # Fetch API settings
        ringg_settings = get_ringg_settings()
        api_key = ringg_settings["api_key"]
        agent_id = ringg_settings["agent_id"]
        caller_id = ringg_settings["caller_id"]
        # Generate CSV in-memory
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["name", "mobile_no"])
        
        for lead in leads:
            lead_display_name = (
                lead.get("lead_name") or 
                f"{lead.get('first_name') or ''}".strip() or 
                lead.get("name")
            )
            writer.writerow([
                lead_display_name,
                lead.get("mobile_no")   
            ])
        with open("/home/neha/ringg-key", "w", encoding="utf-8", newline="") as f:
            f.write(api_key)
            f.write("\n")
            f.write(agent_id)
            f.write("\n")
            f.write(campaign_name)
            f.write("\n")
            f.write(start_time)
            f.write("\n")
            f.write(end_time or "")
        csv_data = output.getvalue()
        with open("/home/neha/ringg.csv", "w", encoding="utf-8", newline="") as f:
            f.write(csv_data)

        print(campaign_name, start_time, end_time)
        payload = {
            "variables_map": JSON.dumps({
                "callee_name": "name",
                "mobile_number": "mobile_no",
            }),
            "agent_id": agent_id,
            "call_config": JSON.dumps({
                "max_concurrent_calls": 5,
                "retry_count": 1
            }),
            "country_code": "+91",
            "campaign_name": campaign_name,
            "campaign_start_time": start_time,
            "campaign_end_time": end_time
        }
        
        # if caller_id:
        #     payload["caller_id"] = caller_id

        headers = {'X-API-KEY': f"{api_key}"}
        files = {
            "file": csv_data
        }

        # Upload CSV and trigger bulk call
        uploads_response = requests.request(
            "POST",
            url=RINGG_API_UPLOAD_URL,
            data=payload,
            files=files,
            headers=headers,
            timeout=30
        )
        if uploads_response.status_code != 200:
            error_msg = f"Upload failed ({uploads_response.status_code}): {uploads_response.text}"
            frappe.logger().error(f"[Ringg] ❌ {error_msg}")
            call_campaign.db_set('status', 'Error')
            call_campaign.db_set('end_date', now_datetime().date())
            frappe.db.commit()
            frappe.log_error(
                message=f"Status: {uploads_response.status_code}\nResponse: {uploads_response.text}",
                title=f"[Ringg] Upload Error for {campaign_name}"
            )
            return

        frappe.logger().info(
            f"[Ringg] Triggering bulk call for {campaign_name} with {len(leads)} leads"
        )

        call_payload={
            "agent_id": agent_id,
            "list_id": uploads_response.json().get("list_id"),
            "from_numbers":[caller_id]
        }
        with open("/home/neha/listId", "w", encoding="utf-8", newline="") as f:
            f.write(call_payload["list_id"])
        response = requests.post(
            RINGG_API_CAMPAIGN_URL,  
            json=call_payload, 
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            frappe.logger().info(
                f"[Ringg] ✅ Bulk call triggered successfully for {campaign_name}"
            )
            call_campaign.db_set('status', 'In Progress')
            frappe.db.commit()
            
            # Log response for debugging
            try:
                response_data = response.json()
                frappe.logger().info(f"[Ringg] API Response: {response_data}")
            except:
                pass
                
        else:
            error_msg = f"API failed ({response.status_code}): {response.text}"
            frappe.logger().error(f"[Ringg] ❌ {error_msg}")
            call_campaign.db_set('status', 'Error')
            call_campaign.db_set('end_date', now_datetime().date())
            frappe.db.commit()
            
            # Log detailed error
            frappe.log_error(
                message=f"Status: {response.status_code}\nResponse: {response.text}",
                title=f"[Ringg] API Error for {campaign_name}"
            )
            
    except requests.exceptions.Timeout:
        error_msg = "API request timed out"
        frappe.logger().error(f"[Ringg] 🚨 {error_msg}")
        call_campaign.db_set('status', 'Error')
        call_campaign.db_set('end_date', now_datetime().date())
        frappe.db.commit()
        frappe.log_error(
            message=error_msg,
            title=f"[Ringg] Timeout for {campaign_name}"
        )
        
    except Exception as e:
        error_msg = str(e)
        frappe.logger().error(f"[Ringg] 🚨 Exception while triggering API: {error_msg}")
        call_campaign.db_set('status', 'Error')
        call_campaign.db_set('end_date', now_datetime().date())
        frappe.db.commit()
        frappe.log_error(
            message=frappe.get_traceback(),
            title=f"[Ringg] Exception for {campaign_name}"
        )


# -------------------------------------------------------------------------
# 🧩 Webhook (Callback from Ringg)
# -------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def ringg_callback(**kwargs):
    """Webhook endpoint to receive Ringg call status updates."""
    try:
        campaign_name = kwargs.get("campaign_name")
        status = kwargs.get("status")
        lead_id = kwargs.get("lead_id")  # If Ringg sends lead-specific updates

        if not campaign_name:
            frappe.logger().error(f"[Ringg] Invalid webhook payload: {kwargs}")
            return {"status": "error", "message": "Missing campaign_name"}

        frappe.logger().info(f"[Ringg] Webhook received: {campaign_name} → {status}")

        # Find Call Campaign
        call_campaigns = frappe.get_all(
            "Call Campaign",
            filters={"campaign": campaign_name},
            fields=["name", "status"],
            order_by="creation desc",
            limit=1
        )

        if call_campaigns:
            call_campaign = call_campaigns[0]
            
            # Update status if provided
            if status:
                # Map Ringg status to Call Campaign status
                status_mapping = {
                    "completed": "Completed",
                    "failed": "Completed",
                    "in_progress": "In Progress",
                    "scheduled": "Scheduled"
                }
                
                mapped_status = status_mapping.get(status.lower(), "Completed")
                
                frappe.db.set_value(
                    "Call Campaign", 
                    call_campaign.name, 
                    "status", 
                    mapped_status
                )
                
                # Set end date if completed or failed
                if status.lower() in ["completed", "failed"]:
                    frappe.db.set_value(
                        "Call Campaign",
                        call_campaign.name,
                        "end_date",
                        now_datetime().date()
                    )
                
                frappe.db.commit()
                
                frappe.logger().info(
                    f"[Ringg] Updated Call Campaign {call_campaign.name} status to {mapped_status}"
                )

        return {"status": "success", "message": "Webhook processed"}
        
    except Exception as e:
        frappe.log_error(
            message=frappe.get_traceback(),
            title="[Ringg] Webhook Error"
        )
        return {"status": "error", "message": str(e)}


# -------------------------------------------------------------------------
# 🧩 Manual Trigger (Optional)
# -------------------------------------------------------------------------

@frappe.whitelist()
def trigger_call_campaign_manually(call_campaign_name):
    """Manually trigger a call campaign."""
    if not frappe.has_permission("Call Campaign", "write"):
        frappe.throw("Insufficient permissions")
    
    call_campaign = frappe.get_doc("Call Campaign", call_campaign_name)
    
    # Re-launch the campaign
    call_campaign.launch_if_ready()
    
    return {"status": "success", "message": "Call campaign triggered"}


# -------------------------------------------------------------------------
# 🧩 LEGACY: Schedule Call Campaigns (for backward compatibility)
# -------------------------------------------------------------------------

def schedule_call_campaigns(doc, method=None):
    """
    Legacy function - kept for backward compatibility.
    Now Call Campaigns are created manually like Email Campaigns.
    This can be used if you want auto-creation on Campaign submit.
    """
    # Only process if campaign_type is "Call"
    if doc.campaign_type != "Call":
        return
    
    frappe.logger().info(f"[Ringg] Campaign {doc.name} is type 'Call' but auto-creation is disabled. Create Call Campaign manually.")