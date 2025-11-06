#!/usr/bin/env python3
"""
Script to import emails from mbox file through Frappe's email pipeline with validation.

Usage:
    cd /path/to/bench
    bench --site your-site.local execute import_mbox_emails.process_mbox --args "['path/to/file.mbox', 'Email Account Name']"

Or run with defaults:
    bench --site your-site.local execute import_mbox_emails.process_mbox
"""

import mailbox
import frappe
from frappe.email.receive import InboundMail


def process_mbox(mbox_path=None, email_account_name=None, limit=None):
	"""
	Process all emails from mbox file through the pull emails pipeline.

	Args:
		mbox_path: Path to .mbox file (default: auto-detect in apps/takeout/)
		email_account_name: Name of Email Account to use (default: first active account)
		limit: Max number of emails to process (default: all)
	"""

	# Set defaults
	if not mbox_path:
		mbox_path = frappe.utils.get_bench_path() + "/apps/takeout/All mail Including Spam and Trash.mbox"

	if not email_account_name:
		# Get first active incoming email account
		accounts = frappe.get_all(
			"Email Account",
			filters={"enable_incoming": 1},
			fields=["name", "email_id"],
			limit=1
		)
		if not accounts:
			frappe.throw("No active Email Account found. Please enable incoming email for an account.")
		email_account_name = accounts[0].name
		print(f"Using Email Account: {email_account_name} ({accounts[0].email_id})")

	# Get email account doc
	email_account = frappe.get_doc("Email Account", email_account_name)

	# Don't auto-create Leads - our validation hook will do it after validation
	append_to_doctype = None

	print(f"Using Email Account: {email_account_name}")
	print(f"Append To: None (Leads will be created after validation)")

	# Open mbox file
	print(f"\n📦 Opening mbox file: {mbox_path}")
	mbox = mailbox.mbox(mbox_path)
	total_emails = len(mbox)
	print(f"📧 Found {total_emails} emails in mbox file")

	if limit:
		total_emails = min(total_emails, limit)
		print(f"⚠️  Processing limit: {limit} emails")

	# Process each email
	processed = 0
	skipped = 0
	errors = 0

	print(f"\n🚀 Starting import...\n")

	for idx, message in enumerate(mbox):
		if limit and idx >= limit:
			break

		try:
			# Convert message to bytes (InboundMail expects bytes)
			raw_message = message.as_bytes()

			# Create InboundMail object (this parses the email)
			inbound_mail = InboundMail(
				content=raw_message,
				email_account=email_account,
				uid=-(idx + 1),  # Use negative UIDs to avoid conflicts
				seen_status=1,  # Mark as seen
				append_to=append_to_doctype  # Pass append_to for proper linking
			)

			# Get email details for logging
			subject = inbound_mail.subject[:50] if inbound_mail.subject else "No Subject"
			sender = inbound_mail.from_email

			print(f"[{idx + 1}/{total_emails}] Processing: {sender} - {subject}")

			# Process through pipeline (creates Communication, triggers validation)
			try:
				communication = inbound_mail.process()
				processed += 1

				# Check if validation ran
				if hasattr(communication, 'flags') and hasattr(communication.flags, 'ai_validation_result'):
					result = communication.flags.ai_validation_result
					print(f"    ✅ Imported | AI Validation: {result}")
				else:
					print(f"    ✅ Imported")

			except Exception as process_error:
				# Email might already exist or be invalid
				error_msg = str(process_error)
				if "SentEmailInInboxError" in error_msg or "same as recipient" in error_msg:
					skipped += 1
					print(f"    ⏭️  Skipped (sent by same account)")
				else:
					errors += 1
					print(f"    ❌ Error: {error_msg[:100]}")

		except Exception as e:
			errors += 1
			print(f"[{idx + 1}/{total_emails}] ❌ Failed to parse email: {str(e)[:100]}")

		# Commit every 10 emails to avoid huge transactions
		if (idx + 1) % 10 == 0:
			frappe.db.commit()
			print(f"    💾 Committed batch")

	# Final commit
	frappe.db.commit()

	# Print summary
	print(f"\n{'='*60}")
	print(f"📊 IMPORT SUMMARY")
	print(f"{'='*60}")
	print(f"Total Emails:     {total_emails}")
	print(f"✅ Processed:     {processed}")
	print(f"⏭️  Skipped:       {skipped}")
	print(f"❌ Errors:        {errors}")
	print(f"{'='*60}\n")

	return {
		"total": total_emails,
		"processed": processed,
		"skipped": skipped,
		"errors": errors
	}


if __name__ == "__main__":
	import sys
	if len(sys.argv) > 1:
		process_mbox(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
	else:
		print("Usage: python import_mbox_emails.py <mbox_path> [email_account_name]")
