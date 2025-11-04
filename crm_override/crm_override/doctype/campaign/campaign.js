// Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Campaign", {
	refresh: function (frm) {
		// Keep ERPNext’s original behavior
        erpnext.toggle_naming_series();

        if (frm.is_new()) {
            frm.toggle_display(
                "naming_series",
                frappe.boot.sysdefaults.campaign_naming_by == "Naming Series"
            );
        } else {
            frm.add_custom_button(
                __("View Leads"),
                function () {
                    frappe.route_options = { utm_source: "Campaign", utm_campaign: frm.doc.name };
                    frappe.set_route("List", "Lead");
                },
                "fa fa-list",
                true
            );
        }

        // Apply schedule visibility logic on refresh
        toggle_schedules(frm);
	},

	campaign_type: function (frm) {
        // Apply visibility logic when campaign type changes
        toggle_schedules(frm);
    }
});

// Helper function to toggle schedule tables
function toggle_schedules(frm) {
    const type = frm.doc.campaign_type;

    if (type === "Email") {
        frm.set_df_property("campaign_schedules", "hidden", 0);
        frm.set_df_property("campaign_schedules_call", "hidden", 1);
    } 
    else if (type === "Call") {
        frm.set_df_property("campaign_schedules", "hidden", 1);
        frm.set_df_property("campaign_schedules_call", "hidden", 0);
    } 
    else {
        // Hide both if no campaign type is selected
        frm.set_df_property("campaign_schedules", "hidden", 1);
        frm.set_df_property("campaign_schedules_call", "hidden", 1);
    }
}
