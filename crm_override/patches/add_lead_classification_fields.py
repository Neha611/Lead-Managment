import frappe

def execute():
    """Add products, market, and lead_type fields to CRM Lead if not exists"""
    try:
        # Reload the doctype to get the latest schema
        frappe.reload_doctype("CRM Lead")

        # Get the CRM Lead DocType document
        lead_doc = frappe.get_doc("DocType", "CRM Lead")

        # Get existing field names
        existing_fields = [df.fieldname for df in lead_doc.fields]

        fields_to_add = []

        # Define the new fields
        if "products" not in existing_fields:
            fields_to_add.append({
                "fieldname": "products",
                "fieldtype": "Small Text",
                "label": "Products",
                "description": "Products of interest identified from email validation",
                "in_standard_filter": 1,
                "in_list_view": 0,
                "in_global_search": 1
            })

        if "market" not in existing_fields:
            fields_to_add.append({
                "fieldname": "market",
                "fieldtype": "Data",
                "label": "Market",
                "description": "Target market/industry identified from email validation",
                "in_standard_filter": 1,
                "in_list_view": 0,
                "in_global_search": 1
            })

        if "lead_type" not in existing_fields:
            fields_to_add.append({
                "fieldname": "lead_type",
                "fieldtype": "Data",
                "label": "Lead Type",
                "description": "Classification of lead type from AI validation",
                "default": "Unclassified",
                "in_standard_filter": 1,
                "in_list_view": 1,
                "in_global_search": 1
            })

        # Add the fields to the DocType
        for field in fields_to_add:
            lead_doc.append("fields", field)
            print(f"✅ Adding field: {field['fieldname']}")

        if fields_to_add:
            lead_doc.save()
            frappe.clear_cache(doctype="CRM Lead")
            print(f"✅ Successfully added {len(fields_to_add)} field(s) to CRM Lead")
        else:
            print("ℹ️ All fields already exist in CRM Lead")

    except Exception as e:
        print(f"❌ Error adding classification fields: {str(e)}")
        raise
