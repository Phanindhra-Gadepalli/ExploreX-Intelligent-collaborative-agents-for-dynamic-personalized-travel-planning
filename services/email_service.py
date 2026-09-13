import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
import json

load_dotenv()

def send_trip_email(to_email, user_name, city, itinerary, budget, confirmation):
    """
    Sends an email to the user with their trip details.
    Uses SMTP credentials from environment variables.
    """
    smtp_server = os.environ.get("MAIL_SERVER")
    smtp_port = os.environ.get("MAIL_PORT")
    smtp_username = os.environ.get("MAIL_USERNAME")
    smtp_password = os.environ.get("MAIL_PASSWORD")
    use_tls = str(os.environ.get("MAIL_USE_TLS", "True")).lower() == "true"
    
    print(f"[EMAIL] SMTP username configured: {'yes' if smtp_username else 'no'}")
    print(f"[EMAIL] SMTP password configured: {'yes' if smtp_password else 'no'}")
    
    if not smtp_server or not smtp_username:
        print(f"[WARN] Email credentials not configured. Would have sent email to {to_email}")
        return False
        
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"Your ExploreX Trip to {city} is Ready!"
        msg['From'] = f"ExploreX Travel <{smtp_username}>"
        msg['To'] = to_email

        # Create the plain-text version
        text = f"Hello {user_name},\n\n{confirmation}\n\n"
        text += f"Your Trip to {city}\n\n"
        
        # Add Budget Info
        text += f"Estimated Budget:\n"
        text += f"Total: ₹{budget.get('total', '0')}\n"
        text += f"Accommodation: ₹{budget.get('accommodation', '0')}\n"
        text += f"Food: ₹{budget.get('food', '0')}\n"
        text += f"Transport: ₹{budget.get('transport', '0')}\n"
        text += f"Activities: ₹{budget.get('attractions', '0')}\n\n"
        
        # Add Itinerary
        text += "Itinerary:\n"
        if itinerary:
            for day in itinerary:
                text += f"\nDay {day.get('day', '?')}\n"
                for spot in day.get('spots', []):
                    text += f"- {spot.get('start_time')} to {spot.get('end_time')}: {spot.get('name')} ({spot.get('category')})\n"
        else:
            text += "No itinerary details available.\n"
            
        text += "\nHappy Travels!\nThe ExploreX Team"
        
        # Create HTML version
        html = f"""
        <html>
          <head></head>
          <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
            <h2>Hello {user_name},</h2>
            <p>{confirmation}</p>
            <h3>Your Trip to {city}</h3>
            
            <div style="background: #f4f4f4; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                <h4>Estimated Budget Summary</h4>
                <ul style="list-style-type: none; padding-left: 0;">
                    <li><strong>Total:</strong> ₹{budget.get('total', '0')}</li>
                    <li><strong>Accommodation:</strong> ₹{budget.get('accommodation', '0')}</li>
                    <li><strong>Food:</strong> ₹{budget.get('food', '0')}</li>
                    <li><strong>Transport:</strong> ₹{budget.get('transport', '0')}</li>
                    <li><strong>Activities:</strong> ₹{budget.get('attractions', '0')}</li>
                </ul>
            </div>
            
            <h4>Your Itinerary</h4>
        """
        
        if itinerary:
            for day in itinerary:
                html += f"<div style='margin-bottom:15px;'><h5 style='color:#0056b3; margin-bottom:5px;'>Day {day.get('day', '?')}</h5><ul style='margin-top:0;'>"
                for spot in day.get('spots', []):
                    html += f"<li><strong>{spot.get('start_time')} - {spot.get('end_time')}</strong>: {spot.get('name')} <span style='color:#666; font-size:0.9em;'>({spot.get('category')})</span></li>"
                html += "</ul></div>"
        else:
            html += "<p>No itinerary details available.</p>"
            
        html += """
            <p style="margin-top: 30px;">Happy Travels!<br><strong>The ExploreX Team</strong></p>
          </body>
        </html>
        """
        
        # Attach parts
        part1 = MIMEText(text, 'plain')
        part2 = MIMEText(html, 'html')
        msg.attach(part1)
        msg.attach(part2)
        
        # Send email
        port = int(smtp_port) if smtp_port else (587 if use_tls else 465)
        
        # Using explicit SSL or STARTTLS
        if use_tls:
            server = smtplib.SMTP(smtp_server, port)
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
            server = smtplib.SMTP_SSL(smtp_server, port)
            
        if smtp_password:
            # Google App Passwords might be pasted with spaces, e.g. "abcd efgh ijkl mnop"
            clean_password = smtp_password.replace(" ", "")
            server.login(smtp_username, clean_password)
            
        server.sendmail(smtp_username, to_email, msg.as_string())
        server.quit()
        return True
        
    except smtplib.SMTPAuthenticationError as auth_err:
        print(f"[EMAIL] SMTP authentication failed. Check Gmail App Password / SMTP configuration.")
        return False
    except Exception as e:
        print(f"[ERROR] Email service exception: {str(e)}")
        import traceback
        traceback.print_exc()
        return False
