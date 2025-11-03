import frappe


# No longer using monkey patch for InboundMail
# Validation now happens in Communication before_insert hook
# This allows ALL emails to be pulled and stored

print("\n" + "="*80)
print("[STARTUP] ✅ Email Validation Module Loaded")
print("[STARTUP] 📧 Validation will occur before linking to CRM Lead")
print("="*80 + "\n")
