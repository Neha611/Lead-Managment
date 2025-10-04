"""
This module initializes the CRM Override app
"""
import frappe
from frappe import _
import json

def get_context_for_dev():
    """Add CRM Override to the context for dev environment"""
    return {
        'crm_override_config': {
            'enabled': True,
            'version': '1.0.0'
        }
    }

@frappe.whitelist(allow_guest=True)
def get_config():
    """Get CRM Override configuration"""
    return {
        'enabled': True,
        'version': '1.0.0'
    }