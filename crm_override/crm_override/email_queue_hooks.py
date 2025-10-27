import frappe
from frappe.utils import now_datetime
import re
import quopri
from urllib.parse import quote

def on_email_queue_after_insert(doc, method):
    """
    Hook that runs after Email Queue is inserted.
    Only creates trackers for UI/manual emails, NOT for campaign emails.
    """
    # Only handle CRM Lead emails
    if doc.reference_doctype != "CRM Lead":
        return

    # ✅ CRITICAL: Skip if tracker already exists for this Email Queue
    # This prevents duplicate tracker creation from campaign emails
    existing_tracker = frappe.db.exists("Lead Email Tracker", {"email_queue_status": doc.name})
    if existing_tracker:
        frappe.logger().info(f"[Hook] Tracker already exists for Email Queue: {doc.name}, skipping hook")
        return

    # ✅ Also check by lead + communication to catch race conditions
    if doc.communication:
        comm_tracker = frappe.db.exists("Lead Email Tracker", {
            "lead": doc.reference_name,
            "communication": doc.communication
        })
        if comm_tracker:
            frappe.logger().info(f"[Hook] Tracker already exists for Lead+Communication, skipping hook")
            return

    try:
        from crm_override.crm_override.broadcast_utils import create_lead_email_tracker
        
        frappe.logger().info(f"[Hook] Creating tracker for UI email: {doc.name}")
        
        # Check if this is a MIME multipart message (UI emails)
        if 'Content-Type: text/html' in doc.message and '--===============' in doc.message:
            frappe.logger().info(f"[Pixel Injection] Detected MIME message for {doc.name}")
            doc.message = inject_pixel_into_mime_message(doc.message, doc.name)
        else:
            frappe.logger().info(f"[Pixel Injection] Simple HTML message for {doc.name}")
            # Simple HTML message - use existing function
            from crm_override.crm_override.broadcast_utils import inject_tracking_pixel
            if not doc.message.startswith("<html"):
                doc.message = f"<html>{doc.message}</html>"
            doc.message = inject_tracking_pixel(doc.message, email_queue_name=doc.name)
        
        doc.save(ignore_permissions=True)

        communication_id = doc.communication
        tracker = create_lead_email_tracker(
            lead_name=doc.reference_name,
            email_queue_name=doc.name,
            communication_name=communication_id,
            initial_status="Queued" if doc.status != "Sent" else "Sent"
        )
        
        if tracker:
            if tracker.communication:
                try:
                    comm = frappe.get_doc("Communication", tracker.communication)
                    comm.db_set("status", "Queued" if doc.status != "Sent" else "Sent")
                    comm.db_set("delivery_status", "Queued")
                    frappe.logger().info(f"[Tracker Update] Communication {comm.name} -> {"Queued" if doc.status != "Sent" else "Sent"}")

                    # Notify UI updates
                    comm.notify_change("update")
                    frappe.publish_realtime(
                        "list_update",
                        {
                            "doctype": "Communication",
                            "name": comm.name,
                            "delivery_status": "Queued" if doc.status != "Sent" else "Sent"
                        },
                        after_commit=True
                    )

                    if comm.reference_doctype and comm.reference_name:
                        frappe.publish_realtime(
                            "docinfo_update",
                            {
                                "doc": comm.as_dict(),
                                "key": "communications",
                                "action": "update"
                            },
                            doctype=comm.reference_doctype,
                            docname=comm.reference_name,
                            after_commit=True
                        )
                except Exception as comm_error:
                    frappe.logger().error(f"[Tracker Update] Failed to update Communication for {tracker.name}: {str(comm_error)}")
            frappe.db.commit()
            frappe.logger().info(f"[Hook] Lead Email Tracker created: {tracker.name}")
        else:
            frappe.logger().warning(f"[Hook] Failed to create tracker for {doc.name}")

    except Exception as e:
        frappe.log_error(
            title=f"Failed to create Lead Email Tracker for {doc.name}",
            message=f"{str(e)}\n{frappe.get_traceback()}"
        )

