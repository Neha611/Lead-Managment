import frappe
from frappe import _

@frappe.whitelist()
def add_schedule_to_campaign(campaign_name, email_template, send_after_days, send_after_minutes):
    """
    Add an email schedule to a campaign.
    """
    campaign = frappe.get_doc("Campaign", campaign_name)

    campaign.append("campaign_schedules", {
        "email_template": email_template,
        "send_after_days": int(send_after_days),
        "send_after_minutes": int(send_after_minutes),
    })
    campaign.save(ignore_permissions=True)
    frappe.db.commit()

    return {"success": True, "message": "Email schedule added to campaign"}


@frappe.whitelist()
def remove_schedule_from_campaign(campaign_name, schedule_idx):
    """
    Remove an email schedule from a campaign.
    """
    campaign = frappe.get_doc("Campaign", campaign_name)

    schedule_idx = int(schedule_idx)
    if 0 <= schedule_idx < len(campaign.campaign_schedules):
        campaign.remove(campaign.campaign_schedules[schedule_idx])
        campaign.save(ignore_permissions=True)
        frappe.db.commit()
        return {"success": True, "message": "Email schedule removed from campaign"}
    else:
        frappe.throw(_("Invalid email schedule index"))


# ---------------------------------------------------------------------------
# ✅ NEW METHODS for CALL SCHEDULES
# ---------------------------------------------------------------------------

@frappe.whitelist()
def add_call_schedule_to_campaign(campaign_name, send_after_days, send_after_minutes):
    """
    Add a call schedule to a campaign.
    :param campaign_name: Name (ID) of the Campaign
    :param send_after_days: Number of days after which to schedule the call
    :param send_after_minutes: Minutes after which to schedule the call
    :return: Success status
    """
    campaign = frappe.get_doc("Campaign", campaign_name)

    # Append to the call schedule child table
    campaign.append("campaign_schedules_call", {
        "send_after_days": int(send_after_days),
        "send_after_minutes": int(send_after_minutes),
    })

    campaign.save(ignore_permissions=True)
    frappe.db.commit()

    return {"success": True, "message": "Call schedule added to campaign"}


@frappe.whitelist()
def remove_call_schedule_from_campaign(campaign_name, schedule_idx):
    """
    Remove a call schedule from a campaign.
    :param campaign_name: Name (ID) of the Campaign
    :param schedule_idx: Index of the schedule to remove
    :return: Success status
    """
    campaign = frappe.get_doc("Campaign", campaign_name)

    schedule_idx = int(schedule_idx)
    if 0 <= schedule_idx < len(campaign.campaign_schedules_call):
        campaign.remove(campaign.campaign_schedules_call[schedule_idx])
        campaign.save(ignore_permissions=True)
        frappe.db.commit()
        return {"success": True, "message": "Call schedule removed from campaign"}
    else:
        frappe.throw(_("Invalid call schedule index"))
