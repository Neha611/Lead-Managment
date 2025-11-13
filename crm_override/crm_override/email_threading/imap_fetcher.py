"""
IMAP Email Fetcher
Scheduled task that fetches unseen emails from IMAP server
"""

import frappe
from frappe.utils import now_datetime
import imaplib
import base64
from crm_override.crm_override.email_threading.email_utils import normalize_imap_email
from crm_override.crm_override.email_threading.email_processor import process_incoming_email


def fetch_imap_emails():
    """
    Scheduled function to fetch emails from IMAP server.
    Should be configured in hooks.py as a scheduled job.
    """
    try:
        print("[IMAP Fetcher] Starting email fetch")
        
        # Get IMAP configuration
        imap_config = get_imap_config()
        
        if not imap_config:
            frappe.logger().error("[IMAP Fetcher] IMAP configuration not found")
            return
        
        # Connect to IMAP server
        mail = connect_imap(imap_config)
        
        if not mail:
            return
        
        # Select inbox
        mail.select('INBOX')
        
        # Search for unseen emails
        status, message_ids = mail.search(None, 'UNSEEN')
        
        if status != 'OK':
            frappe.logger().error("[IMAP Fetcher] Failed to search emails")
            return
        
        # Process each email
        email_ids = message_ids[0].split()
        processed_count = 0
        
        print(f"[IMAP Fetcher] Found {len(email_ids)} unseen emails")
        
        for email_id in email_ids:
            try:
                # Fetch email
                status, msg_data = mail.fetch(email_id, '(RFC822)')
                
                if status != 'OK':
                    continue
                
                # Parse email
                raw_email = msg_data[0][1]
                
                # Normalize email
                normalized_email = normalize_imap_email(raw_email)
                
                if not normalized_email:
                    frappe.logger().error(f"[IMAP Fetcher] Failed to normalize email {email_id}")
                    continue
                
                # Process email through central pipeline
                comm_name = process_incoming_email(normalized_email)
                
                if comm_name:
                    processed_count += 1
                    print(f"[IMAP Fetcher] Processed email {email_id} -> {comm_name}")
                else:
                    frappe.logger().error(f"[IMAP Fetcher] Failed to process email {email_id}")
                
            except Exception as e:
                frappe.log_error(
                    title=f"IMAP Email Processing Error - {email_id}",
                    message=f"Error: {str(e)}\n{frappe.get_traceback()}"
                )
                continue
        
        # Close connection
        mail.close()
        mail.logout()
        
        print(f"[IMAP Fetcher] Completed - processed {processed_count}/{len(email_ids)} emails")
        
    except Exception as e:
        frappe.log_error(
            title="IMAP Fetch Failed",
            message=f"Error: {str(e)}\n{frappe.get_traceback()}"
        )


def get_imap_config() -> dict:
    """
    Get IMAP configuration from Email Account doctype
    """
    try:
        # Fetch first account with incoming + IMAP enabled
        email_accounts = frappe.get_all(
            "Email Account",
            filters={"enable_incoming": 1, "use_imap": 1},
            fields=["name", "email_id", "email_server", "incoming_port", "use_ssl", "password", "connected_app"]
        )

        if not email_accounts:
            frappe.logger().error("[IMAP Fetcher] No IMAP Email Account found with incoming enabled")
            return None

        account = email_accounts[0]

        # Check if using OAuth (Connected App) or password
        password = None
        access_token = None

        if account.connected_app:
            # Using OAuth - get access token
            access_token = get_oauth_access_token(account.name)
            print(f"[IMAP Fetcher] Using OAuth authentication for {account.name}")
        else:
            # Using password authentication
            from frappe.utils.password import get_decrypted_password
            password = get_decrypted_password("Email Account", account.name, "password", raise_exception=False)
            print(f"[IMAP Fetcher] Using password authentication for {account.name}")

        config = {
            "host": account.email_server,
            "port": account.incoming_port or 993,
            "email": account.email_id,
            "password": password,
            "access_token": access_token,
            "use_ssl": account.use_ssl,
            "account_name": account.name,
            "connected_app": account.connected_app
        }

        print(f"[IMAP Fetcher] Loaded IMAP config from Email Account: {account.name}")
        return config

    except Exception as e:
        frappe.log_error(
            title="IMAP Config Load Failed",
            message=f"Error: {str(e)}\n{frappe.get_traceback()}"
        )
        return None


