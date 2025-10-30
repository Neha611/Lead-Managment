"""
IMAP Email Fetcher for Development Environments
Scheduled task that fetches unseen emails from IMAP server
"""

import frappe
from frappe.utils import now_datetime
import imaplib
import email
from email.parser import BytesParser
from email import policy
from crm_override.crm_override.email_threading.email_strategy import IMAPStrategy, EmailStrategyFactory
from crm_override.crm_override.email_threading.email_processor import process_incoming_email


def fetch_imap_emails():
    """
    Scheduled function to fetch emails from IMAP server.
    Should be configured in hooks.py as a scheduled job.
    
    Only runs when email_inbound_source = "imap" in site_config.json
    """
    # Check if IMAP mode is enabled
    if not EmailStrategyFactory.is_imap_mode():
        frappe.logger().info("[IMAP Fetcher] Skipping - not in IMAP mode")
        return
    
    try:
        frappe.logger().info("[IMAP Fetcher] Starting email fetch")
        
        # Get IMAP configuration from site_config.json
        imap_config = _get_imap_config()
        
        if not imap_config:
            frappe.logger().error("[IMAP Fetcher] IMAP configuration not found in site_config.json")
            return
        
        # Connect to IMAP server
        mail = _connect_imap(imap_config)
        
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
        
        frappe.logger().info(f"[IMAP Fetcher] Found {len(email_ids)} unseen emails")
        
        for email_id in email_ids:
            try:
                # Fetch email
                status, msg_data = mail.fetch(email_id, '(RFC822)')
                
                if status != 'OK':
                    continue
                
                # Parse email
                raw_email = msg_data[0][1]
                
                # Use IMAP strategy to normalize
                strategy = IMAPStrategy()
                normalized_email = strategy.normalize_email(raw_email)
                
                if not normalized_email:
                    frappe.logger().error(f"[IMAP Fetcher] Failed to normalize email {email_id}")
                    continue
                
                # Process email through central pipeline
                comm_name = process_incoming_email(normalized_email)
                
                if comm_name:
                    processed_count += 1
                    frappe.logger().info(f"[IMAP Fetcher] Processed email {email_id} -> {comm_name}")
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
        
        frappe.logger().info(f"[IMAP Fetcher] Completed - processed {processed_count}/{len(email_ids)} emails")
        
    except Exception as e:
        frappe.log_error(
            title="IMAP Fetch Failed",
            message=f"Error: {str(e)}\n{frappe.get_traceback()}"
        )


def _get_imap_config() -> dict:
    """
    Get IMAP configuration from Email Account doctype.
    Compatible with your Frappe field schema.
    """
    try:
        # Fetch first account with incoming + IMAP enabled
        email_accounts = frappe.get_all(
            "Email Account",
            filters={"enable_incoming": 1, "use_imap": 1},
            fields=["name", "email_id", "email_server", "incoming_port", "use_ssl", "password"]
        )

        if not email_accounts:
            frappe.logger().error("[IMAP Fetcher] No IMAP Email Account found with incoming enabled")
            return None

        account = email_accounts[0]

        # Decrypt password securely
        from frappe.utils.password import get_decrypted_password
        password = get_decrypted_password("Email Account", account.name, "password", raise_exception=False)

        config = {
            "host": account.email_server,
            "port": account.incoming_port or 993,
            "email": account.email_id,
            "password": password,
            "use_ssl": account.use_ssl,
            "account_name": account.name
        }

        frappe.logger().info(f"[IMAP Fetcher] Loaded IMAP config from Email Account: {account.name}")
        return config

    except Exception as e:
        frappe.log_error(
            title="IMAP Config Load Failed",
            message=f"Error: {str(e)}\n{frappe.get_traceback()}"
        )
        return None




def _connect_imap(config: dict):
    """
    Connect to IMAP server using configuration
    """
    try:
        host = config.get('host')
        port = config.get('port', 993)
        email_addr = config.get('email')
        password = config.get('password')
        use_ssl = config.get('use_ssl', True)
        
        frappe.logger().info(f"[IMAP Fetcher] Connecting to {host}:{port}")
        
        # Connect
        if use_ssl:
            mail = imaplib.IMAP4_SSL(host, port)
        else:
            mail = imaplib.IMAP4(host, port)
        
        # Login
        mail.login(email_addr, password)
        
        frappe.logger().info("[IMAP Fetcher] Successfully connected and authenticated")
        
        return mail
        
    except Exception as e:
        frappe.log_error(
            title="IMAP Connection Failed",
            message=f"Host: {config.get('host')}\n"
                   f"Error: {str(e)}\n"
                   f"{frappe.get_traceback()}"
        )
        return None


@frappe.whitelist()
def test_imap_connection():
    """
    Test IMAP connection - useful for debugging
    """
    try:
        config = _get_imap_config()
        
        if not config:
            return {
                "success": False,
                "message": "No valid IMAP Email Account found with incoming enabled"
            }
        
        mail = _connect_imap(config)
        
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


@frappe.whitelist()
def manual_imap_fetch():
    """
    Manually trigger IMAP email fetch - useful for testing
    """
    try:
        if not EmailStrategyFactory.is_imap_mode():
            return {
                "success": False,
                "message": "Not in IMAP mode. Set email_inbound_source='imap' in site_config.json"
            }
        
        fetch_imap_emails()
        
        return {
            "success": True,
            "message": "IMAP fetch completed. Check logs for details."
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"Error: {str(e)}"
        }