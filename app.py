"""
app.py — BloodConnect Main Application
=======================================
PURPOSE: This is the heart of the application.
It defines all the URLs (routes) and what happens
when someone visits each URL.

HOW FLASK WORKS:
When someone opens localhost:5000/register in their browser:
1. Browser sends request to Flask
2. Flask looks for @app.route("/register")
3. Runs the function below that decorator
4. Function returns HTML page
5. Browser displays it

Think of routes as menu items and functions as chefs
who prepare the response for each menu item.
"""

from flask import (Flask, render_template, request,
                   redirect, url_for, flash)
from dotenv import load_dotenv
from datetime import datetime
import logging
import os

# Load .env file FIRST — before importing database or email
# This ensures EMAIL_ADDRESS and EMAIL_PASSWORD are available
load_dotenv()

# Import our modules
from database import (
    init_db, add_donor, get_all_donors, get_donor_by_id,
    toggle_availability, get_matching_donors, get_cooldown_info,
    add_blood_request, get_all_requests, get_request_with_alerts,
    update_request_status, log_alert, donor_confirms_coming,
    donor_declines, record_donation_outcome, get_alert_by_id,
    get_stats, expire_old_requests, get_donor_alerts, get_donor_by_email
)
from email_sender import (
    send_registration_confirmation, send_bulk_alerts,
    send_requester_confirmation, send_outcome_thankyou
)

from apscheduler.schedulers.background import BackgroundScheduler

# ── LOGGING SETUP ────────────────────────────────
# PURPOSE: Records everything that happens in the app
# WHY: When something goes wrong we need to know what happened.
#      Logs are like a black box recorder for your app.
os.makedirs("logs",    exist_ok=True)
os.makedirs("reports", exist_ok=True)

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s | %(levelname)s | %(module)s | %(message)s",
    handlers = [
        logging.FileHandler(
            f"logs/app_{datetime.today().strftime('%Y-%m-%d')}.log",
            encoding="utf-8"
        ),
        logging.StreamHandler()  # Also print to terminal
    ]
)
logger = logging.getLogger(__name__)

# ── FLASK APP SETUP ──────────────────────────────
app = Flask(__name__)

# secret_key is required for flash messages to work
# In production this should be a long random string stored in .env
app.secret_key = os.environ.get("SECRET_KEY", "bloodconnect_dev_2024")

# Initialize database when app starts
init_db()
logger.info("BloodConnect application started")


# ════════════════════════════════════════════════════════
# HOME PAGE
# URL: localhost:5000/
# PURPOSE: Landing page with stats and urgent feed
# ════════════════════════════════════════════════════════

@app.route("/")
def index():
    stats = get_stats()
    # Get 5 most recent open requests for urgency feed
    all_reqs = get_all_requests()
    urgent_feed = [r for r in all_reqs if r["status"] == "Open"][:5]
    logger.info("Home page accessed")
    return render_template("index.html", stats=stats, feed=urgent_feed)


ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect(url_for("dashboard"))
        flash("Wrong password", "error")
    return render_template("admin_login.html")

from werkzeug.security import generate_password_hash, check_password_hash
from flask import session

@app.route("/donor/login", methods=["GET", "POST"])
def donor_login():
    if request.method == "POST":
        email    = request.form.get("email")
        password = request.form.get("password")
        donor    = get_donor_by_email(email)

        if donor and check_password_hash(donor["password_hash"], password):
            session["donor_id"] = donor["id"]
            session["donor_name"] = donor["name"]
            flash(f"Welcome back {donor['name']}!", "success")
            return redirect(url_for("my_profile"))
        flash("Wrong email or password", "error")
    return render_template("donor_login.html")

@app.route("/my-profile")
def my_profile():
    if "donor_id" not in session:
        return redirect(url_for("donor_login"))
    donor    = get_donor_by_id(session["donor_id"])
    cooldown = get_cooldown_info(donor)
    alerts   = get_donor_alerts(session["donor_id"])
    return render_template("my_profile.html",
                           donor=donor,
                           cooldown=cooldown,
                           alerts=alerts)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ════════════════════════════════════════════════════════
# DONOR REGISTRATION
# URL: localhost:5000/register
# PURPOSE: New donor signs up
# FLOW: Fill form → validate → save to DB → send email → redirect
# ════════════════════════════════════════════════════════

