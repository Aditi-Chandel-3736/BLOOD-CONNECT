"""
email_sender.py — BloodConnect Email System
============================================
PURPOSE: Sends all automated emails in the system.
WHY NECESSARY: Email is how donors get alerted instantly.
Without this file, everything stays inside the app
and nobody gets notified in real time.

HOW IT WORKS:
Uses Python's built-in smtplib library to connect to
Gmail's SMTP server and send emails programmatically.
Think of it as your app logging into Gmail and
sending emails automatically on your behalf.

SECURITY:
- Every piece of user-typed text is passed through escape() so nobody
  can inject HTML or fake links into our emails.
- Alert emails contain NO patient name and NO phone number. Donors see
  those only inside BloodConnect after they accept the request.
- Email addresses are masked in log files (r***@gmail.com).
"""

import smtplib
import os
import logging
from html import escape
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

# Gmail SMTP settings
# SMTP = Simple Mail Transfer Protocol
# Port 587 = TLS encrypted connection (secure)
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _mask(email):
    """r***@gmail.com — keeps email addresses out of log files."""
    name, _, domain = (email or "").partition("@")
    return (name[:1] + "***@" + domain) if domain else "***"


def _base_url():
    # Get base URL — for production this will be your deployed URL
    return os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")


def _get_credentials():
    """
    PURPOSE: Gets email credentials from environment variables
    WHY: We NEVER hardcode passwords in code files.
         If code is shared on GitHub, everyone would see your password.
         .env file keeps credentials safe and separate from code.
    """
    email = os.environ.get("EMAIL_ADDRESS", "")
    password = os.environ.get("EMAIL_PASSWORD", "")
    return email, password


def _send(to_email, subject, html_body):
    """
    PURPOSE: Core function that actually sends one email
    WHY: All other email functions call this one.
         Keeps sending logic in one place — if Gmail changes
         their settings we only fix it here once.

    RETURNS: True if sent, False if failed
    """
    sender, password = _get_credentials()
    # Emoji removed for log lines — some Windows terminals cannot print them
    log_subject = subject.encode("ascii", "ignore").decode().strip()

    if not to_email:
        return False

    # If email not configured — skip silently (useful during development)
    if not sender or not password:
        logger.warning(f"[EMAIL SKIPPED] Email not configured. Would send to: "
                       f"{_mask(to_email)} | Subject: {log_subject}")
        return False

    try:
        # Create message container
        # MIMEMultipart allows both plain text and HTML
        msg = MIMEMultipart("alternative")
        msg["From"]    = f"BloodConnect <{sender}>"
        msg["To"]      = to_email
        msg["Subject"] = subject

        # Attach HTML body
        msg.attach(MIMEText(html_body, "html"))

        # Connect and send
        # 'with' statement ensures connection closes even if error occurs
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()              # Enable TLS encryption
            server.login(sender, password) # Login with app password
            server.sendmail(sender, to_email, msg.as_string())

        logger.info(f"Email sent: to={_mask(to_email)} | subject={log_subject}")
        return True

    except smtplib.SMTPAuthenticationError:
        logger.error("Gmail authentication failed. Check EMAIL_ADDRESS and EMAIL_PASSWORD in .env")
        return False
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error sending to {_mask(to_email)}: {e.__class__.__name__}")
        return False
    except Exception as e:
        logger.error(f"Unexpected email error to {_mask(to_email)}: {e.__class__.__name__}")
        return False


