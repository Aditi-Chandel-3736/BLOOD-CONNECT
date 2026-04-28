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
"""

import smtplib
import os
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

# Gmail SMTP settings
# SMTP = Simple Mail Transfer Protocol
# Port 587 = TLS encrypted connection (secure)
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


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

    HOW IT WORKS:
    1. Creates an email message object
    2. Sets From, To, Subject fields
    3. Attaches HTML content (so email looks beautiful)
    4. Connects to Gmail SMTP server
    5. Logs in with your Gmail app password
    6. Sends the email
    7. Connection closes automatically

    RETURNS: True if sent, False if failed
    """
    sender, password = _get_credentials()

    # If email not configured — skip silently (useful during development)
    if not sender or not password:
        logger.warning("Email not configured — skipping send")
        print(f"[EMAIL SKIPPED] Would send to: {to_email} | Subject: {subject}")
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
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()              # Enable TLS encryption
            server.login(sender, password) # Login with app password
            server.sendmail(sender, to_email, msg.as_string())

        logger.info(f"Email sent: to={to_email} | subject={subject}")
        return True

    except smtplib.SMTPAuthenticationError:
        logger.error("Gmail authentication failed. Check EMAIL_ADDRESS and EMAIL_PASSWORD in .env")
        return False
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error sending to {to_email}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected email error to {to_email}: {e}")
        return False


# ═══════════════════════════════════════════════════
# EMAIL 1 — DONOR REGISTRATION CONFIRMATION
# Sent when: Donor successfully registers
# Purpose: Welcome email + confirms their details
# ═══════════════════════════════════════════════════

