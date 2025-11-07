import frappe
from google import genai


def get_validation_settings():
	"""
	Get validation settings from Custom Email Validator Settings DocType.
	Falls back to site_config if DocType doesn't exist.

	Returns:
		dict: Settings dictionary
	"""
	logger = frappe.logger("email_validation", allow_site=True, file_count=5)

	try:
		logger.info("="*80)
		logger.info("🔍 FETCHING VALIDATION SETTINGS")
		logger.info("="*80)

		# Clear cache to get fresh settings
		frappe.clear_cache(doctype="Custom Email Validator Settings")
		logger.info("✅ Cache cleared for Custom Email Validator Settings")

		doctype_exists = frappe.db.exists("DocType", "Custom Email Validator Settings")
		logger.info(f"📋 DocType exists: {doctype_exists}")

		if doctype_exists:
			logger.info("🔄 Fetching settings document...")
			settings = frappe.get_doc("Custom Email Validator Settings", "Custom Email Validator Settings")
			logger.info(f"✅ Settings document loaded: {settings.name}")

			# Get decrypted API key using get_decrypted_password() for Password field
			api_key = frappe.utils.password.get_decrypted_password(
				"Custom Email Validator Settings",
				"Custom Email Validator Settings",
				"gemini_api_key"
			) or frappe.conf.get("gemini_api_key")
			logger.info(f"🔑 API key retrieved: {'Present' if api_key else 'Missing'}")

			# Get validation_prompt field with detailed logging
			validation_prompt_value = getattr(settings, "validation_prompt", None)
			logger.info(f"📝 validation_prompt field:")
			logger.info(f"   - Type: {type(validation_prompt_value)}")
			logger.info(f"   - Is None: {validation_prompt_value is None}")
			logger.info(f"   - Length: {len(validation_prompt_value) if validation_prompt_value else 0}")
			logger.info(f"   - First 300 chars: {validation_prompt_value[:300] if validation_prompt_value else 'EMPTY/NONE'}")

			# Try alternative field access methods
			try:
				dict_value = settings.as_dict().get("validation_prompt")
				logger.info(f"📝 validation_prompt (via as_dict): {dict_value[:300] if dict_value else 'EMPTY/NONE'}")
			except Exception as e:
				logger.error(f"❌ Error getting validation_prompt via as_dict: {str(e)}")

			# Try direct database query
			try:
				db_value = frappe.db.get_single_value("Custom Email Validator Settings", "validation_prompt")
				logger.info(f"📝 validation_prompt (direct DB query):")
				logger.info(f"   - Type: {type(db_value)}")
				logger.info(f"   - Length: {len(db_value) if db_value else 0}")
				logger.info(f"   - First 300 chars: {db_value[:300] if db_value else 'EMPTY/NONE'}")

				# Use DB value if getattr failed
				if not validation_prompt_value and db_value:
					logger.warning("⚠️ getattr returned None but DB has value! Using DB value.")
					validation_prompt_value = db_value
			except Exception as e:
				logger.error(f"❌ Error getting validation_prompt from DB: {str(e)}")

			result = {
				"enabled": getattr(settings, "enable_validation", 1),
				"api_key": api_key,
				"model": getattr(settings, "gemini_model", "gemini-2.0-flash-exp") or "gemini-2.0-flash-exp",
				"fail_safe": getattr(settings, "fail_safe_mode", 1),
				"log_invalid": getattr(settings, "log_invalid_emails", 1),
				"custom_prompt": validation_prompt_value
			}

			logger.info(f"✅ Final result - custom_prompt: {result['custom_prompt'][:200] if result.get('custom_prompt') else 'NONE/EMPTY'}")
			logger.info("="*80)
			return result
		else:
			# Fallback to site_config
			logger.warning("⚠️ DocType doesn't exist, falling back to site_config")
			logger.info("="*80)
			return {
				"enabled": frappe.conf.get("enable_email_validation", True),
				"api_key": frappe.conf.get("gemini_api_key"),
				"model": frappe.conf.get("gemini_model", "gemini-2.0-flash-exp"),
				"fail_safe": frappe.conf.get("fail_safe_mode", True),
				"log_invalid": frappe.conf.get("log_invalid_emails", True),
				"custom_prompt": None
			}
	except Exception as e:
		logger.error("="*80)
		logger.error(f"❌ ERROR IN get_validation_settings: {str(e)}")
		logger.error(f"Traceback: {frappe.get_traceback()}")
		logger.error("="*80)
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
	Send raw email to Gemini for validation with structured output.

	Args:
		raw_email_content: Raw email message (bytes or str)
		sender_email: Email sender address (optional, for logging)
		subject: Email subject (optional, for logging)

	Returns:
		dict: {
			"validity": "Valid" or "Invalid",
			"tags": ["tag1", "tag2", ...],
			"reason": "Explanation text"
		}
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
		# Get API key from settings
		api_key = settings.get("api_key")

		if not api_key:
			frappe.log_error(
				title="Gemini API Key Missing",
				message="Please set 'gemini_api_key' in Custom Email Validator Settings or site_config.json"
			)
			# Fail-safe: allow email if no API key configured
			return {"validity": "Valid", "tags": ["No API Key"], "reason": "API key not configured, defaulting to Valid"}

		# Convert bytes to string if needed
		if isinstance(raw_email_content, bytes):
			try:
				email_text = raw_email_content.decode('utf-8', errors='replace')
			except Exception:
				email_text = str(raw_email_content)
		else:
			email_text = str(raw_email_content)


		# Initialize Gemini client
		client = genai.Client(api_key=api_key)

		# Use custom prompt if available, otherwise use default
		custom_prompt_raw = settings.get("custom_prompt")
		logger.info(f"🔍 Custom prompt retrieved from settings: {custom_prompt_raw[:200] if custom_prompt_raw else 'None'}...")

		if custom_prompt_raw and custom_prompt_raw.strip():
			# Strip HTML tags from custom prompt (if user used Text Editor field)
			import re
			prompt_template = re.sub(r'<[^>]+>', '', custom_prompt_raw)  # Remove HTML tags
			prompt_template = prompt_template.strip()

			if prompt_template:
				logger.info(f"✅ Using CUSTOM prompt (length: {len(prompt_template)} chars)")
				logger.info(f"   First 200 chars: {prompt_template[:200]}...")
				prompt = f"{prompt_template}\n\nRaw Email Content:\n{email_text}"
			else:
				logger.warning("⚠️ Custom prompt became empty after HTML stripping, using DEFAULT prompt")
				custom_prompt_raw = None  # Force fallback to default

		if not custom_prompt_raw or not custom_prompt_raw.strip():
			# Default validation prompt
			logger.info(f"📄 Using DEFAULT hardcoded prompt")
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

