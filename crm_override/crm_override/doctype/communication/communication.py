# Copyright (c) 2025, Neha and contributors
# For license information, please see license.txt

from frappe.core.doctype.communication.communication import Communication as BaseCommunication
from frappe.core.doctype.communication.communication import update_comment_in_doc


class Communication(BaseCommunication):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		# Backward compatibility for older frappe versions (pre-v12) that had comment_type
		if not hasattr(self, 'comment_type'):
			self.comment_type = None

	def on_update(self):
		# Override to handle missing comment_type attribute in older frappe versions
		try:
			# Call parent's on_update
			update_comment_in_doc(self)

			# Handle parent doc communication update
			from frappe.core.utils import get_parent_doc
			parent = get_parent_doc(self)
			if parent and hasattr(parent, 'on_communication_update') and callable(parent.on_communication_update):
				parent.on_communication_update(self)
			elif parent:
				from frappe.core.doctype.communication.communication import update_parent_document_on_communication
				update_parent_document_on_communication(self)
		except AttributeError as e:
			# If attribute error occurs (like comment_type), handle gracefully
			if 'comment_type' in str(e):
				# Set default and retry
				if not hasattr(self, 'comment_type'):
					self.comment_type = None
				# Try parent method again
				super().on_update()
			else:
				raise
