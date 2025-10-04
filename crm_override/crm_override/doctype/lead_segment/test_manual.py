import frappe
import sys
import os

# Add the frappe bench path to Python path
bench_path = "/home/neha/frappe/frappe-bench"
sites_path = os.path.join(bench_path, "sites")
apps_path = os.path.join(bench_path, "apps")
sys.path.insert(0, bench_path)
sys.path.insert(0, apps_path)

# Import the create_lead_segment function
from crm_override.crm_override.api import create_lead_segment

# Initialize Frappe
frappe.init(site="library.localhost", sites_path=sites_path)
frappe.connect()

def test_lead_segment():
    try:
        # Create test leads
        lead1 = frappe.get_doc({
            "doctype": "CRM Lead",
            "lead_name": "Test Lead 1",
            "first_name": "Test1",
            "email": "lead1@example.com"
        }).insert(ignore_if_duplicate=True)

        lead2 = frappe.get_doc({
            "doctype": "CRM Lead",
            "lead_name": "Test Lead 2",
            "first_name": "Test2",
            "email": "lead2@example.com"
        }).insert(ignore_if_duplicate=True)

        frappe.db.commit()
        print(f"✓ Created test leads: {lead1.name}, {lead2.name}")

        # Test creating a segment
        from crm_override.crm_override.api import create_lead_segment
        segment = create_lead_segment("Test Segment", [lead1.name, lead2.name], "Test Description")
        print(f"✓ Created segment: {segment['name']}")

        # Verify the segment
        segment_doc = frappe.get_doc('Lead Segment', segment['name'])
        leads_in_segment = [item.lead for item in segment_doc.leads]
        print(f"✓ Leads in segment: {leads_in_segment}")

        # Verify counts
        assert len(leads_in_segment) == 2, "Segment should have exactly 2 leads"
        assert lead1.name in leads_in_segment, "Lead 1 should be in segment"
        assert lead2.name in leads_in_segment, "Lead 2 should be in segment"
        print("✓ All verifications passed")

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        raise

    finally:
        # Clean up
        try:
            print("\nCleaning up test data...")
            frappe.delete_doc("Lead Segment", segment['name'], force=True)
            frappe.delete_doc("CRM Lead", lead1.name, force=True)
            frappe.delete_doc("CRM Lead", lead2.name, force=True)
            frappe.db.commit()
            print("✓ Cleanup complete")
        except Exception as e:
            print(f"❌ Error during cleanup: {str(e)}")

def test_different_scenarios():
    try:
        # Test 1: Create segment with no leads
        print("\nTest 1: Creating segment with no leads")
        try:
            create_lead_segment("Empty Segment", [], "Should fail")
            print("❌ Test 1 failed: Should have raised an error")
        except Exception as e:
            print(f"✓ Test 1 passed: Got expected error: {str(e)}")

        # Test 2: Create segment with single lead
        print("\nTest 2: Creating segment with single lead")
        lead = frappe.get_doc({
            "doctype": "CRM Lead",
            "lead_name": "Single Test Lead",
            "first_name": "Single",
            "email": "single@example.com"
        }).insert(ignore_if_duplicate=True)
        frappe.db.commit()
        
        segment = create_lead_segment("Single Lead Segment", [lead.name], "Test with single lead")
        print(f"✓ Test 2 passed: Created segment with single lead: {segment['name']}")
        
        # Clean up Test 2
        frappe.delete_doc("Lead Segment", segment['name'], force=True)
        frappe.delete_doc("CRM Lead", lead.name, force=True)
        frappe.db.commit()

        # Test 3: Create segment with duplicate leads
        print("\nTest 3: Creating segment with duplicate leads")
        lead = frappe.get_doc({
            "doctype": "CRM Lead",
            "lead_name": "Duplicate Test Lead",
            "first_name": "Duplicate",
            "email": "duplicate@example.com"
        }).insert(ignore_if_duplicate=True)
        frappe.db.commit()
        
        segment = create_lead_segment("Duplicate Lead Segment", [lead.name, lead.name], "Test with duplicate leads")
        segment_doc = frappe.get_doc('Lead Segment', segment['name'])
        assert len(segment_doc.leads) == 1, "Segment should deduplicate leads"
        print("✓ Test 3 passed: Duplicate leads handled correctly")
        
        # Clean up Test 3
        frappe.delete_doc("Lead Segment", segment['name'], force=True)
        frappe.delete_doc("CRM Lead", lead.name, force=True)
        frappe.db.commit()

        print("\n✓ All scenario tests completed successfully")

    except Exception as e:
        print(f"\n❌ Error during scenario tests: {str(e)}")
        raise

if __name__ == "__main__":
    print("Starting Lead Segment tests...\n")
    print("Testing basic functionality:")
    test_lead_segment()
    
    print("\nTesting different scenarios:")
    test_different_scenarios()