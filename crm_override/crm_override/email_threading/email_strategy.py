"""
Email Strategy Pattern Implementation
Provides a unified interface for handling incoming emails from different sources
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional
import frappe
from frappe.utils import now_datetime
import email
from email import policy
from email.parser import BytesParser
import json


class EmailStrategy(ABC):
    """
    Abstract base class for email handling strategies.
    All strategies must implement normalize_email() to return a standardized format.
    """
    
    @abstractmethod
    def normalize_email(self, raw_data) -> Dict:
        """
        Normalize incoming email data to a standard format.
        
        Returns:
        {
            'from': str,  # sender email
            'to': List[str],  # recipient emails
            'cc': List[str],  # cc emails
            'subject': str,
            'body_text': str,  # plain text body
            'body_html': str,  # html body
            'message_id': str,  # unique message identifier
            'in_reply_to': str,  # message_id of parent email
            'references': List[str],  # chain of message_ids
            'thread_id': str,  # Frappe thread identifier
            'headers': Dict,  # all email headers
            'attachments': List[Dict],  # list of attachment metadata
            'date': datetime,  # email sent date
            'raw_email': str  # original email for debugging
        }
        """
        pass


class IMAPStrategy(EmailStrategy):
    """
    Strategy for handling emails fetched from IMAP servers (Gmail, etc.)
    Used in development environments
    """
    
    def normalize_email(self, raw_data) -> Dict:
        """
        Parse email from IMAP raw bytes/message
        
        Args:
            raw_data: Raw email bytes or email.message.Message object
        """
        try:
            # Parse raw bytes to email message
            if isinstance(raw_data, bytes):
                msg = BytesParser(policy=policy.default).parsebytes(raw_data)
            else:
                msg = raw_data
            
            # Extract basic fields
            from_addr = self._extract_email(msg.get('From', ''))
            to_addrs = self._extract_emails(msg.get('To', ''))
            cc_addrs = self._extract_emails(msg.get('Cc', ''))
            subject = msg.get('Subject', '')
            message_id = msg.get('Message-ID', '').strip('<>')
            in_reply_to = msg.get('In-Reply-To', '').strip('<>')
            
            # Extract references chain
            references = []
            refs = msg.get('References', '')
            if refs:
                references = [ref.strip('<>') for ref in refs.split()]
            
            # Extract custom Frappe thread ID header
            thread_id = msg.get('X-Frappe-Thread-ID', '')
            
            # Extract email body
            body_text, body_html = self._extract_body(msg)
            
            # Extract attachments
            attachments = self._extract_attachments(msg)
            
            # Get all headers as dict
            headers = dict(msg.items())
            
            # Get date
            date_str = msg.get('Date')
            email_date = email.utils.parsedate_to_datetime(date_str) if date_str else now_datetime()
            
            return {
                'from': from_addr,
                'to': to_addrs,
                'cc': cc_addrs,
                'subject': subject,
                'body_text': body_text,
                'body_html': body_html,
                'message_id': message_id,
                'in_reply_to': in_reply_to,
                'references': references,
                'thread_id': thread_id,
                'headers': headers,
                'attachments': attachments,
                'date': email_date,
                'raw_email': msg.as_string()
            }
            
        except Exception as e:
            frappe.log_error(
                title="IMAP Email Normalization Failed",
                message=f"Error: {str(e)}\n{frappe.get_traceback()}"
            )
            return None
    
    def _extract_email(self, address_str: str) -> str:
        """Extract email address from 'Name <email@example.com>' format"""
        if not address_str:
            return ''
        
        parsed = email.utils.parseaddr(address_str)
        return parsed[1] if parsed[1] else address_str
    
    def _extract_emails(self, addresses_str: str) -> List[str]:
        """Extract multiple email addresses"""
        if not addresses_str:
            return []
        
        addresses = email.utils.getaddresses([addresses_str])
        return [addr[1] for addr in addresses if addr[1]]
    
    def _extract_body(self, msg) -> tuple:
        """Extract text and HTML body from email"""
        body_text = ''
        body_html = ''
        
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get('Content-Disposition', ''))
                
                # Skip attachments
                if 'attachment' in content_disposition:
                    continue
                
                if content_type == 'text/plain':
                    body_text = part.get_content()
                elif content_type == 'text/html':
                    body_html = part.get_content()
        else:
            content_type = msg.get_content_type()
            if content_type == 'text/plain':
                body_text = msg.get_content()
            elif content_type == 'text/html':
                body_html = msg.get_content()
        
        return body_text, body_html
    
    def _extract_attachments(self, msg) -> List[Dict]:
        """Extract attachment metadata from email"""
        attachments = []
        
        if msg.is_multipart():
            for part in msg.walk():
                content_disposition = str(part.get('Content-Disposition', ''))
                
                if 'attachment' in content_disposition:
                    filename = part.get_filename()
                    if filename:
                        attachments.append({
                            'filename': filename,
                            'content_type': part.get_content_type(),
                            'size': len(part.get_payload(decode=True)),
                            'content': part.get_payload(decode=True)
                        })
        
        return attachments


class SendGridStrategy(EmailStrategy):
    """
    Strategy for handling emails from SendGrid Inbound Parse Webhook
    Used in production environments
    """
    
    def normalize_email(self, raw_data) -> Dict:
        """
        Parse email from SendGrid webhook payload
        
        Args:
            raw_data: Dict containing SendGrid webhook POST data
        """
        try:
            # SendGrid sends data as form fields
            from_addr = raw_data.get('from', '')
            to_addrs = self._parse_address_list(raw_data.get('to', ''))
            cc_addrs = self._parse_address_list(raw_data.get('cc', ''))
            subject = raw_data.get('subject', '')
            
            # SendGrid provides both text and HTML
            body_text = raw_data.get('text', '')
            body_html = raw_data.get('html', '')
            
            # Email metadata
            message_id = raw_data.get('message-id', '').strip('<>')
            in_reply_to = raw_data.get('in-reply-to', '').strip('<>')
            
            # References chain
            references = []
            refs = raw_data.get('references', '')
            if refs:
                references = [ref.strip('<>') for ref in refs.split()]
            
            # Extract custom Frappe thread ID from headers
            thread_id = ''
            headers_json = raw_data.get('headers', '{}')
            if headers_json:
                try:
                    headers = json.loads(headers_json)
                    thread_id = headers.get('X-Frappe-Thread-ID', '')
                except:
                    headers = {}
            else:
                headers = {}
            
            # Parse attachments from SendGrid format
            attachments = self._extract_sendgrid_attachments(raw_data)
            
            # Date
            date_str = raw_data.get('date')
            email_date = email.utils.parsedate_to_datetime(date_str) if date_str else now_datetime()
            
            return {
                'from': from_addr,
                'to': to_addrs,
                'cc': cc_addrs,
                'subject': subject,
                'body_text': body_text,
                'body_html': body_html,
                'message_id': message_id,
                'in_reply_to': in_reply_to,
                'references': references,
                'thread_id': thread_id,
                'headers': headers,
                'attachments': attachments,
                'date': email_date,
                'raw_email': raw_data.get('email', '')  # SendGrid provides raw email
            }
            
        except Exception as e:
            frappe.log_error(
                title="SendGrid Email Normalization Failed",
                message=f"Error: {str(e)}\n{frappe.get_traceback()}"
            )
            return None
    
    def _parse_address_list(self, address_str: str) -> List[str]:
        """Parse comma-separated email addresses"""
        if not address_str:
            return []
        
        addresses = email.utils.getaddresses([address_str])
        return [addr[1] for addr in addresses if addr[1]]
    
    def _extract_sendgrid_attachments(self, raw_data: Dict) -> List[Dict]:
        """Extract attachments from SendGrid webhook payload"""
        attachments = []
        
        # SendGrid sends attachments as separate form fields
        # Format: attachment1, attachment2, etc.
        attachment_count = int(raw_data.get('attachments', 0))
        
        for i in range(1, attachment_count + 1):
            attachment_data = raw_data.get(f'attachment{i}')
            attachment_info = raw_data.get(f'attachment-info', {})
            
            if attachment_data:
                # Parse attachment info if available
                try:
                    if isinstance(attachment_info, str):
                        attachment_info = json.loads(attachment_info)
                    
                    info = attachment_info.get(f'attachment{i}', {})
                    filename = info.get('filename', f'attachment{i}')
                    content_type = info.get('type', 'application/octet-stream')
                except:
                    filename = f'attachment{i}'
                    content_type = 'application/octet-stream'
                
                attachments.append({
                    'filename': filename,
                    'content_type': content_type,
                    'size': len(attachment_data),
                    'content': attachment_data
                })
        
        return attachments


class EmailStrategyFactory:
    """
    Factory to create appropriate email strategy based on configuration
    """
    
    @staticmethod
    def get_strategy() -> EmailStrategy:
        """
        Returns the appropriate strategy based on site_config.json setting
        """
        # Read from site_config.json
        email_source = frappe.conf.get('email_inbound_source', 'imap').lower()
        
        if email_source == 'sendgrid':
            return SendGridStrategy()
        elif email_source == 'imap':
            return IMAPStrategy()
        else:
            frappe.throw(f"Unknown email_inbound_source: {email_source}. Use 'imap' or 'sendgrid'")
    
    @staticmethod
    def is_imap_mode() -> bool:
        """Check if running in IMAP mode"""
        return frappe.conf.get('email_inbound_source', 'imap').lower() == 'imap'
    
    @staticmethod
    def is_sendgrid_mode() -> bool:
        """Check if running in SendGrid mode"""
        return frappe.conf.get('email_inbound_source', 'imap').lower() == 'sendgrid'