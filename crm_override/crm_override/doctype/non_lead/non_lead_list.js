// Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.listview_settings['Non Lead'] = {
	add_fields: ['custom_tags'],
	formatters: {
		custom_tags(value) {
			if (!value) return '';

			const tags = value.split(',').map(tag => tag.trim()).filter(tag => tag);

			return tags.map(tag =>
				`<span class="badge badge-pill badge-warning" style="
					background-color: #fff3cd;
					color: #856404;
					padding: 2px 8px;
					margin: 1px 2px;
					border-radius: 10px;
					font-size: 11px;
					font-weight: 500;
					display: inline-block;
				">${tag}</span>`
			).join('');
		}
	}
};