@app.route("/register", methods=["GET", "POST"])
def register():
    """
    GET request  → show empty registration form
    POST request → process submitted form data
    """
    if request.method == "POST":
        # Get form data
        # request.form is a dictionary of all form field values
        name        = request.form.get("name",        "").strip()
        email       = request.form.get("email",       "").strip()
        phone       = request.form.get("phone",       "").strip()
        age_str     = request.form.get("age",         "0")
        blood_group = request.form.get("blood_group", "")
        city        = request.form.get("city",        "").strip()
        area        = request.form.get("area",        "").strip()
        password    = request.form.get("password",    "").strip()

        # VALIDATION 1 — Check all required fields are filled
        if not all([name, email, phone, blood_group, city, area, password]):
            flash("Please fill all required fields.", "error")
            return render_template("register.html")

        # VALIDATION 2 — Age must be 18-65 (WHO guideline)
        try:
            age = int(age_str)
        except ValueError:
            flash("Please enter a valid age.", "error")
            return render_template("register.html")

        if age < 18:
            flash("Minimum age to donate blood is 18 years.", "error")
            return render_template("register.html")

        if age > 65:
            flash("Maximum age to donate blood is 65 years.", "error")
            return render_template("register.html")

        # VALIDATION 3 — Phone must be 10 digits
        if len(phone.replace(" ", "")) != 10:
            flash("Please enter a valid 10 digit phone number.", "error")
            return render_template("register.html")

        logger.info(f"Registration attempt: {name} | {blood_group} | {city}")

        # Save to database
        donor_id = add_donor(name, email, phone, age,
                             blood_group, city, area, generate_password_hash(password))

        # If donor_id is None — email already registered
        if not donor_id:
            flash("This email is already registered. Each email can only register once.", "error")
            return render_template("register.html")

        # Send welcome email (don't fail if email fails)
        send_registration_confirmation(email, name, blood_group, donor_id)

        flash(
            f"Welcome {name}! You are now registered as a {blood_group} donor. "
            f"Check your email for confirmation.",
            "success"
        )
        logger.info(f"Donor registered: id={donor_id} | {name} | {blood_group} | {city}")
        return redirect(url_for("dashboard"))

    # GET request — just show the empty form
    return render_template("register.html")


# ════════════════════════════════════════════════════════
# BLOOD REQUEST
# URL: localhost:5000/request-blood
# PURPOSE: Patient family submits emergency request
# FLOW: Fill form → validate → find matching donors →
#       send bulk emails → save → redirect to tracking page
# ════════════════════════════════════════════════════════

@app.route("/request-blood", methods=["GET", "POST"])
def request_blood():
    if request.method == "POST":
        patient_name   = request.form.get("patient_name",   "").strip()
        blood_group    = request.form.get("blood_group",    "")
        units_str      = request.form.get("units_needed",   "1")
        hospital_name  = request.form.get("hospital_name",  "").strip()
        city           = request.form.get("city",           "").strip()
        contact_person = request.form.get("contact_person", "").strip()
        contact_phone  = request.form.get("contact_phone",  "").strip()
        urgency        = request.form.get("urgency",        "Normal")

        # Validate required fields
        if not all([patient_name, blood_group, hospital_name,
                    city, contact_person, contact_phone]):
            flash("Please fill all required fields.", "error")
            return render_template("request_blood.html")

        # Validate phone
        if len(contact_phone.replace(" ", "")) != 10:
            flash("Please enter a valid 10 digit contact phone number.", "error")
            return render_template("request_blood.html")

        try:
            units = int(units_str)
        except ValueError:
            units = 1

        logger.info(
            f"Blood request: {blood_group} | {city} | "
            f"urgency={urgency} | hospital={hospital_name}"
        )

        # Find matching donors — this includes cooldown check
        # Only eligible donors are returned
        matching = get_matching_donors(blood_group, city)

        # Save request regardless of whether donors found
        req_id = add_blood_request(
            patient_name, blood_group, units, hospital_name,
            city, contact_person, contact_phone, urgency
        )

        if not matching:
            # No donors found — still save request
            flash(
                f"Request saved but no available {blood_group} donors found "
                f"in {city} right now. Try nearby cities or check back later.",
                "warning"
            )
            logger.warning(f"No matching donors: {blood_group} | {city}")
            return redirect(url_for("request_detail", req_id=req_id))

        # Send bulk email alerts to all matching donors
        sent, failed = send_bulk_alerts(
            donors         = matching,
            req_id         = req_id,
            patient_name   = patient_name,
            blood_group    = blood_group,
            units_needed   = units,
            hospital_name  = hospital_name,
            city           = city,
            contact_person = contact_person,
            contact_phone  = contact_phone,
            urgency        = urgency,
            log_alert_fn   = log_alert  # Pass the log_alert function
        )

        # Send confirmation to the requester
        contact_email = request.form.get("contact_email", "").strip()
        if contact_email:
            send_requester_confirmation(
                contact_email, contact_person,
                patient_name, blood_group, sent, contact_phone
            )

        flash(
            f"🩸 Alert sent to {sent} matching {blood_group} donors in {city}! "
            f"They will call {contact_phone} directly.",
            "success"
        )
        logger.info(f"Alerts sent: req={req_id} sent={sent} failed={failed}")
        return redirect(url_for("request_detail", req_id=req_id))

    return render_template("request_blood.html")


# ════════════════════════════════════════════════════════
# REQUEST DETAIL / TRACKING PAGE
# URL: localhost:5000/request/5
# PURPOSE: Shows a blood request with all donor responses
# ════════════════════════════════════════════════════════

