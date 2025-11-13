import json
import frappe

def execute():
	"""Fix products field to custom_products in all CRM Lead list view settings"""

	try:
		# Get all CRM View Settings for CRM Lead
		view_settings = frappe.get_all(
			"CRM View Settings",
			filters={"dt": "CRM Lead", "type": "list"},
			fields=["name"]
		)

		if not view_settings:
			print("ℹ️ No CRM Lead list view settings found")
			return

		for setting in view_settings:
			doc = frappe.get_doc("CRM View Settings", setting.name)
			changed = False

			# Update columns - replace "products" with "custom_products"
			if doc.columns:
				columns = json.loads(doc.columns)

				for col in columns:
					if col.get("key") == "products":
						col["key"] = "custom_products"
						# Keep the label as "Products" (user-friendly)
						if col.get("label") == "Products":
							col["label"] = "Products"
						print(f"✅ Renamed products to custom_products in columns for view settings {doc.name}")
						changed = True

				if changed:
					doc.columns = json.dumps(columns)

			# Update rows - replace "products" with "custom_products"
			if doc.rows:
				rows = json.loads(doc.rows)

				if "products" in rows:
					index = rows.index("products")
					rows[index] = "custom_products"
					doc.rows = json.dumps(rows)
					print(f"✅ Renamed products to custom_products in rows for view settings {doc.name}")
					changed = True

			if changed:
				doc.save(ignore_permissions=True)
				print(f"✅ Updated view setting {doc.name}")

		frappe.db.commit()
		print("✅ Successfully fixed products field to custom_products in all CRM Lead list view settings")

	except Exception as e:
		print(f"❌ Error fixing products field in view settings: {str(e)}")
		frappe.log_error(
			title="Failed to fix products field in view settings",
			message=f"Error: {str(e)}\n{frappe.get_traceback()}"
		)
		raise