You must respond with a structured JSON object containing:
- "validity": Either "Valid" or "Invalid"
- "tags": Array of relevant tags describing the email (e.g., country, product type, business category)
- "reason": Brief explanation of your decision

Raw Email Content:
{email_text}
"""

		# Get model name from settings
		model_name = settings.get("model", "gemini-2.0-flash-exp")

		# Define schema for structured output
		schema = {
			"type": "object",
			"properties": {
				"validity": {
					"type": "string",
					"enum": ["Valid", "Invalid"],
					"description": "Whether the email is valid or invalid"
				},
				"tags": {
					"type": "array",
					"items": {"type": "string"},
					"description": "Relevant tags describing the email content"
				},
				"reason": {
					"type": "string",
					"description": "Brief explanation for the validity decision"
				}
			},
			"required": ["validity", "tags", "reason"]
		}

		# Call Gemini API with structured output
		response = client.models.generate_content(
			model=model_name,
			contents=prompt,
			config={
				'temperature': 0,  # Deterministic output
				'max_output_tokens': 1500,
				'response_mime_type': 'application/json',
				'response_schema': schema
			}
		)
		logger.info(f"✅ Received response from Gemini: {response}")

		# Handle response using structured access
		if not response or not response.candidates or len(response.candidates) == 0:
			logger.error(f"❌ Gemini returned empty response. Response object: {response}")
			raise ValueError("Gemini API returned empty response")

		# Access the exact text from structured response
		result_text = response.candidates[0].content.parts[0].text.strip()
		logger.info(f"🤖 Raw Gemini Response: {result_text}")

		# Parse JSON response
		import json
		try:
			result = json.loads(result_text)
		except json.JSONDecodeError as e:
			logger.error(f"❌ Failed to parse JSON response: {result_text}")
			raise ValueError(f"Invalid JSON response from Gemini: {str(e)}")

		# Validate response structure
		if "validity" not in result:
			logger.warning(f"⚠️  Missing 'validity' field in response: {result}")
			result["validity"] = "Valid" if settings.get("fail_safe") else "Invalid"

		if "tags" not in result:
			result["tags"] = []

		if "reason" not in result:
			result["reason"] = "No reason provided"

		# Normalize validity value
		result["validity"] = result["validity"].capitalize()
		if result["validity"] not in ["Valid", "Invalid"]:
			logger.warning(f"⚠️  Unexpected validity value: {result['validity']}")
			result["validity"] = "Valid" if settings.get("fail_safe") else "Invalid"

		# Log validation for audit
		logger.info(f"🤖 Gemini Response: {result}")
		logger.info(f"Result - Sender: {sender_email}, Subject: {subject}")
		logger.info(f"  Validity: {result['validity']}")
		logger.info(f"  Tags: {result['tags']}")
		logger.info(f"  Reason: {result['reason']}")

		return result

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
		# logger.error(f"For settings: {settings}")
		logger.error("="*80)

		# Also log to Error Log for UI visibility
		frappe.log_error(
			title="Gemini Email Validation Failed",
			message=f"Sender: {sender_email}\nSubject: {subject}\nError: {str(e)}\n{frappe.get_traceback()}"
		)

		# Use fail_safe setting to determine behavior
		validity = "Valid" if settings.get("fail_safe") else "Invalid"
		logger.warning(f"⚠️  Validation failed for {sender_email}, defaulting to {validity}")

		return {
			"validity": validity,
			"tags": ["Error"],
			"reason": f"Validation failed: {str(e)}"
		}


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
