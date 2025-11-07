import frappe

def execute():
    """Add custom_tags field to CRM Lead if not exists"""
    try:
        # Check if custom_tags field exists
        frappe.reload_doctype("CRM Lead")
        
        # Add custom_tags to default list view if not present
        meta = frappe.get_meta("CRM Lead")
        default_fields = [df.fieldname for df in meta.fields]
        
        if "custom_tags" not in default_fields:
            # Add custom_tags field
            lead_doc = frappe.get_doc("DocType", "CRM Lead")
            lead_doc.append("fields", {
                "fieldname": "custom_tags",
                "fieldtype": "Small Text",
                "label": "Tags",
                "description": "AI-generated tags from email validation",
                "in_standard_filter": 1,
                "in_list_view": 1,
                "in_global_search": 1
            })
            lead_doc.save()

        # Update the default list configuration in CRM Lead controller
        # Get correct path for CRM Lead doctype
        controller_path = "/crm/fcrm/doctype/crm_lead/crm_lead.py"
        with open(controller_path, 'r') as f:
            content = f.read()

        if '"custom_tags"' not in content:
            # Add custom_tags to default list data if not present
            content = content.replace(
                '"modified",\n            "_assign",\n            "image"',
                '"modified",\n            "_assign",\n            "image",\n            "custom_tags"'
            )
            with open(controller_path, 'w') as f:
                f.write(content)

        frappe.clear_cache(doctype="CRM Lead")
        print("✅ Successfully added custom_tags field to CRM Lead")
        
    except Exception as e:
        print(f"❌ Error adding custom_tags field: {str(e)}")
        raise