def connect_imap(config: dict):
    """
    Connect to IMAP server using configuration
    Supports both password and OAuth authentication
    """
    try:
        host = config.get('host')
        port = config.get('port', 993)
        email_addr = config.get('email')
        password = config.get('password')
        access_token = config.get('access_token')
        use_ssl = config.get('use_ssl', True)

        print(f"[IMAP Fetcher] Connecting to {host}:{port}")

        # Connect
        if use_ssl:
            mail = imaplib.IMAP4_SSL(host, port)
        else:
            mail = imaplib.IMAP4(host, port)

        # Authenticate based on available credentials
        if access_token:
            # OAuth authentication using XOAUTH2
            auth_string = generate_oauth2_string(email_addr, access_token)
            mail.authenticate('XOAUTH2', lambda x: auth_string)
            print("[IMAP Fetcher] Successfully authenticated using OAuth")
        elif password:
            # Password authentication
            mail.login(email_addr, password)
            print("[IMAP Fetcher] Successfully authenticated using password")
        else:
            raise Exception("No authentication credentials provided (neither password nor OAuth token)")

        return mail

    except Exception as e:
        frappe.log_error(
            title="IMAP Connection Failed",
            message=f"Host: {config.get('host')}\n"
                   f"Error: {str(e)}\n"
                   f"{frappe.get_traceback()}"
        )
        return None


def get_oauth_access_token(email_account_name: str) -> str:
    """
    Get OAuth access token for email account using Connected App
    """
    try:
        email_account = frappe.get_doc("Email Account", email_account_name)

        if not email_account.connected_app:
            return None

        # Get connected user (usually the email account's user)
        connected_user = email_account.connected_user or frappe.session.user

        # Get access token from Connected App
        from frappe.integrations.doctype.connected_app.connected_app import get_connection
        connection = get_connection(email_account.connected_app, connected_user)

        if connection and hasattr(connection, 'access_token'):
            return connection.access_token

        return None

    except Exception as e:
        frappe.log_error(
            title="OAuth Token Fetch Failed",
            message=f"Email Account: {email_account_name}\n"
                   f"Error: {str(e)}\n"
                   f"{frappe.get_traceback()}"
        )
        return None


def generate_oauth2_string(email: str, access_token: str) -> str:
    """
    Generate OAuth2 authentication string for IMAP XOAUTH2
    Format: base64(user={email}\x01auth=Bearer {token}\x01\x01)
    """
    auth_string = f'user={email}\x01auth=Bearer {access_token}\x01\x01'
    return base64.b64encode(auth_string.encode()).decode()


@frappe.whitelist()
def test_imap_connection():
    """
    Test IMAP connection - useful for debugging
    """
    try:
        config = get_imap_config()
        
        if not config:
            return {
                "success": False,
                "message": "No valid IMAP Email Account found with incoming enabled"
            }
        
        mail = connect_imap(config)
        
        if not mail:
            return {
                "success": False,
                "message": "Failed to connect to IMAP server"
            }
        
        # Try to select inbox
        status, count = mail.select('INBOX')
        
        if status != 'OK':
            return {
                "success": False,
                "message": "Connected but failed to select INBOX"
            }
        
        # Get message count
        email_count = len(count[0].split()) if count[0] else 0
        
        # Close connection
        mail.close()
        mail.logout()
        
        return {
            "success": True,
            "message": f"Successfully connected to IMAP server",
            "host": config.get('host'),
            "email": config.get('email'),
            "inbox_messages": email_count
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"Error: {str(e)}"
        }
