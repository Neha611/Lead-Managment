// Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Non Lead', {
	refresh: function(frm) {
		// Simplified controller for Non Lead
		// These are spam/promotional emails, minimal functionality needed

		// Format custom_tags field as badges
		if (frm.doc.custom_tags) {
			frm.fields_dict.custom_tags.$wrapper.find('.control-value').html(
				format_tags_as_badges(frm.doc.custom_tags)
			);
		}
	},
	custom_tags: function(frm) {
		// Re-format tags when field value changes
		if (frm.doc.custom_tags) {
			frm.fields_dict.custom_tags.$wrapper.find('.control-value').html(
				format_tags_as_badges(frm.doc.custom_tags)
			);
		}
	}
});

// Helper function to format tags as badges
function format_tags_as_badges(tags_string) {
	if (!tags_string) return '';

	const tags = tags_string.split(',').map(tag => tag.trim()).filter(tag => tag);

	return tags.map(tag =>
		`<span class="badge badge-pill badge-warning" style="
			background-color: #fff3cd;
			color: #856404;
			padding: 4px 12px;
			margin: 2px 4px;
			border-radius: 12px;
			font-size: 12px;
			font-weight: 500;
			display: inline-block;
		">${tag}</span>`
	).join('');
}
