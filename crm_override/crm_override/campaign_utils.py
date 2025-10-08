import frappe
from frappe import _

@frappe.whitelist()
def add_schedule_to_campaign(campaign_name, email_template, send_after_days, send_after_minutes):
    """
    Add an email schedule to a campaign.
    :param campaign_name: Name (ID) of the Campaign
    :param email_template: Name of the Email Template
    :param send_after_days: Number of days after which to send
    :param send_after_minutes: Minutes after which to send
    :return: Success status
    """
    campaign = frappe.get_doc("Campaign", campaign_name)

    # Add the schedule
    campaign.append("campaign_schedules", {
        "email_template": email_template,
        "send_after_days": int(send_after_days),
        "send_after_minutes": send_after_minutes,
    })
    campaign.save(ignore_permissions=True)
    frappe.db.commit()

    return {"success": True, "message": "Schedule added to campaign"}


@frappe.whitelist()
def remove_schedule_from_campaign(campaign_name, schedule_idx):
    """
    Remove an email schedule from a campaign.
    :param campaign_name: Name (ID) of the Campaign
    :param schedule_idx: Index of the schedule to remove
    :return: Success status
    """
    campaign = frappe.get_doc("Campaign", campaign_name)

    # Remove the schedule
    schedule_idx = int(schedule_idx)
    if 0 <= schedule_idx < len(campaign.campaign_schedules):
        campaign.remove(campaign.campaign_schedules[schedule_idx])
        campaign.save(ignore_permissions=True)
        frappe.db.commit()
        return {"success": True, "message": "Schedule removed from campaign"}
    else:
        frappe.throw(_("Invalid schedule index"))
