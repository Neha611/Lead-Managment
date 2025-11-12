"""
Simple test to verify settings are loading.
Run: bench --site crm.localhost execute crm_override.test_settings_inline.test
"""

import frappe


def test():
    """Test settings loading."""
    from crm_override.crm_override.email_validator import get_validation_settings

    print("\n" + "="*80)
    print("Testing Custom Email Validator Settings")
    print("="*80)

    result = get_validation_settings()
    custom_prompt = result.get('custom_prompt')

    print(f"\nResult:")
    print(f"  custom_prompt exists: {custom_prompt is not None}")
    print(f"  custom_prompt length: {len(custom_prompt) if custom_prompt else 0}")
    print(f"  First 300 chars: {custom_prompt[:300] if custom_prompt else 'NONE'}")

    print("\n" + "="*80)
    print("Check logs at: sites/crm.localhost/logs/email_validation.log")
    print("Look for: '🔍 FETCHING VALIDATION SETTINGS'")
    print("="*80 + "\n")
