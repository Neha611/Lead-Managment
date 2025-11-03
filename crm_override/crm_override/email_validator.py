import frappe
from google import genai


def get_validation_settings():
	"""
	Get validation settings from Custom Email Validator Settings DocType.
	Falls back to site_config if DocType doesn't exist.

	Returns:
		dict: Settings dictionary
	"""
	try:
		# Clear cache to get fresh settings
		frappe.clear_cache(doctype="Custom Email Validator Settings")

		if frappe.db.exists("DocType", "Custom Email Validator Settings"):
			settings = frappe.get_doc("Custom Email Validator Settings", "Custom Email Validator Settings")

			result = {
				"enabled": getattr(settings, "enable_validation", 1),
				"api_key": getattr(settings, "api_key", None) or frappe.conf.get("gemini_api_key"),
				"model": getattr(settings, "gemini_model", "gemini-2.0-flash-exp") or "gemini-2.0-flash-exp",
				"fail_safe": getattr(settings, "fail_safe_mode", 1),
				"log_invalid": getattr(settings, "log_invalid_emails", 1),
				"custom_prompt": getattr(settings, "validation_prompt", None)
			}

			return result
		else:
			# Fallback to site_config
			return {
				"enabled": frappe.conf.get("enable_email_validation", True),
				"api_key": frappe.conf.get("gemini_api_key"),
				"model": frappe.conf.get("gemini_model", "gemini-2.0-flash-exp"),
				"fail_safe": frappe.conf.get("fail_safe_mode", True),
				"log_invalid": frappe.conf.get("log_invalid_emails", True),
				"custom_prompt": None
			}
	except Exception as e:
		frappe.logger().error(f"[Email Validation] Error getting settings: {str(e)}")
		# Return safe defaults
		return {
			"enabled": True,
			"api_key": frappe.conf.get("gemini_api_key"),
			"model": "gemini-2.0-flash-exp",
			"fail_safe": True,
			"log_invalid": True,
			"custom_prompt": None
		}


