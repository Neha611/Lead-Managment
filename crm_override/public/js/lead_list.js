frappe.listview_settings['Lead'] = {
    onload: function(listview) {
        listview.page.add_actions_menu_item(__('Create Segment'), () => {
            const selected = listview.get_checked_items();
            if (!selected.length) {
                frappe.msgprint(__('Please select at least one lead.'));
                return;
            }

            frappe.prompt([
                {
                    label: __('Segment Name'),
                    fieldname: 'segmentname',
                    fieldtype: 'Data',
                    reqd: 1
                },
                {
                    label: __('Description'),
                    fieldname: 'description',
                    fieldtype: 'Small Text'
                }
            ], (values) => {
                frappe.call({
                    method: 'crm_override.crm_override.api.create_lead_segment',
                    args: {
                        segmentname: values.segmentname,
                        lead_names: selected.map(lead => lead.name),
                        description: values.description
                    },
                    callback: function(r) {
                        if (!r.exc) {
                            frappe.msgprint({
                                title: __('Success'),
                                indicator: 'green',
                                message: __('Lead Segment {0} created successfully', [r.message.segmentname])
                            });
                            // Optionally, refresh the list to show any updates
                            listview.refresh();
                        }
                    }
                });
            }, __('Create Lead Segment'), __('Create'));
        });
    }
};