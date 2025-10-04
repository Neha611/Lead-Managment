### Lead Segment

Whatsapp Broadcast like feature

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app crm_override
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/crm_override
pre-commit install
```

Testing query -:
```bash
from crm_override.crm_override.api import create_lead_segment, send_email_to_segment
import frappe

# --- 1. Create or replace segment ---
segment_name = "seg1"
lead_names = [
    "CRM-LEAD-2025-00196",    # replace with your actual lead names
    "CRM-LEAD-2025-00197"
]

if frappe.db.exists("Lead Segment", segment_name):
    frappe.delete_doc("Lead Segment", segment_name, ignore_permissions=True)
    frappe.db.commit()
    print(f"🗑️ Old segment '{segment_name}' deleted.")

segment = create_lead_segment(
    segmentname=segment_name,
    lead_names=lead_names,
    description="Test Segment for Broadcast"
)

segment_id = segment.get("name")
print(f"✅ Segment created: {segment_id}")


# --- 2. Send email broadcast ---
print("\n📧 Sending broadcast email...")

responses = send_email_to_segment(
    segment_name=segment_id,
    subject="🚀 Test Broadcast",
    message="""
        <h3>Hello from Frappe!</h3>
        <p>This is a test email broadcast to the seg1 segment.</p>
        <p>Check the Emails tab to verify timeline entry.</p>
    """,
    sender_email="ngneha090@gmail.com"      # must be configured in Email Account
)


# --- 3. Print results and verify communication ---
print("\n📊 Broadcast Results:")
for res in responses:
    print("\n--- Result ---")
    for k, v in res.items():
        print(f"{k}: {v}")

    comm_id = res.get("communication_id")
    if comm_id:
        print("\n🔎 Verifying communication record...")
        try:
            comm = frappe.get_doc("Communication", comm_id)
            print("✅ Communication found:")
            print(f" - Subject: {comm.subject}")
        except Exception as e:
            print(f"❌ Error fetching communication: {str(e)}")

```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit
