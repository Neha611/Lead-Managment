#!/usr/bin/env python3
"""
Quick test script to verify Custom Email Validator Settings are being loaded correctly.

Usage:
    cd /path/to/bench
    bench --site crm.localhost execute crm_override.test_settings.test_custom_prompt_loading
"""

import frappe
from crm_override.crm_override.email_validator import get_validation_settings
import re


def test_custom_prompt_loading():
    """Test if custom prompt is being loaded from settings."""

    print("\n" + "="*80)
    print("TESTING CUSTOM EMAIL VALIDATOR SETTINGS")
    print("="*80)

    # Test 1: Direct database query
    print("\n1. Direct Database Query:")
    try:
        db_value = frappe.db.get_single_value("Custom Email Validator Settings", "validation_prompt")
        print(f"   ✅ DB query successful")
        print(f"   Type: {type(db_value)}")
        print(f"   Length: {len(db_value) if db_value else 0} chars")
        print(f"   First 300 chars: {db_value[:300] if db_value else 'EMPTY'}")

        if db_value:
            # Strip HTML
            cleaned = re.sub(r'<[^>]+>', '', db_value).strip()
            print(f"\n   After HTML stripping:")
            print(f"   Length: {len(cleaned)} chars")
            print(f"   Content: {cleaned[:300] if cleaned else 'EMPTY'}")
    except Exception as e:
        print(f"   ❌ Error: {str(e)}")

    # Test 2: Using get_doc
    print("\n2. Using frappe.get_doc:")
    try:
        settings = frappe.get_doc("Custom Email Validator Settings", "Custom Email Validator Settings")
        prompt = getattr(settings, "validation_prompt", None)
        print(f"   ✅ get_doc successful")
        print(f"   Type: {type(prompt)}")
        print(f"   Length: {len(prompt) if prompt else 0} chars")
        print(f"   First 300 chars: {prompt[:300] if prompt else 'EMPTY'}")
    except Exception as e:
        print(f"   ❌ Error: {str(e)}")

    # Test 3: Using get_validation_settings()
    print("\n3. Using get_validation_settings():")
    try:
        settings = get_validation_settings()
        custom_prompt = settings.get("custom_prompt")
        print(f"   ✅ Function returned successfully")
        print(f"   custom_prompt key exists: {('custom_prompt' in settings)}")
        print(f"   Type: {type(custom_prompt)}")
        print(f"   Length: {len(custom_prompt) if custom_prompt else 0} chars")
        print(f"   First 300 chars: {custom_prompt[:300] if custom_prompt else 'EMPTY'}")

        if custom_prompt:
            # Strip HTML
            cleaned = re.sub(r'<[^>]+>', '', custom_prompt).strip()
            print(f"\n   After HTML stripping:")
            print(f"   Length: {len(cleaned)} chars")
            print(f"   Content: {cleaned[:300] if cleaned else 'EMPTY'}")

            if cleaned:
                print(f"\n   ✅✅✅ CUSTOM PROMPT WILL BE USED")
            else:
                print(f"\n   ❌ Custom prompt empty after HTML stripping, DEFAULT will be used")
        else:
            print(f"\n   ❌ No custom prompt found, DEFAULT will be used")

    except Exception as e:
        print(f"   ❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()

    print("\n" + "="*80)
    print("TEST COMPLETE")
    print("="*80 + "\n")

    # Now check the logs
    print("📋 Check logs at: sites/crm.localhost/logs/email_validation.log")
    print("🔍 Look for: '🔍 FETCHING VALIDATION SETTINGS' section\n")