def validate_email_with_gemini(raw_email_content, sender_email=None, subject=None):
	"""
	Send raw email to Gemini for validation.

	Args:
		raw_email_content: Raw email message (bytes or str)
		sender_email: Email sender address (optional, for logging)
		subject: Email subject (optional, for logging)

	Returns:
		str: "Valid" or "Invalid"
	"""
	# Initialize logger outside try block so it's available in exception handler
	logger = frappe.logger("email_validation", allow_site=True, file_count=5)

	logger.info("="*80)
	logger.info(f"🔍 Starting validation for: {sender_email}")
	logger.info(f"Subject: {subject}")

	try:
		logger.info("⏳ Calling Gemini API...")
		# Get validation settings
		settings = get_validation_settings()
		print("Calling GEMINI with these settings: ", settings)
		# Get API key from settings
		api_key = settings.get("api_key")

		if not api_key:
			frappe.log_error(
				title="Gemini API Key Missing",
				message="Please set 'gemini_api_key' in Custom Email Validator Settings or site_config.json"
			)
			# Fail-safe: allow email if no API key configured
			return "Valid"

		# Convert bytes to string if needed
		if isinstance(raw_email_content, bytes):
			try:
				email_text = raw_email_content.decode('utf-8', errors='replace')
			except Exception:
				email_text = str(raw_email_content)
		else:
			email_text = str(raw_email_content)

		# Limit content to avoid token limits (keep it small for faster processing)
		# ~500 characters = ~100-150 tokens, enough to determine spam vs legitimate
		email_text = email_text[:2000]

		# Initialize Gemini client
		client = genai.Client(api_key=api_key)
		print("Initialized Gemini client")
		# Use custom prompt if available, otherwise use default
		if settings.get("custom_prompt"):
			# Strip HTML tags from custom prompt (if user used Text Editor field)
			import re
			prompt_template = settings.get("custom_prompt")
			prompt_template = re.sub(r'<[^>]+>', '', prompt_template)  # Remove HTML tags
			prompt_template = prompt_template.strip()

			logger.info(f"Using custom prompt: {prompt_template[:100]}...")
			prompt = f"{prompt_template}\n\nRaw Email Content:\n{email_text}"
		else:
			# Default validation prompt
			prompt = f"""You are an email validation system. Analyze the following raw email and determine if it's legitimate or spam/invalid.

Consider the following criteria for INVALID emails:
- Phishing attempts
- Obvious spam (lottery, prince scams, etc.)
- Malicious content
- Bulk marketing emails with no value
- Suspicious sender patterns

Consider the following criteria for VALID emails:
- Legitimate business emails
- Personal communications
- Transactional emails (receipts, confirmations)
- Professional correspondence
- Automated system notifications from legitimate services

Respond with ONLY ONE WORD: either "Valid" or "Invalid"

Raw Email Content:
{email_text}
"""

		# Get model name from settings
		model_name = settings.get("model", "gemini-2.0-flash-exp")

		# Call Gemini API with optimized settings
		response = client.models.generate_content(
			model=model_name,
			contents=prompt,
			config={
				'temperature': 0,  # Deterministic output
				'max_output_tokens': 50,  # Allow for thinking tokens + actual response
			}
		)
		# print("Received response from Gemini", response)

		# Handle response using structured access
		print("Checking response candidates")
		if not response or not response.candidates or len(response.candidates) == 0:
			logger.error(f"❌ Gemini returned empty response. Response object: {response}")
			raise ValueError("Gemini API returned empty response")
		print("Checked")
		# Access the exact text from structured response
		result = response.candidates[0].content.parts[0].text.strip()
		logger.info(f"🤖 Raw Gemini Response: {result}")
		print(result)
		# Exact match (case-insensitive)
		if result.lower() == "invalid":
			final_result = "Invalid"
		elif result.lower() == "valid":
			final_result = "Valid"
		else:
			# If response is unclear, default based on fail_safe setting
			logger.warning(f"⚠️  Unclear response from Gemini: {result}")
			final_result = "Valid" if settings.get("fail_safe") else "Invalid"

		# Log validation for audit
		logger.info(f"🤖 Gemini Response: {final_result}")
		logger.info(f"Result - Sender: {sender_email}, Subject: {subject}, Decision: {final_result}")

		return final_result

	except Exception as e:
		# Get settings for fail_safe mode
		settings = get_validation_settings()

		# Log error to email_validation log
		logger.error("="*80)
		logger.error(f"❌ VALIDATION ERROR")
		logger.error(f"Sender: {sender_email}")
		logger.error(f"Subject: {subject}")
		logger.error(f"Error: {str(e)}")
		logger.error(f"Traceback: {frappe.get_traceback()}")
		logger.error("="*80)

		# Also log to Error Log for UI visibility
		frappe.log_error(
			title="Gemini Email Validation Failed",
			message=f"Sender: {sender_email}\nSubject: {subject}\nError: {str(e)}\n{frappe.get_traceback()}"
		)

		# Use fail_safe setting to determine behavior
		if settings.get("fail_safe"):
			logger.warning(f"⚠️  Validation failed for {sender_email}, defaulting to Valid (fail-safe mode)")
			return "Valid"
		else:
			logger.warning(f"⚠️  Validation failed for {sender_email}, defaulting to Invalid (strict mode)")
			return "Invalid"


def log_invalid_email(mail_obj):
	"""
	Log invalid emails for audit purposes.

	Args:
		mail_obj: InboundMail object
	"""
	try:
		# Create a simple error log entry
		frappe.log_error(
			title=f"Invalid Email Rejected: {getattr(mail_obj, 'subject', 'No Subject')}",
			message=f"""
Sender: {getattr(mail_obj, 'from_email', 'Unknown')}
Subject: {getattr(mail_obj, 'subject', 'No Subject')}
Date: {getattr(mail_obj, 'date', 'Unknown')}
Email Account: {getattr(mail_obj.email_account, 'name', 'Unknown')}

AI Validation: Invalid

Raw Content Preview:
{str(getattr(mail_obj, 'raw_message', ''))[:1000]}...
			"""
		)

		frappe.logger().info(
			f"[Invalid Email] Logged rejection for: {getattr(mail_obj, 'from_email', 'Unknown')}"
		)

	except Exception as e:
		frappe.logger().error(f"[Email Validation] Failed to log invalid email: {str(e)}")


def is_validation_enabled():
	"""
	Check if email validation is enabled via Custom Email Validator Settings.

	Returns:
		bool: True if validation is enabled
	"""
	try:
		settings = get_validation_settings()
		return settings.get("enabled", True)
	except Exception:
		# Default to enabled if we can't get settings
		return True
