import json
import frappe

def execute():
	"""Add description field to Lead Segment list view settings"""

	try:
		# Get all CRM View Settings for Lead Segment
		view_settings = frappe.get_all(
			"CRM View Settings",
			filters={"dt": "Lead Segment", "type": "list"},
			fields=["name"]
		)

		if not view_settings:
			print("ℹ️ No Lead Segment list view settings found, will use default")
			return

		for setting in view_settings:
			doc = frappe.get_doc("CRM View Settings", setting.name)
			changed = False

			# Add description to columns if not already present
			if doc.columns:
				columns = json.loads(doc.columns)

				# Check if description already exists
				has_description = any(col.get("key") == "description" for col in columns)

				if not has_description:
					# Add description column after segmentname
					description_col = {
						"key": "description",
						"label": "Description",
						"type": "Text Editor",
						"width": "200px"
					}

					# Find position after segmentname
					insert_pos = 1
					for idx, col in enumerate(columns):
						if col.get("key") == "segmentname":
							insert_pos = idx + 1
							break

					columns.insert(insert_pos, description_col)
					doc.columns = json.dumps(columns)
					changed = True
					print(f"✅ Added description to columns for view settings {doc.name}")

			# Add description to rows if not already present
			if doc.rows:
				rows = json.loads(doc.rows)

				if "description" not in rows:
					# Add description after segmentname
					if "segmentname" in rows:
						insert_pos = rows.index("segmentname") + 1
						rows.insert(insert_pos, "description")
					else:
						rows.append("description")

					doc.rows = json.dumps(rows)
					changed = True
					print(f"✅ Added description to rows for view settings {doc.name}")

			if changed:
				doc.save(ignore_permissions=True)
				print(f"✅ Updated view setting {doc.name}")

		frappe.db.commit()
		print("✅ Successfully added description field to Lead Segment list view settings")

	except Exception as e:
		print(f"❌ Error adding description to Lead Segment list view: {str(e)}")
		frappe.log_error(
			title="Failed to add description to Lead Segment list view",
			message=f"Error: {str(e)}\n{frappe.get_traceback()}"
		)
		raise