def send_registration_confirmation(donor_email, donor_name,
                                    blood_group, donor_id):
    subject = f"🩸 Welcome to BloodConnect, {donor_name}!"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
      <div style="background:#C53030;padding:20px;text-align:center;border-radius:8px 8px 0 0">
        <h1 style="color:white;margin:0;font-size:24px">🩸 BloodConnect</h1>
        <p style="color:#FED7D7;margin:5px 0 0">Saving Lives Together</p>
      </div>
      <div style="padding:30px;background:#ffffff;border:1px solid #E2E8F0">
        <h2 style="color:#1A202C">Welcome, {donor_name}! 🎉</h2>
        <p style="color:#4A5568;line-height:1.6">
          You are now a registered <strong>{blood_group}</strong> blood donor on BloodConnect.
          When someone in your city urgently needs <strong>{blood_group}</strong> blood,
          you will receive an instant email alert.
        </p>
        <div style="background:#FFF5F5;border-left:4px solid #C53030;padding:15px;border-radius:4px;margin:20px 0">
          <p style="margin:0"><strong>Donor ID:</strong> #{donor_id}</p>
          <p style="margin:8px 0 0"><strong>Blood Group:</strong> {blood_group}</p>
        </div>
        <div style="background:#FFFFF0;border:1px solid #ECC94B;border-radius:8px;padding:15px;margin:20px 0">
          <h3 style="color:#744210;margin:0 0 10px">⚕️ Important Medical Note</h3>
          <p style="color:#744210;margin:0;font-size:14px">
            You can donate whole blood once every <strong>56 days</strong>
            as per WHO guidelines. The system automatically tracks this
            and will not alert you during your cooldown period.
          </p>
        </div>
        <p style="color:#718096;font-size:13px">
          After visiting a hospital to donate blood, please return to BloodConnect
          and mark your donation outcome. This keeps your cooldown timer accurate
          and helps other donors in the system.
        </p>
      </div>
      <div style="background:#F7FAFC;padding:12px;text-align:center;color:#A0AEC0;font-size:12px;border-radius:0 0 8px 8px">
        BloodConnect — Every drop counts 🩸
      </div>
    </div>
    """
    return _send(donor_email, subject, html)


# ═══════════════════════════════════════════════════
# EMAIL 2 — BLOOD REQUEST ALERT TO DONOR
# Sent when: Blood request matches a donor
# Purpose: Alert donor with full patient details
#          AND contact number to call directly
# ═══════════════════════════════════════════════════

def send_donor_alert(donor_email, donor_name, alert_id,
                     patient_name, blood_group, units_needed,
                     hospital_name, city, contact_person,
                     contact_phone, urgency):
    """
    WHY CONTACT PHONE IS CRITICAL:
    The contact_phone is shown PROMINENTLY in this email.
    This is how the donor calls the patient family directly.
    Without this, donor would not know who to contact after arriving.

    THE 3-STAGE BUTTONS:
    - YES I AM COMING → Stage 2 (Confirmed)
    - CANNOT HELP → Declined
    After visiting hospital, donor returns to report Stage 3 outcome.
    """
    urgency_colors = {
        "Normal":   "#38A169",
        "Urgent":   "#D69E2E",
        "Critical": "#C53030"
    }
    color = urgency_colors.get(urgency, "#C53030")

    subject = f"🚨 {urgency.upper()}: {blood_group} Blood Needed — {hospital_name}, {city}"

    # Get base URL — for production this will be your Railway URL
    base_url = os.environ.get("BASE_URL", "http://localhost:5000")

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
      <div style="background:#C53030;padding:20px;text-align:center;border-radius:8px 8px 0 0">
        <h1 style="color:white;margin:0;font-size:24px">🩸 BloodConnect</h1>
        <p style="color:#FED7D7;margin:5px 0 0">Emergency Blood Alert</p>
      </div>
      <div style="padding:25px;background:#ffffff;border:1px solid #E2E8F0">

        <div style="background:{color};color:white;padding:10px 15px;
                    border-radius:6px;text-align:center;margin-bottom:20px">
          <strong>⚠️ {urgency.upper()} REQUEST</strong>
        </div>

        <h2 style="color:#1A202C">Dear {donor_name},</h2>
        <p style="color:#4A5568">
          A patient urgently needs <strong>{blood_group}</strong> blood.
          Your blood group matches. <strong>Please respond immediately.</strong>
        </p>

        <!-- PATIENT DETAILS -->
        <div style="background:#FFF5F5;border:2px solid #FC8181;
                    padding:18px;border-radius:8px;margin:15px 0">
          <h3 style="color:#C53030;margin:0 0 12px">🏥 Patient Details</h3>
          <table style="width:100%;border-collapse:collapse">
            <tr>
              <td style="padding:5px 0;color:#718096;width:130px"><strong>Patient</strong></td>
              <td style="padding:5px 0">{patient_name}</td>
            </tr>
            <tr>
              <td style="padding:5px 0;color:#718096"><strong>Blood Group</strong></td>
              <td style="padding:5px 0;color:#C53030;font-size:18px;font-weight:bold">{blood_group}</td>
            </tr>
            <tr>
              <td style="padding:5px 0;color:#718096"><strong>Units Needed</strong></td>
              <td style="padding:5px 0">{units_needed} unit(s)</td>
            </tr>
            <tr>
              <td style="padding:5px 0;color:#718096"><strong>Hospital</strong></td>
              <td style="padding:5px 0">{hospital_name}</td>
            </tr>
            <tr>
              <td style="padding:5px 0;color:#718096"><strong>City</strong></td>
              <td style="padding:5px 0">{city}</td>
            </tr>
          </table>
        </div>

        <!-- CONTACT NUMBER — MOST IMPORTANT PART -->
        <div style="background:#F0FFF4;border:2px solid #68D391;
                    padding:18px;border-radius:8px;margin:15px 0;text-align:center">
          <p style="color:#276749;font-size:13px;font-weight:bold;
                    text-transform:uppercase;letter-spacing:1px;margin:0 0 8px">
            📞 Call This Number Immediately
          </p>
          <p style="font-size:28px;font-weight:900;color:#1A202C;margin:0">{contact_phone}</p>
          <p style="color:#276749;margin:5px 0 0">{contact_person} (Contact Person)</p>
          <p style="color:#718096;font-size:12px;margin:8px 0 0">
            Call before going to hospital to confirm they still need blood
          </p>
        </div>

        <!-- ACTION BUTTONS -->
        <div style="text-align:center;margin:25px 0">
          <a href="{base_url}/confirm/{alert_id}/yes"
             style="background:#38A169;color:white;padding:14px 28px;
                    border-radius:8px;text-decoration:none;font-weight:bold;
                    font-size:16px;margin-right:10px;display:inline-block">
            ✅ YES I AM COMING
          </a>
          <a href="{base_url}/confirm/{alert_id}/no"
             style="background:#E53E3E;color:white;padding:14px 28px;
                    border-radius:8px;text-decoration:none;font-weight:bold;
                    font-size:16px;display:inline-block">
            ❌ CANNOT HELP
          </a>
        </div>

        <!-- STAGE 3 REMINDER -->
        <div style="background:#EBF8FF;border:1px solid #90CDF4;
                    border-radius:8px;padding:15px;margin:15px 0">
          <h4 style="color:#2B6CB0;margin:0 0 8px">📋 After Visiting Hospital</h4>
          <p style="color:#2B6CB0;font-size:13px;margin:0">
            Please return to BloodConnect and report what happened —
            whether you donated, blood was not needed, or you were rejected.
            This keeps your cooldown timer accurate and helps other donors.
          </p>
        </div>

        <p style="color:#718096;font-size:12px;text-align:center">
          ⚕️ Do not donate if you donated blood in the last 56 days,
          are on medication, have fever, or feel unwell.
        </p>
      </div>
      <div style="background:#F7FAFC;padding:12px;text-align:center;
                  color:#A0AEC0;font-size:12px;border-radius:0 0 8px 8px">
        BloodConnect — Every drop counts 🩸
      </div>
    </div>
    """
    return _send(donor_email, subject, html)


# ═══════════════════════════════════════════════════
# EMAIL 3 — REQUESTER CONFIRMATION
# Sent when: Blood request alerts are fired
# Purpose: Tells patient family how many donors were alerted
# ═══════════════════════════════════════════════════

