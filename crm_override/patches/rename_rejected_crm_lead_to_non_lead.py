import frappe


def execute():
	"""
	Rename 'Rejected CRM Lead' DocType to 'Non Lead' and update all references.
	This includes renaming child doctypes and updating database records.
	"""
	# Rename DocTypes
	if frappe.db.exists("DocType", "Rejected CRM Lead"):
		frappe.rename_doc("DocType", "Rejected CRM Lead", "Non Lead", force=True)
	
	if frappe.db.exists("DocType", "Rejected CRM Lead Status"):
		frappe.rename_doc("DocType", "Rejected CRM Lead Status", "Non Lead Status", force=True)
	
	if frappe.db.exists("DocType", "Rejected CRM Lead Source"):
		frappe.rename_doc("DocType", "Rejected CRM Lead Source", "Non Lead Source", force=True)
	
	# Update Communication references
	frappe.db.sql("""
		UPDATE `tabCommunication`
		SET reference_doctype = 'Non Lead'
		WHERE reference_doctype = 'Rejected CRM Lead'
	""")
	
	# Update Comment references
	frappe.db.sql("""
		UPDATE `tabComment`
		SET reference_doctype = 'Non Lead'
		WHERE reference_doctype = 'Rejected CRM Lead'
	""")
	
	# Update File attachments
	frappe.db.sql("""
		UPDATE `tabFile`
		SET attached_to_doctype = 'Non Lead'
		WHERE attached_to_doctype = 'Rejected CRM Lead'
	""")
	
	# Update Version tracking
	frappe.db.sql("""
		UPDATE `tabVersion`
		SET ref_doctype = 'Non Lead'
		WHERE ref_doctype = 'Rejected CRM Lead'
	""")
	
	frappe.db.commit()
	
	print("Successfully renamed Rejected CRM Lead to Non Lead")