@app.route("/request/<int:req_id>")
def request_detail(req_id):
    req, alerts = get_request_with_alerts(req_id)
    if not req:
        flash("Request not found.", "error")
        return redirect(url_for("dashboard"))
    return render_template("request_detail.html", req=req, alerts=alerts)


@app.route("/request/<int:req_id>/close", methods=["POST"])
def close_request(req_id):
    update_request_status(req_id, "Closed")
    flash("Request closed.", "success")
    return redirect(url_for("dashboard"))


# ════════════════════════════════════════════════════════
# STAGE 2 — DONOR CONFIRMS COMING
# URL: localhost:5000/confirm/15/yes  OR  /confirm/15/no
# PURPOSE: Donor clicks YES or NO in their email
# NOTE: Clicking YES does NOT start cooldown.
#       It only means donor is coming to hospital.
#       Cooldown starts only after Stage 3 outcome.
# ════════════════════════════════════════════════════════

@app.route("/confirm/<int:alert_id>/<response>")
def confirm(alert_id, response):
    alert = get_alert_by_id(alert_id)
    if not alert:
        flash("Invalid link.", "error")
        return redirect(url_for("index"))

    if response == "yes":
        donor_confirms_coming(alert_id)
        logger.info(f"Donor confirmed coming: alert={alert_id}")
        # Show confirmation page with Stage 3 options pre-loaded
        return render_template("confirm.html", alert=alert, confirmed=True)

    elif response == "no":
        donor_declines(alert_id)
        logger.info(f"Donor declined: alert={alert_id}")
        return render_template("confirm.html", alert=alert, confirmed=False)

    return redirect(url_for("index"))


# ════════════════════════════════════════════════════════
# STAGE 3 — DONATION OUTCOME
# URL: localhost:5000/outcome/15
# PURPOSE: Donor reports what ACTUALLY happened at hospital
#
# This is the key fix for the problem you identified.
# Three possible outcomes:
#   donated    → Blood successfully donated → START 56 day cooldown
#   notneeded  → Blood not needed anymore   → NO cooldown
#   rejected   → Medically rejected         → NO cooldown
# ════════════════════════════════════════════════════════

@app.route("/outcome/<int:alert_id>", methods=["GET", "POST"])
def outcome(alert_id):
    alert = get_alert_by_id(alert_id)
    if not alert:
        flash("Invalid link.", "error")
        return redirect(url_for("index"))

    if request.method == "POST":
        outcome_value = request.form.get("outcome", "")

        if outcome_value not in ["Donated", "NotNeeded", "Rejected"]:
            flash("Please select an outcome.", "error")
            return render_template("outcome.html", alert=alert)

        # THIS IS WHERE COOLDOWN IS DECIDED
        record_donation_outcome(alert_id, outcome_value)

        # Send thank you email to donor
        send_outcome_thankyou(
            alert["donor_email"] if "donor_email" in alert.keys()
            else "",
            alert["donor_name"],
            outcome_value
        )

        logger.info(
            f"Outcome recorded: alert={alert_id} outcome={outcome_value}"
        )

        return render_template("outcome_done.html",
                               alert=alert,
                               outcome=outcome_value)

    return render_template("outcome.html", alert=alert)


# ════════════════════════════════════════════════════════
# DASHBOARD
# URL: localhost:5000/dashboard
# PURPOSE: Shows all requests and donors
# ════════════════════════════════════════════════════════

@app.route("/dashboard")
def dashboard():
    donors   = get_all_donors()
    requests = get_all_requests()
    stats    = get_stats()
    logger.info("Dashboard accessed")
    return render_template("dashboard.html",
                           donors=donors,
                           requests=requests,
                           stats=stats)


# ════════════════════════════════════════════════════════
# DONOR PROFILE
# URL: localhost:5000/donor/5
# PURPOSE: Shows individual donor details and cooldown status
# ════════════════════════════════════════════════════════

@app.route("/donor/<int:donor_id>")
def donor_profile(donor_id):
    donor = get_donor_by_id(donor_id)
    if not donor:
        flash("Donor not found.", "error")
        return redirect(url_for("dashboard"))
    cooldown = get_cooldown_info(donor)
    return render_template("donor_profile.html",
                           donor=donor,
                           cooldown=cooldown)


@app.route("/donor/<int:donor_id>/toggle", methods=["POST"])
def toggle(donor_id):
    toggle_availability(donor_id)
    flash("Availability updated!", "success")
    return redirect(url_for("donor_profile", donor_id=donor_id))


# ════════════════════════════════════════════════════════
# RUN THE APP
# ════════════════════════════════════════════════════════

if __name__ == "__main__":
    logger.info("Starting BloodConnect development server")
    # debug=True means:
    # 1. Auto-reloads when you save a file
    # 2. Shows detailed error pages
    # 3. NEVER use debug=True in production
   
    scheduler = BackgroundScheduler()
    scheduler.add_job(expire_old_requests, 'interval', minutes=1)  # testing
    scheduler.start()
    app.run(debug=True, port=5000, host="0.0.0.0")

