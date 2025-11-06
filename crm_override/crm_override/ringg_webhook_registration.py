import frappe
import requests

RINGG_API_URL = "https://prod-api.ringg.ai/ca/api/v0/agent/v1"

def get_ringg_settings():
    """Fetch API credentials."""
    settings = frappe.db.get_value(
        "Ringg AI Settings",
        {"agent_name": "AI Call"},
        ["api_key", "agent_id"],
        as_dict=True,
    )
    return settings

@frappe.whitelist()
def register_ringg_webhooks():
    """Register Ringg AI webhooks for testing."""
    try:
        ringg_settings = get_ringg_settings()
        api_key = ringg_settings["api_key"]
        agent_id = ringg_settings["agent_id"]

        # ⚙️ Use a public URL — ngrok / cloudflare tunnel for local testing
        callback_url = "https://constraints-writes-lil-treatment.trycloudflare.com/api/method/crm_override.crm_override.ringg_webhook.ringg_webhook"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "X-API-KEY": api_key,
        }

        body = {
            "operation": "edit_event_subscriptions",
            "agent_id": agent_id,
            "event_subscriptions": [
                {
                    "event_type": "call_completed",
                    "callback_url": callback_url,
                    "headers": {
                        "Authorization": "Bearer webhook-secret",
                        "Content-Type": "application/json"
                    },
                    "method_type": "POST"
                },
                {
                    "event_type": "recording_completed",
                    "callback_url": callback_url,
                    "headers": {
                        "Authorization": "Bearer webhook-secret",
                        "Content-Type": "application/json"
                    },
                    "method_type": "POST"
                },
                {
                    "event_type": "platform_analysis_completed",
                    "callback_url": callback_url,
                    "headers": {
                        "Authorization": "Bearer webhook-secret",
                        "Content-Type": "application/json"
                    },
                    "method_type": "POST"
                },
                {
                    "event_type": "client_analysis_completed",
                    "callback_url": callback_url,
                    "headers": {
                        "Authorization": "Bearer webhook-secret",
                        "Content-Type": "application/json"
                    },
                    "method_type": "POST"
                },
            ]
        }

        response = requests.patch(RINGG_API_URL, headers=headers, json=body, timeout=30)
        if response.status_code == 200:
            frappe.msgprint("✅ Webhook setup successful! Test by making a call.")
        else:
            frappe.log_error(f"Response: {response.text}", "[Ringg] Webhook Registration Failed")
            frappe.throw(f"Webhook setup failed: {response.text}")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "[Ringg] Webhook Registration Error")
        frappe.throw("❌ Error registering webhooks. Check logs for details.")
