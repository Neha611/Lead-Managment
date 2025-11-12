"""
Email Utility Functions
Handles email parsing and normalization from IMAP
"""

from typing import Dict, List
import frappe
from frappe.utils import now_datetime
import email
from email import policy
from email.parser import BytesParser


def normalize_imap_email(raw_data) -> Dict:
    """
    Parse and normalize email from IMAP raw bytes/message
    
    Args:
        raw_data: Raw email bytes or email.message.Message object
        
    Returns:
        Standardized email dict:
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
    try:
        # Parse raw bytes to email message
        if isinstance(raw_data, bytes):
            msg = BytesParser(policy=policy.default).parsebytes(raw_data)
        else:
            msg = raw_data
        
        # Extract basic fields
        from_addr = extract_email(msg.get('From', ''))
        to_addrs = extract_emails(msg.get('To', ''))
        cc_addrs = extract_emails(msg.get('Cc', ''))
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
        body_text, body_html = extract_body(msg)
        
        # Extract attachments
        attachments = extract_attachments(msg)
        
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


def extract_email(address_str: str) -> str:
    """Extract email address from 'Name <email@example.com>' format"""
    if not address_str:
        return ''
    
    parsed = email.utils.parseaddr(address_str)
    return parsed[1] if parsed[1] else address_str


def extract_emails(addresses_str: str) -> List[str]:
    """Extract multiple email addresses"""
    if not addresses_str:
        return []
    
    addresses = email.utils.getaddresses([addresses_str])
    return [addr[1] for addr in addresses if addr[1]]


def extract_body(msg) -> tuple:
    """
    Extract text and HTML body from email
    
    Returns: (body_text, body_html)
    """
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


def extract_attachments(msg) -> List[Dict]:
    """
    Extract attachment metadata from email
    
    Returns: List of dicts with filename, content_type, size, content
    """
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