def inject_pixel_into_mime_message(message, email_queue_name):
    """
    Inject tracking pixel into MIME multipart messages.
    Handles quoted-printable encoding properly.
    """
    # Generate the tracking pixel HTML
    base_url = "https://ops.tradyon.ai"
    tracking_url = f"{base_url}/api/method/crm_override.crm_override.email_tracker.email_tracker?name={quote(email_queue_name)}"
    pixel = f'<img src="{tracking_url}" width="1" height="1" style="display:none;" alt=""/>'
    print("tracking_url:", tracking_url)
    
    frappe.logger().info(f"[Pixel Injection] Tracking URL: {tracking_url}")
    
    # Pattern to match the HTML section with quoted-printable encoding
    # This captures: headers + content + boundary
    html_pattern = r'(Content-Type: text/html.*?Content-Transfer-Encoding: quoted-printable\s*\n)\s*\n(.*?)((?:\n)?--===============[0-9]+==)'
    
    def inject_into_html(match):
        header = match.group(1)  # Headers
        encoded_content = match.group(2)  # Quoted-printable encoded HTML
        boundary = match.group(3)  # MIME boundary
        
        frappe.logger().info(f"[Pixel Injection] Found HTML section, content length: {len(encoded_content)}")
        
        # Decode quoted-printable to get actual HTML
        try:
            # Remove soft line breaks (=\n) and decode
            decoded_html = quopri.decodestring(encoded_content.encode()).decode('utf-8')
            frappe.logger().info(f"[Pixel Injection] Decoded HTML length: {len(decoded_html)}")
            
            # Replace the <!--email_open_check--> placeholder with actual pixel
            if '<!--email_open_check-->' in decoded_html:
                decoded_html = decoded_html.replace('<!--email_open_check-->', pixel)
                frappe.logger().info(f"[Pixel Injection] Replaced <!--email_open_check--> placeholder")
            # Fallback: inject before </body> or </html>
            elif '</body>' in decoded_html.lower():
                decoded_html = re.sub(r'</body>', f'{pixel}</body>', decoded_html, count=1, flags=re.IGNORECASE)
                frappe.logger().info(f"[Pixel Injection] Injected before </body>")
            elif '</html>' in decoded_html.lower():
                html_lower = decoded_html.lower()
                last_html_pos = html_lower.rfind('</html>')
                if last_html_pos != -1:
                    decoded_html = decoded_html[:last_html_pos] + pixel + decoded_html[last_html_pos:]
                    frappe.logger().info(f"[Pixel Injection] Injected before </html>")
            else:
                # No good injection point found
                decoded_html = decoded_html + '\n' + pixel
                frappe.logger().info(f"[Pixel Injection] Appended at end")
            
            # Re-encode to quoted-printable
            re_encoded = quopri.encodestring(decoded_html.encode('utf-8')).decode('utf-8')
            
            return header + '\n' + re_encoded + boundary
            
        except Exception as e:
            frappe.logger().error(f"[Pixel Injection] Error decoding/encoding: {str(e)}")
            # Return original if decoding fails
            return header + '\n' + encoded_content + boundary
    
    # Apply the injection
    new_message = re.sub(html_pattern, inject_into_html, message, flags=re.DOTALL)
    
    # Check if replacement happened
    if new_message == message:
        frappe.logger().warning(f"[Pixel Injection] Pattern did not match for Email Queue: {email_queue_name}")
        # Try alternative pattern without quoted-printable
        html_pattern_alt = r'(Content-Type: text/html.*?\n(?:Content-Transfer-Encoding: [^\n]+\n)?)\s*\n(.*?)((?:\n)?--===============[0-9]+==)'
        new_message = re.sub(html_pattern_alt, inject_into_html, message, flags=re.DOTALL)
        
        if new_message == message:
            frappe.logger().error(f"[Pixel Injection] All patterns failed for: {email_queue_name}")
    else:
        frappe.logger().info(f"[Pixel Injection] Successfully injected pixel for: {email_queue_name}")
    
    return new_message


def on_email_queue_before_save(doc, method):
    """Called before Email Queue is saved - catches ALL updates"""
    try:
        # Store the old status before it changes
        if not doc.is_new():
            old_status = frappe.db.get_value("Email Queue", doc.name, "status")
            if old_status and old_status != doc.status:
                doc._status_changed = True
                doc._old_status = old_status
                frappe.logger().info(
                    f"[Hook - Before Save] Email Queue {doc.name} status changing: "
                    f"{old_status} -> {doc.status}"
                )
    except Exception as e:
        frappe.logger().error(f"Error in before_save hook: {str(e)}")

