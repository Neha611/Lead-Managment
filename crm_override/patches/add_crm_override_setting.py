import frappe

def execute():
    """Add CRM Override to CRM app configuration"""
    app_config = frappe.get_single('CRM Settings')
    if not hasattr(app_config, 'enable_crm_override'):
        frappe.get_doc({
            'doctype': 'Custom Field',
            'dt': 'CRM Settings',
            'fieldname': 'enable_crm_override',
            'label': 'Enable CRM Override',
            'fieldtype': 'Check',
            'insert_after': 'enable_lead_custom_fields',
            'default': '1'
        }).insert()
        
    frappe.db.commit()