def send_requester_confirmation(contact_email, contact_person,
                                 patient_name, blood_group,
                                 donors_alerted, contact_phone):
    subject = f"✅ {donors_alerted} Donors Alerted for {patient_name}"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
      <div style="background:#C53030;padding:20px;text-align:center;border-radius:8px 8px 0 0">
        <h1 style="color:white;margin:0">🩸 BloodConnect</h1>
      </div>
      <div style="padding:25px;background:#ffffff;border:1px solid #E2E8F0">
        <h2 style="color:#1A202C">Dear {contact_person},</h2>
        <p>Your blood request for <strong>{patient_name}</strong> ({blood_group}) has been received.</p>
        <div style="background:#F0FFF4;border-left:4px solid #38A169;
                    padding:15px;border-radius:4px;margin:20px 0">
          <p style="margin:0;font-size:18px">
            ✅ <strong>{donors_alerted} matching donors</strong> have been alerted in your city.
          </p>
        </div>
        <p style="color:#4A5568">
          Donors who can help will <strong>call you directly on {contact_phone}</strong>.
          Please keep your phone available and answer unknown numbers.
        </p>
        <p style="color:#718096;font-size:13px">
          You can track donor responses in real time on your BloodConnect dashboard.
        </p>
      </div>
    </div>
    """
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
        <div style='background:#F0FFF4;border-left:4px solid #38A169;
                    padding:15px;border-radius:4px;margin:20px 0'>
          <h3 style='color:#276749;margin:0'>You are a hero! 🦸</h3>
          <p style='color:#276749;margin:8px 0 0'>
            Your donation has been recorded. Your 56-day cooldown has started.
            You will be automatically eligible again after this period.
          </p>
        </div>
        """
    elif outcome == "NotNeeded":
        subject = "💙 Thank You for Showing Up, " + donor_name
        msg = """
        <div style='background:#EBF8FF;border-left:4px solid #63B3ED;
                    padding:15px;border-radius:4px;margin:20px 0'>
          <h3 style='color:#2B6CB0;margin:0'>Thank you for trying! 💙</h3>
          <p style='color:#2B6CB0;margin:8px 0 0'>
            We understand the blood was no longer needed when you arrived.
            Your willingness to help matters greatly.
            Your cooldown timer has NOT been affected — you are still eligible to donate.
          </p>
        </div>
        """
    else:  # Rejected
        subject = "💙 Thank You for Coming, " + donor_name
        msg = """
        <div style='background:#FFFFF0;border-left:4px solid #ECC94B;
                    padding:15px;border-radius:4px;margin:20px 0'>
          <h3 style='color:#744210;margin:0'>Thank you for your effort 💙</h3>
          <p style='color:#744210;margin:8px 0 0'>
            We are sorry you were not able to donate this time.
            Your cooldown timer has NOT been affected.
            Please consult a doctor about your eligibility for future donations.
          </p>
        </div>
        """

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
      <div style="background:#C53030;padding:20px;text-align:center;border-radius:8px 8px 0 0">
        <h1 style="color:white;margin:0">🩸 BloodConnect</h1>
      </div>
      <div style="padding:25px;background:#ffffff;border:1px solid #E2E8F0">
        <h2 style="color:#1A202C">Dear {donor_name},</h2>
        {msg}
        <p style="color:#718096;font-size:13px">
          Thank you for being part of BloodConnect. Your presence in our
          donor network saves lives.
        </p>
      </div>
    </div>
    """
    return _send(donor_email, subject, html)


# ═══════════════════════════════════════════════════
# BULK ALERT FUNCTION
# PURPOSE: Sends alerts to ALL matching donors at once
# WHY: A blood emergency needs ALL possible donors
#      alerted simultaneously, not one by one
# ═══════════════════════════════════════════════════

def send_bulk_alerts(donors, req_id, patient_name, blood_group,
                     units_needed, hospital_name, city,
                     contact_person, contact_phone, urgency,
                     log_alert_fn):
    """
    Loops through all matching donors and sends each one
    a personalized alert email with their own confirm/decline links.

    RETURNS: (sent_count, failed_count)
    """
    sent = 0
    failed = 0

    logger.info(f"Starting bulk alert: {len(donors)} donors | request={req_id}")

    for donor in donors:
        # Log alert in DB first to get unique alert_id
        # Each donor gets their own unique alert_id
        # This is important — confirm/decline links use alert_id
        alert_id = log_alert_fn(req_id, donor["id"])

        success = send_donor_alert(
            donor_email    = donor["email"],
            donor_name     = donor["name"],
            alert_id       = alert_id,
            patient_name   = patient_name,
            blood_group    = blood_group,
            units_needed   = units_needed,
            hospital_name  = hospital_name,
            city           = city,
            contact_person = contact_person,
            contact_phone  = contact_phone,
            urgency        = urgency
        )

        if success:
            sent += 1
        else:
            failed += 1

    logger.info(f"Bulk alert complete: sent={sent} failed={failed}")
    return sent, failed