def _layout(subtitle, body_html):
    """Shared email frame (header + footer). body_html must already be escaped."""
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
      <div style="background:#C53030;padding:20px;text-align:center;border-radius:8px 8px 0 0">
        <h1 style="color:white;margin:0;font-size:24px">🩸 BloodConnect</h1>
        <p style="color:#FED7D7;margin:5px 0 0">{subtitle}</p>
      </div>
      <div style="padding:25px;background:#ffffff;border:1px solid #E2E8F0;color:#4A5568;line-height:1.6">
        {body_html}
      </div>
      <div style="background:#F7FAFC;padding:12px;text-align:center;color:#A0AEC0;font-size:12px;border-radius:0 0 8px 8px">
        BloodConnect — Every drop counts 🩸
      </div>
    </div>
    """


def _button(href, label, color="#C53030"):
    return (f'<a href="{href}" style="background:{color};color:white;padding:14px 28px;'
            f'border-radius:8px;text-decoration:none;font-weight:bold;font-size:16px;'
            f'display:inline-block">{label}</a>')


# ═══════════════════════════════════════════════════
# EMAIL 1 — REGISTRATION CONFIRMATION
# Sent when: Someone successfully registers
# Purpose: Welcome email + confirms their details
# ═══════════════════════════════════════════════════

def send_registration_confirmation(donor_email, donor_name,
                                   blood_group, donor_id, role="donor"):
    subject = f"🩸 Welcome to BloodConnect, {donor_name}!"
    if role == "donor":
        intro = f"""
        <p>You are now a registered <strong>{escape(blood_group)}</strong> blood donor on BloodConnect.
        When someone in your city needs blood your group can give to, you will receive an alert.</p>
        <div style="background:#FFFFF0;border:1px solid #ECC94B;border-radius:8px;padding:15px;margin:20px 0">
          <h3 style="color:#744210;margin:0 0 10px">⚕️ Important Medical Note</h3>
          <p style="color:#744210;margin:0;font-size:14px">
            Safe gaps between donations depend on your country's rules and your health.
            Always confirm with the blood bank or a doctor that you are eligible before donating.
          </p>
        </div>"""
    else:
        intro = """
        <p>Your account is ready. You can now create blood requests and track
        donor responses from your dashboard.</p>"""
    html = _layout("Saving Lives Together", f"""
        <h2 style="color:#1A202C">Welcome, {escape(donor_name)}! 🎉</h2>
        {intro}
        <div style="background:#FFF5F5;border-left:4px solid #C53030;padding:15px;border-radius:4px;margin:20px 0">
          <p style="margin:0"><strong>Account ID:</strong> #{int(donor_id)}</p>
        </div>
        <p style="font-size:13px;color:#718096">If you did not create this account, please ignore this email.</p>
    """)
    return _send(donor_email, subject, html)


# ═══════════════════════════════════════════════════
# EMAIL 2 — BLOOD REQUEST ALERT TO DONOR
# Sent when: Blood request matches a donor
# PRIVACY: Only general information (blood group, hospital, city,
#          urgency). The patient's name and the family's phone number
#          are shown inside BloodConnect ONLY after the donor logs in
#          and explicitly accepts the request.
# ═══════════════════════════════════════════════════

def send_donor_alert(donor_email, donor_name, req_id, blood_group,
                     units_needed, hospital_name, city, urgency):
    urgency_colors = {
        "Normal":   "#38A169",
        "Urgent":   "#D69E2E",
        "Critical": "#C53030"
    }
    color = urgency_colors.get(urgency, "#C53030")
    subject = f"🚨 {urgency.upper()}: {blood_group} Blood Needed — {city}"
    link = f"{_base_url()}/request/{int(req_id)}"

    html = _layout("Emergency Blood Alert", f"""
        <div style="background:{color};color:white;padding:10px 15px;border-radius:6px;text-align:center;margin-bottom:20px">
          <strong>⚠️ {escape(urgency.upper())} REQUEST</strong>
        </div>
        <h2 style="color:#1A202C">Dear {escape(donor_name)},</h2>
        <p>A patient needs <strong>{escape(blood_group)}</strong> blood and your blood group is compatible.</p>
        <table style="width:100%;border-collapse:collapse;background:#FFF5F5;border-radius:8px">
          <tr><td style="padding:8px 12px;color:#718096;width:130px"><strong>Blood Group</strong></td>
              <td style="padding:8px 12px;color:#C53030;font-size:18px;font-weight:bold">{escape(blood_group)}</td></tr>
          <tr><td style="padding:8px 12px;color:#718096"><strong>Units Needed</strong></td>
              <td style="padding:8px 12px">{int(units_needed)} unit(s)</td></tr>
          <tr><td style="padding:8px 12px;color:#718096"><strong>Hospital</strong></td>
              <td style="padding:8px 12px">{escape(hospital_name)}</td></tr>
          <tr><td style="padding:8px 12px;color:#718096"><strong>City</strong></td>
              <td style="padding:8px 12px">{escape(city)}</td></tr>
        </table>
        <div style="text-align:center;margin:25px 0">{_button(link, "View request &amp; respond")}</div>
        <div style="background:#EBF8FF;border:1px solid #90CDF4;border-radius:8px;padding:15px">
          <p style="color:#2B6CB0;font-size:13px;margin:0">
            🔒 To protect the patient's privacy, contact details are shown only after you
            log in and <strong>accept</strong> the request. If you cannot help, please press
            "Decline" so the family knows to rely on other donors.
          </p>
        </div>
        <p style="color:#718096;font-size:12px;text-align:center;margin-top:16px">
          ⚕️ Please donate only if you feel well and the blood bank confirms you are eligible.
        </p>
    """)
    return _send(donor_email, subject, html)


# ═══════════════════════════════════════════════════
# EMAIL 3 — REQUESTER CONFIRMATION
# Sent when: Blood request alerts are fired
# Purpose: Tells the requester how many donors were alerted
# ═══════════════════════════════════════════════════

def send_requester_confirmation(contact_email, contact_person,
                                blood_group, donors_alerted, req_id):
    subject = f"✅ Your {blood_group} blood request is live"
    link = f"{_base_url()}/request/{int(req_id)}"
    html = _layout("Request received", f"""
        <h2 style="color:#1A202C">Dear {escape(contact_person)},</h2>
        <p>Your blood request (<strong>{escape(blood_group)}</strong>) has been received.</p>
        <div style="background:#F0FFF4;border-left:4px solid #38A169;padding:15px;border-radius:4px;margin:20px 0">
          <p style="margin:0;font-size:18px">✅ <strong>{int(donors_alerted)} matching donors</strong> have been alerted.</p>
        </div>
        <p>When a donor accepts, you will get a notification on BloodConnect with their
        name and phone number so you can coordinate.</p>
        <div style="text-align:center;margin:25px 0">{_button(link, "Track your request")}</div>
    """)
    return _send(contact_email, subject, html)


# ═══════════════════════════════════════════════════
# EMAIL 4 — OUTCOME THANK YOU EMAIL
# Sent when: Donor reports Stage 3 outcome
# Purpose: Thank donor regardless of outcome
# ═══════════════════════════════════════════════════

def send_outcome_thankyou(donor_email, donor_name, outcome):
    if outcome == "Donated":
        subject = "🏆 Thank You for Saving a Life, " + donor_name + "!"
        msg = """
        <div style='background:#F0FFF4;border-left:4px solid #38A169;padding:15px;border-radius:4px;margin:20px 0'>
          <h3 style='color:#276749;margin:0'>You are a hero! 🦸</h3>
          <p style='color:#276749;margin:8px 0 0'>
            Your donation has been recorded and BloodConnect will pause new alerts for a while.
            Before donating again, please check with the blood bank that you are eligible.
          </p>
        </div>
        """
    elif outcome == "NotNeeded":
        subject = "💙 Thank You for Showing Up, " + donor_name
        msg = """
        <div style='background:#EBF8FF;border-left:4px solid #63B3ED;padding:15px;border-radius:4px;margin:20px 0'>
          <h3 style='color:#2B6CB0;margin:0'>Thank you for trying! 💙</h3>
          <p style='color:#2B6CB0;margin:8px 0 0'>
            We understand the blood was no longer needed when you arrived.
            Your willingness to help matters greatly. Your alerts have NOT been paused.
          </p>
        </div>
        """
    else:  # Rejected
        subject = "💙 Thank You for Coming, " + donor_name
        msg = """
        <div style='background:#FFFFF0;border-left:4px solid #ECC94B;padding:15px;border-radius:4px;margin:20px 0'>
          <h3 style='color:#744210;margin:0'>Thank you for your effort 💙</h3>
          <p style='color:#744210;margin:8px 0 0'>
            We are sorry you were not able to donate this time.
            Please consult a doctor about your eligibility for future donations.
          </p>
        </div>
        """

    html = _layout("Thank you", f"""
        <h2 style="color:#1A202C">Dear {escape(donor_name)},</h2>
        {msg}
        <p style="color:#718096;font-size:13px">
          Thank you for being part of BloodConnect. Your presence in our donor network saves lives.
        </p>
    """)
    return _send(donor_email, subject, html)


# ═══════════════════════════════════════════════════
# EMAIL 5 — STATUS UPDATES (new)
# Sent when: a donor accepts (to requester), a request is cancelled
#            (to accepted donors), or a donation is completed.
# PRIVACY: Only says "log in to see details" — no phone numbers by email.
# ═══════════════════════════════════════════════════

def send_status_update(to_email, to_name, headline, message, req_id):
    link = f"{_base_url()}/request/{int(req_id)}"
    html = _layout("Request update", f"""
        <h2 style="color:#1A202C">Dear {escape(to_name)},</h2>
        <p style="font-size:16px"><strong>{escape(headline)}</strong></p>
        <p>{escape(message)}</p>
        <div style="text-align:center;margin:25px 0">{_button(link, "Open BloodConnect")}</div>
    """)
    return _send(to_email, f"🩸 {headline}", html)


# ═══════════════════════════════════════════════════
# BULK ALERT FUNCTION
# PURPOSE: Sends alerts to ALL matching donors at once
# WHY: A blood emergency needs ALL possible donors
#      alerted simultaneously, not one by one
# ═══════════════════════════════════════════════════

def send_bulk_alerts(donors, req_id, blood_group, units_needed,
                     hospital_name, city, urgency, log_alert_fn):
    """
    Loops through all matching donors, records an alert for each one
    and sends each one an alert email (public request info only).

    RETURNS: (sent_count, failed_count)
    """
    sent = 0
    failed = 0

    logger.info(f"Starting bulk alert: {len(donors)} donors | request={req_id}")

    for donor in donors:
        # Log alert in DB first so the donor's dashboard shows it
        log_alert_fn(req_id, donor["id"])

        success = send_donor_alert(
            donor_email   = donor["email"],
            donor_name    = donor["name"],
            req_id        = req_id,
            blood_group   = blood_group,
            units_needed  = units_needed,
            hospital_name = hospital_name,
            city          = city,
            urgency       = urgency
        )

        if success:
            sent += 1
        else:
            failed += 1

    logger.info(f"Bulk alert complete: sent={sent} failed={failed}")
    return sent, failed
