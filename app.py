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

SECURITY RULES USED EVERYWHERE IN THIS FILE:
- @login_required / @admin_required protect private pages.
- Every POST form is checked for a CSRF token (security.csrf_protect).
- All input is validated on the server (validators.py).
- Private request details (patient name, contact phone, private note)
  are loaded ONLY after request_access() says the viewer may see them.
"""

from flask import (Flask, render_template, request, redirect, url_for,
                   flash, session, g, abort)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
from datetime import datetime, timedelta
import hmac
import logging
import os
import time

# Load .env file FIRST — before importing database or email
# This ensures EMAIL_ADDRESS and EMAIL_PASSWORD are available
load_dotenv()

# Import our modules
from database import (
    init_db, get_all_donors, get_donor_by_id, add_donor,
    toggle_availability, set_availability, set_account_active,
    get_matching_donors, get_cooldown_info, get_donor_by_email,
    email_in_use, phone_in_use, update_profile, update_password,
    add_blood_request, find_duplicate_active_request, get_all_requests,
    get_request_by_id, get_public_request, get_private_request_details,
    search_requests, get_requests_by_owner, get_request_with_alerts,
    get_accepted_donors, get_response_summary, get_request_events,
    update_request_status, update_request_details, get_confirmed_donor_ids,
    log_alert, get_alert_for, accept_request, decline_request,
    withdraw_acceptance, record_donation_outcome, get_alert_by_id,
    get_donor_alerts, add_notification, add_notifications,
    get_notifications, unread_notification_count, mark_notifications_read,
    get_stats, expire_old_requests,
    ACTIVE_STATUSES, ALL_STATUSES, STATUS_ASSIGNED, STATUS_IN_PROGRESS,
    STATUS_COMPLETED, STATUS_CANCELLED, COOLDOWN_DAYS
)
from email_sender import (
    send_registration_confirmation, send_bulk_alerts,
    send_requester_confirmation, send_outcome_thankyou, send_status_update
)
from validators import (
    BLOOD_GROUPS, URGENCY_LEVELS, ROLES, PASSWORD_RULES, RECEIVE_FROM,
    DONATE_TO, can_donate, normalize_email, validate_name, validate_email,
    validate_phone, validate_password, validate_age, validate_blood_group,
    validate_place, validate_units, validate_urgency, validate_note,
    validate_required_by
)
from security import (
    load_secret_key, csrf_field, csrf_protect, auth_version, is_safe_next,
    login_required, admin_required, client_ip, is_rate_limited, hit, reset,
    add_security_headers
)

from apscheduler.schedulers.background import BackgroundScheduler

# ── LOGGING SETUP ────────────────────────────────
# PURPOSE: Records everything that happens in the app
# WHY: When something goes wrong we need to know what happened.
#      Logs are like a black box recorder for your app.
# PRIVACY: We log account/request IDs, never passwords, phone numbers
#          or patient names.
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

# secret_key signs the login cookie. Taken from .env (SECRET_KEY) or,
# if missing, generated once and saved in .flask_secret_key (not in git).
app.secret_key = load_secret_key()

app.config.update(
    SESSION_COOKIE_HTTPONLY   = True,    # JavaScript cannot read the login cookie
    SESSION_COOKIE_SAMESITE   = "Lax",   # other sites cannot send it with their forms
    SESSION_COOKIE_SECURE     = os.environ.get("SESSION_COOKIE_SECURE") == "1",  # HTTPS only (production)
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8),  # absolute maximum login time
    MAX_CONTENT_LENGTH        = 64 * 1024,            # reject huge form posts
)
# Log out automatically after this many minutes without any activity
IDLE_TIMEOUT_MINUTES = int(os.environ.get("SESSION_IDLE_MINUTES", "60"))

# Behind a hosting proxy (Railway, Render, Nginx) set TRUST_PROXY=1 so
# rate limiting sees the real visitor IP address.
if os.environ.get("TRUST_PROXY") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

# Admin password comes ONLY from .env — there is no default any more.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

# Times are stored in UTC; shown to users in this offset (330 = India, UTC+5:30)
DISPLAY_OFFSET = timedelta(minutes=int(os.environ.get("DISPLAY_UTC_OFFSET_MINUTES", "330")))

# Used to keep failed-login timing the same whether or not the email exists
DUMMY_HASH = generate_password_hash("timing-equaliser-not-a-password")

# Initialize database when app starts
init_db()
logger.info("BloodConnect application started")

# ── BLOOD DONATION FACTS ─────────────────────────
# Every fact links to the source it came from. Please keep it that way
# if you add more — never add medical claims without a reliable source.
WHO_FACTS = "https://www.who.int/news-room/fact-sheets/detail/blood-safety-and-availability"
NHSBT_TYPES = "https://www.blood.co.uk/why-give-blood/blood-types/"
BLOOD_FACTS = [
    {"text": "Your blood group is decided by the A and B antigens (ABO group) and the RhD antigen "
             "(positive or negative) on your red blood cells.",
     "source": "NHS Blood and Transplant", "url": NHSBT_TYPES},
    {"text": "O negative red cells are safe for patients of any blood group — that is why "
             "O negative donors are called 'universal donors'.",
     "source": "NHS Blood and Transplant", "url": NHSBT_TYPES},
    {"text": "Patients with AB positive blood can receive red cells from all eight main blood "
             "groups under standard ABO/RhD compatibility rules.",
     "source": "ABO/RhD compatibility (see chart)", "url": "#compatibility"},
    {"text": "Donated blood can be separated into red cells, platelets, plasma and cryoprecipitate, "
             "so one donation can meet the needs of more than one patient.",
     "source": "World Health Organization", "url": WHO_FACTS},
    {"text": "WHO says screening all donated blood for HIV, hepatitis B, hepatitis C and syphilis "
             "should be mandatory.",
     "source": "World Health Organization", "url": WHO_FACTS},
    {"text": "WHO reports 28.9 blood donations per 1,000 people in high-income countries, but only "
             "4.5 per 1,000 in low-income countries.",
     "source": "World Health Organization", "url": WHO_FACTS},
    {"text": "Around 2% of NHS Blood and Transplant donors have AB positive blood, one of the "
             "rarest groups.",
     "source": "NHS Blood and Transplant", "url": NHSBT_TYPES},
]

OUTCOME_LABELS = {"Donated": "donated successfully",
                  "NotNeeded": "found the blood was no longer needed",
                  "Rejected": "could not donate after the medical check"}


# ════════════════════════════════════════════════════════
# REQUEST HOOKS
# PURPOSE: Code that runs before/after EVERY page
# ════════════════════════════════════════════════════════

@app.before_request
def load_logged_in_user():
    """
    Loads the logged-in account into g.user and enforces:
    - idle timeout (session expires after inactivity)
    - deactivated accounts are signed out
    - a password change signs out all other sessions
    """
    g.user = None
    g.is_admin = False
    if request.endpoint == "static":
        return

    if "donor_id" in session or session.get("is_admin"):
        last_seen = session.get("last_seen", 0)
        if time.time() - last_seen > IDLE_TIMEOUT_MINUTES * 60:
            session.clear()
            flash("Your session expired after a period of inactivity. Please log in again.", "warning")
            return
        session["last_seen"] = int(time.time())

    if session.get("is_admin"):
        g.is_admin = True

    donor_id = session.get("donor_id")
    if donor_id:
        user = get_donor_by_id(donor_id)
        if (not user or not user["is_active"]
                or session.get("auth_ver") != auth_version(user)):
            session.clear()
            flash("Please log in again.", "warning")
            return
        g.user = user


@app.before_request
def check_csrf():
    csrf_protect()


_last_expiry_check = 0


@app.before_request
def expire_requests_periodically():
    """
    Expires old requests at most once a minute. This works even when the
    app runs under gunicorn (where the APScheduler block below does not run).
    """
    global _last_expiry_check
    if request.endpoint == "static" or time.time() - _last_expiry_check < 60:
        return
    _last_expiry_check = time.time()
    try:
        _expire_and_notify()
    except Exception:
        logger.exception("Request expiry check failed")


def _expire_and_notify():
    for req_id, owner_id, donor_ids in expire_old_requests():
        link = f"/request/{req_id}"
        if owner_id:
            add_notification(owner_id, "Your blood request expired. You can create a new one if you still need blood.", link)
        add_notifications(donor_ids, "A request you accepted has expired. Its contact details are no longer shared.", link)


@app.after_request
def security_headers(response):
    return add_security_headers(response)


@app.context_processor
def inject_globals():
    unread = unread_notification_count(g.user["id"]) if g.get("user") else 0
    return {
        "csrf_field": csrf_field,
        "current_user": g.get("user"),
        "is_admin": g.get("is_admin"),
        "unread_count": unread,
        "BLOOD_GROUPS": BLOOD_GROUPS,
        "URGENCY_LEVELS": URGENCY_LEVELS,
        "ALL_STATUSES": ALL_STATUSES,
        "ACTIVE_STATUSES": ACTIVE_STATUSES,
        "PASSWORD_RULES": [text for _, text in PASSWORD_RULES],
        "COOLDOWN_DAYS": COOLDOWN_DAYS,
        "current_year": datetime.today().year,
    }


@app.template_filter("localtime")
def localtime_filter(value, fmt="%d %b %Y, %I:%M %p"):
    """Converts a UTC database timestamp into the display timezone."""
    if not value:
        return ""
    try:
        dt = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(value)
    return (dt + DISPLAY_OFFSET).strftime(fmt)


@app.template_filter("nicedate")
def nicedate_filter(value):
    if not value:
        return ""
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime("%d %b %Y")
    except ValueError:
        return str(value)


@app.template_filter("slug")
def slug_filter(value):
    return str(value or "").lower().replace(" ", "-")


# ════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════

def form_text(name, max_len=300):
    """Reads one form field, trims spaces, and caps its length."""
    return (request.form.get(name) or "").strip()[:max_len]


def redirect_next(default_endpoint):
    target = request.args.get("next") or request.form.get("next")
    if is_safe_next(target):
        return redirect(target)
    return redirect(url_for(default_endpoint))


def start_session(donor):
    """Signs a user in. session.clear() first so no old data carries over."""
    session.clear()
    session.permanent = True
    session["donor_id"]   = donor["id"]
    session["donor_name"] = donor["name"]
    session["auth_ver"]   = auth_version(donor)
    session["last_seen"]  = int(time.time())


def request_access(req):
    """
    THE PRIVACY GATE. Decides what the current viewer may see for a request.
    RETURNS:
      'admin'    — administrator: everything
      'owner'    — the account that created the request: everything
      'accepted' — a donor who ACCEPTED this request while it is still active:
                   the contact details needed to coordinate
      'donor'    — any other logged-in user: public details only
      'public'   — not logged in
    Checked on EVERY request from the database — changing the ID in the
    URL cannot bypass it, and access disappears as soon as the request is
    cancelled/completed/expired or the donor withdraws.
    """
    if g.get("is_admin"):
        return "admin"
    user = g.get("user")
    if not user:
        return "public"
    if req["owner_id"] and req["owner_id"] == user["id"]:
        return "owner"
    if req["status"] in ACTIVE_STATUSES:
        alert = get_alert_for(req["id"], user["id"])
        if alert and alert["stage"] == "Confirmed":
            return "accepted"
    return "donor"


def can_manage(req):
    return request_access(req) in ("admin", "owner")


def notify_and_email(user_ids, message, link, headline, req_id):
    """In-app notification + (if email is configured) an email to each user."""
    add_notifications(user_ids, message, link)
    for uid in user_ids:
        person = get_donor_by_id(uid)
        if person:
            send_status_update(person["email"], person["name"], headline, message, req_id)


def read_request_form():
    """Reads + validates the blood request form. RETURNS (data, errors)."""
    errors = {}
    data = {
        "patient_name":   form_text("patient_name", 60),
        "blood_group":    form_text("blood_group", 3),
        "units_needed":   form_text("units_needed", 3),
        "hospital_name":  form_text("hospital_name", 120),
        "city":           form_text("city", 60),
        "contact_person": form_text("contact_person", 60),
        "contact_phone":  form_text("contact_phone", 15),
        "urgency":        form_text("urgency", 10),
        "required_by":    form_text("required_by", 10),
        "public_note":    form_text("public_note", 400),
        "private_note":   form_text("private_note", 400),
    }
    checks = {
        "patient_name":   validate_name(data["patient_name"], "Patient name"),
        "blood_group":    validate_blood_group(data["blood_group"]),
        "hospital_name":  validate_place(data["hospital_name"], "Hospital name", 120),
        "city":           validate_place(data["city"], "City"),
        "contact_person": validate_name(data["contact_person"], "Contact person"),
        "contact_phone":  validate_phone(data["contact_phone"], "Contact phone"),
        "urgency":        validate_urgency(data["urgency"]),
        "public_note":    validate_note(data["public_note"], "Public description", public=True),
        "private_note":   validate_note(data["private_note"], "Private note"),
    }
    data["units_needed_int"], checks["units_needed"] = validate_units(data["units_needed"])
    data["required_by_iso"], checks["required_by"] = validate_required_by(data["required_by"])
    errors = {k: v for k, v in checks.items() if v}
    return data, errors


# ════════════════════════════════════════════════════════
# HOME PAGE
# URL: localhost:5000/
# PURPOSE: Landing page with stats, facts and the urgent feed
# PRIVACY: The feed uses search_requests() → public columns only
# ════════════════════════════════════════════════════════

@app.route("/")
def index():
    stats = get_stats()
    urgent_feed = search_requests(status="active", limit=6)
    logger.info("Home page accessed")
    return render_template("index.html", stats=stats, feed=urgent_feed,
                           facts=BLOOD_FACTS, receive_from=RECEIVE_FROM,
                           donate_to=DONATE_TO)


# ════════════════════════════════════════════════════════
# BROWSE / SEARCH REQUESTS
# URL: localhost:5000/requests?blood_group=O%2B&city=Mumbai
# PURPOSE: Donors browse active requests. Public columns only.
# ════════════════════════════════════════════════════════

@app.route("/requests")
def browse_requests():
    bg      = request.args.get("blood_group", "")
    city    = request.args.get("city", "").strip()[:60]
    urgency = request.args.get("urgency", "")
    status  = request.args.get("status", "active")
    user    = g.get("user")
    is_donor = bool(user and user["role"] == "donor" and user["blood_group"] in BLOOD_GROUPS)
    compatible = request.args.get("compatible", "1" if is_donor else "0") == "1" and is_donor

    bg      = bg if bg in BLOOD_GROUPS else ""
    urgency = urgency if urgency in URGENCY_LEVELS else ""
    status  = status if status in ("active", "all") + ALL_STATUSES else "active"

    results = search_requests(blood_group=bg or None, city=city or None,
                              urgency=urgency or None, status=status,
                              compatible_with=user["blood_group"] if compatible else None)
    filters = {"blood_group": bg, "city": city, "urgency": urgency,
               "status": status, "compatible": compatible}
    return render_template("requests.html", requests=results, filters=filters,
                           is_donor=is_donor)


# ════════════════════════════════════════════════════════
# ADMIN LOGIN
# URL: localhost:5000/admin/login
# ════════════════════════════════════════════════════════

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if not ADMIN_PASSWORD:
            flash("Admin login is disabled. Set ADMIN_PASSWORD in your .env file and restart the app.", "error")
            return render_template("admin_login.html"), 503
        ip = client_ip()
        if is_rate_limited("admin", ip, 5, 15):
            flash("Too many failed attempts. Please wait 15 minutes and try again.", "error")
            return render_template("admin_login.html"), 429
        entered = request.form.get("password", "")
        if hmac.compare_digest(entered.encode(), ADMIN_PASSWORD.encode()):
            reset("admin", ip)
            session.clear()
            session.permanent = True
            session["is_admin"] = True
            session["last_seen"] = int(time.time())
            logger.info("Admin logged in")
            return redirect_next("dashboard")
        hit("admin", ip)
        logger.warning("Failed admin login attempt")
        flash("Incorrect admin password.", "error")
        return render_template("admin_login.html"), 401
    return render_template("admin_login.html")


# ════════════════════════════════════════════════════════
# DONOR / USER LOGIN
# URL: localhost:5000/donor/login
# SECURITY: generic error message, timing-safe check,
#           5 failures per email+IP (or 20 per IP) → 15 minute lock
# ════════════════════════════════════════════════════════

@app.route("/donor/login", methods=["GET", "POST"])
def donor_login():
    if g.get("user") and request.method == "GET":
        return redirect(url_for("my_profile"))

    if request.method == "POST":
        email    = normalize_email(request.form.get("email"))[:254]
        password = request.form.get("password", "")[:128]
        ip       = client_ip()
        key      = f"{email}|{ip}"

        if is_rate_limited("login", key, 5, 15) or is_rate_limited("login_ip", ip, 20, 15):
            flash("Too many failed login attempts. Please wait 15 minutes and try again.", "error")
            return render_template("donor_login.html", email=email), 429

        donor = get_donor_by_email(email) if email else None
        ok = False
        if donor and donor["password_hash"]:
            ok = check_password_hash(donor["password_hash"], password)
            # The old version stripped spaces from passwords at registration
            if not ok and password != password.strip():
                ok = check_password_hash(donor["password_hash"], password.strip())
                password = password.strip() if ok else password
        else:
            check_password_hash(DUMMY_HASH, password)  # same delay as a real check

        if not ok:
            hit("login", key)
            hit("login_ip", ip)
            logger.warning("Failed login attempt")
            flash("Incorrect email or password.", "error")
            return render_template("donor_login.html", email=email), 401

        if not donor["is_active"]:
            flash("This account has been deactivated. Please contact the administrator.", "error")
            return render_template("donor_login.html", email=email), 403

        # Upgrade old password hashes to the current algorithm (scrypt)
        if not donor["password_hash"].startswith("scrypt:"):
            update_password(donor["id"], generate_password_hash(password))
            donor = get_donor_by_id(donor["id"])

        reset("login", key)
        start_session(donor)
        logger.info(f"Login: donor={donor['id']}")
        flash(f"Welcome back {donor['name']}!", "success")
        return redirect_next("my_profile")
    return render_template("donor_login.html")


@app.route("/logout", methods=["GET", "POST"])
def logout():
    # Logging out changes state, so only a POST (with CSRF token) does it.
    if request.method == "POST":
        session.clear()
        flash("You have been logged out.", "success")
    return redirect(url_for("index"))


# ════════════════════════════════════════════════════════
# REGISTRATION
# URL: localhost:5000/register
# PURPOSE: New donor OR recipient signs up
# FLOW: Fill form → validate → save to DB → send email → log in
# ════════════════════════════════════════════════════════

@app.route("/register", methods=["GET", "POST"])
def register():
    """
    GET request  → show empty registration form
    POST request → process submitted form data
    """
    if g.get("user") and request.method == "GET":
        return redirect(url_for("my_profile"))

    if request.method == "POST":
        ip = client_ip()
        form = {
            "role":        form_text("role", 10) or "donor",
            "name":        form_text("name", 60),
            "email":       normalize_email(form_text("email", 254)),
            "phone":       form_text("phone", 15),
            "age":         form_text("age", 3),
            "blood_group": form_text("blood_group", 3),
            "city":        form_text("city", 60),
            "area":        form_text("area", 60),
        }
        # Passwords are NOT trimmed — spaces are allowed characters
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm_password", "")

        if is_rate_limited("register", ip, 10, 60):
            flash("Too many registrations from this network. Please try again in an hour.", "error")
            return render_template("register.html", form=form, errors={}), 429

        errors = {}
        if form["role"] not in ROLES:
            errors["role"] = "Please choose how you want to use BloodConnect."
        errors["name"]  = validate_name(form["name"])
        errors["email"] = validate_email(form["email"])
        errors["phone"] = validate_phone(form["phone"])
        errors["city"]  = validate_place(form["city"], "City")
        errors["area"]  = validate_place(form["area"], "Area / locality")
        errors["password"] = validate_password(password)
        if not errors["password"] and password != confirm:
            errors["confirm_password"] = "Passwords do not match."

        age, blood_group = 0, ""
        if form["role"] == "donor":
            age, errors["age"] = validate_age(form["age"])
            errors["blood_group"] = validate_blood_group(form["blood_group"])
            blood_group = form["blood_group"]

        # Duplicate account prevention
        if not errors["email"] and email_in_use(form["email"]):
            errors["email"] = "An account with this email already exists. Please log in instead."
        if not errors["phone"] and phone_in_use(form["phone"]):
            errors["phone"] = "This phone number is already registered to another account."

        errors = {k: v for k, v in errors.items() if v}
        if errors:
            flash("Please fix the highlighted fields.", "error")
            return render_template("register.html", form=form, errors=errors), 400

        logger.info(f"Registration attempt: role={form['role']}")

        # Save to database (password is stored as a scrypt hash)
        donor_id = add_donor(form["name"], form["email"], form["phone"], age,
                             blood_group, form["city"], form["area"],
                             generate_password_hash(password), role=form["role"])

        # If donor_id is None — email already registered (race condition)
        if not donor_id:
            errors = {"email": "An account with this email already exists. Please log in instead."}
            return render_template("register.html", form=form, errors=errors), 400

        hit("register", ip)
        # Send welcome email (don't fail if email fails)
        send_registration_confirmation(form["email"], form["name"], blood_group,
                                       donor_id, role=form["role"])

        start_session(get_donor_by_id(donor_id))
        if form["role"] == "donor":
            flash(f"Welcome {form['name']}! You are now registered as a {blood_group} donor.", "success")
        else:
            flash(f"Welcome {form['name']}! Your account is ready — you can now request blood.", "success")
        logger.info(f"Account registered: id={donor_id}")
        return redirect_next("my_profile")

    # GET request — just show the empty form
    role = request.args.get("role")
    return render_template("register.html",
                           form={"role": role if role in ROLES else "donor"},
                           errors={})


# ════════════════════════════════════════════════════════
# MY DASHBOARD (donor + recipient)
# URL: localhost:5000/my-profile
# ════════════════════════════════════════════════════════

@app.route("/my-profile")
@login_required
def my_profile():
    donor    = g.user
    cooldown = get_cooldown_info(donor)
    alerts   = get_donor_alerts(donor["id"])
    accepted = [a for a in alerts if a["stage"] == "Confirmed"]
    pending  = [a for a in alerts if a["stage"] == "Alerted"
                and a["request_status"] in ACTIVE_STATUSES]
    history  = [a for a in alerts if a["donation_outcome"] in ("Donated", "NotNeeded", "Rejected")]
    donations = [a for a in history if a["donation_outcome"] == "Donated"]

    matching = []
    if donor["role"] == "donor" and donor["blood_group"] in BLOOD_GROUPS:
        matching = search_requests(compatible_with=donor["blood_group"],
                                   city=donor["city"], limit=5)
        matching = [r for r in matching if r["owner_id"] != donor["id"]]

    return render_template("my_profile.html",
                           donor=donor,
                           cooldown=cooldown,
                           alerts=alerts,
                           accepted=accepted,
                           pending=pending,
                           history=history,
                           donations=donations,
                           matching=matching,
                           my_requests=get_requests_by_owner(donor["id"]),
                           notifications=get_notifications(donor["id"], 5),
                           phone_ok=validate_phone(donor["phone"]) is None)


@app.route("/my-profile/edit", methods=["GET", "POST"])
@login_required
def edit_profile():
    donor = g.user
    if request.method == "POST":
        form = {
            "role":        form_text("role", 10),
            "name":        form_text("name", 60),
            "phone":       form_text("phone", 15),
            "age":         form_text("age", 3),
            "blood_group": form_text("blood_group", 3),
            "city":        form_text("city", 60),
            "area":        form_text("area", 60),
        }
        errors = {
            "role":  None if form["role"] in ROLES else "Please choose an account type.",
            "name":  validate_name(form["name"]),
            "phone": validate_phone(form["phone"]),
            "city":  validate_place(form["city"], "City"),
            "area":  validate_place(form["area"], "Area / locality"),
        }
        age, blood_group = 0, ""
        if form["role"] == "donor":
            age, errors["age"] = validate_age(form["age"])
            errors["blood_group"] = validate_blood_group(form["blood_group"])
            blood_group = form["blood_group"]
        if not errors["phone"] and phone_in_use(form["phone"], exclude_id=donor["id"]):
            errors["phone"] = "This phone number is already registered to another account."
        errors = {k: v for k, v in errors.items() if v}
        if errors:
            flash("Please fix the highlighted fields.", "error")
            return render_template("edit_profile.html", form=form, errors=errors), 400

        update_profile(donor["id"], form["name"], form["phone"], form["city"],
                       form["area"], form["role"], age, blood_group)
        session["donor_name"] = form["name"]
        flash("Your profile has been updated.", "success")
        return redirect(url_for("my_profile"))

    form = {k: donor[k] for k in ("role", "name", "phone", "age", "blood_group", "city", "area")}
    if not form["age"]:
        form["age"] = ""
    return render_template("edit_profile.html", form=form, errors={})


@app.route("/my-profile/password", methods=["GET", "POST"])
@login_required
def change_password():
    donor = g.user
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new     = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        key = str(donor["id"])
        errors = {}

        if is_rate_limited("pwchange", key, 5, 15):
            flash("Too many incorrect attempts. Please wait 15 minutes.", "error")
            return render_template("change_password.html", errors={}), 429
        if not donor["password_hash"] or not check_password_hash(donor["password_hash"], current):
            hit("pwchange", key)
            errors["current_password"] = "Your current password is incorrect."
        errors["new_password"] = validate_password(new)
        if not errors["new_password"] and new != confirm:
            errors["confirm_password"] = "Passwords do not match."
        if not errors.get("new_password") and current and new == current:
            errors["new_password"] = "The new password must be different from the current one."
        errors = {k: v for k, v in errors.items() if v}
        if errors:
            return render_template("change_password.html", errors=errors), 400

        update_password(donor["id"], generate_password_hash(new))
        reset("pwchange", key)
        # Keep THIS session logged in; other devices are signed out
        session["auth_ver"] = auth_version(get_donor_by_id(donor["id"]))
        flash("Password changed. Other devices have been signed out.", "success")
        return redirect(url_for("my_profile"))
    return render_template("change_password.html", errors={})


@app.route("/my-profile/availability", methods=["POST"])
@login_required
def update_availability():
    available = request.form.get("available") == "1"
    set_availability(g.user["id"], available)
    flash("You are now available for new requests." if available
          else "You are now marked temporarily unavailable — you won't receive new alerts.", "success")
    return redirect(url_for("my_profile"))


@app.route("/notifications")
@login_required
def notifications():
    items = get_notifications(g.user["id"], 50)
    mark_notifications_read(g.user["id"])
    return render_template("notifications.html", items=items)


# ════════════════════════════════════════════════════════
# BLOOD REQUEST
# URL: localhost:5000/request-blood
# PURPOSE: A logged-in user submits an emergency request
# FLOW: Fill form → validate → duplicate check → save →
#       find compatible donors → alert them → redirect to tracking page
# WHY LOGIN IS NOW REQUIRED: the requester must be known so only they
#       can see responses, cancel the request and be notified when a
#       donor accepts. It also discourages fake requests.
# ════════════════════════════════════════════════════════

@app.route("/request-blood", methods=["GET", "POST"])
@login_required
def request_blood():
    user = g.user
    if request.method == "POST":
        data, errors = read_request_form()
        if errors:
            flash("Please fix the highlighted fields.", "error")
            return render_template("request_blood.html", form=data, errors=errors), 400

        if is_rate_limited("new_request", str(user["id"]), 5, 60):
            flash("You have created several requests in the last hour. Please wait before creating another.", "error")
            return render_template("request_blood.html", form=data, errors={}), 429

        # Duplicate request prevention
        dup = find_duplicate_active_request(user["id"], data["blood_group"],
                                            data["hospital_name"], data["city"])
        if dup:
            flash("You already have an active request for this blood group at this hospital. "
                  "You can edit it instead of creating a new one.", "warning")
            return redirect(url_for("request_detail", req_id=dup["id"]))

        logger.info(f"Blood request: {data['blood_group']} | urgency={data['urgency']}")

        req_id = add_blood_request(
            data["patient_name"], data["blood_group"], data["units_needed_int"],
            data["hospital_name"], data["city"], data["contact_person"],
            data["contact_phone"], data["urgency"], owner_id=user["id"],
            public_note=data["public_note"] or None,
            private_note=data["private_note"] or None,
            required_by=data["required_by_iso"]
        )
        hit("new_request", str(user["id"]))

        # Find matching donors — compatible, available, outside cooldown
        matching = get_matching_donors(data["blood_group"], data["city"], exclude_id=user["id"])

        sent = 0
        if matching:
            # Send alerts (public details only) to all matching donors
            sent, failed = send_bulk_alerts(
                donors        = matching,
                req_id        = req_id,
                blood_group   = data["blood_group"],
                units_needed  = data["units_needed_int"],
                hospital_name = data["hospital_name"],
                city          = data["city"],
                urgency       = data["urgency"],
                log_alert_fn  = log_alert
            )
            add_notifications(
                [d["id"] for d in matching],
                f"New {data['urgency'].lower()} request: {data['blood_group']} blood needed at "
                f"{data['hospital_name']}, {data['city']}. Your blood group is compatible.",
                f"/request/{req_id}"
            )
            logger.info(f"Alerts: req={req_id} donors={len(matching)} emailed={sent} failed={failed}")

        send_requester_confirmation(user["email"], data["contact_person"],
                                    data["blood_group"], len(matching), req_id)

        if matching:
            flash(f"🩸 Request created. {len(matching)} compatible donor(s) in {data['city']} were notified. "
                  f"You'll be notified as soon as a donor accepts.", "success")
        else:
            flash(f"Request saved, but no available compatible donors were found in {data['city']} right now. "
                  f"It is listed publicly so new donors can still find it.", "warning")
            logger.warning(f"No matching donors: req={req_id}")
        return redirect(url_for("request_detail", req_id=req_id))

    return render_template("request_blood.html",
                           form={"units_needed": "1", "urgency": "Normal",
                                 "contact_person": user["name"], "contact_phone": user["phone"],
                                 "city": user["city"]},
                           errors={})


# ════════════════════════════════════════════════════════
# REQUEST DETAIL / TRACKING PAGE
# URL: localhost:5000/request/5
# PURPOSE: One page, different views depending on request_access():
#   owner/admin → everything + donor responses
#   accepted    → contact details to coordinate the donation
#   donor       → public details + Accept / Decline buttons
# ════════════════════════════════════════════════════════

@app.route("/request/<int:req_id>")
def request_detail(req_id):
    if not g.get("user") and not g.get("is_admin"):
        flash("Please log in to view and respond to blood requests.", "warning")
        return redirect(url_for("donor_login", next=request.path))

    req = get_public_request(req_id)
    if not req:
        abort(404)
    access = request_access(get_request_by_id(req_id))

    private = accepted_donors = summary = all_alerts = None
    if access in ("admin", "owner", "accepted"):
        private = get_private_request_details(req_id)
    if access in ("admin", "owner"):
        accepted_donors = get_accepted_donors(req_id)
        summary = get_response_summary(req_id)
    if access == "admin":
        _, all_alerts = get_request_with_alerts(req_id)

    user = g.get("user")
    my_alert = get_alert_for(req_id, user["id"]) if user else None
    is_donor = bool(user and user["role"] == "donor")
    compatible = is_donor and can_donate(user["blood_group"], req["blood_group"])
    slots_left = max(req["units_needed"] - req["accepted_count"], 0)

    return render_template("request_detail.html",
                           req=req, access=access, private=private,
                           accepted_donors=accepted_donors, summary=summary,
                           all_alerts=all_alerts, my_alert=my_alert,
                           is_donor=is_donor, compatible=compatible,
                           slots_left=slots_left,
                           cooldown=get_cooldown_info(user) if is_donor else None,
                           events=get_request_events(req_id),
                           celebrate=request.args.get("accepted") == "1"
                                     and access == "accepted")


@app.route("/request/<int:req_id>/accept", methods=["POST"])
@login_required
def accept(req_id):
    user = g.user
    if user["role"] != "donor":
        flash("Only donor accounts can accept requests. You can switch to a donor account in Edit Profile.", "warning")
        return redirect(url_for("request_detail", req_id=req_id))

    result = accept_request(req_id, user["id"], user["blood_group"])
    messages = {
        "not_found":    ("This request does not exist.", "error"),
        "inactive":     ("This request is no longer active.", "warning"),
        "own_request":  ("You cannot accept your own request.", "warning"),
        "incompatible": ("Your blood group is not compatible with this request.", "warning"),
        "already":      ("You have already accepted this request.", "success"),
        "done":         ("You have already reported an outcome for this request.", "warning"),
        "full":         ("Enough donors have already accepted this request. Thank you for offering!", "warning"),
    }
    if result == "not_found":
        abort(404)
    if result != "ok":
        msg, cat = messages[result]
        flash(msg, cat)
        return redirect(url_for("request_detail", req_id=req_id))

    req = get_request_by_id(req_id)
    if req["owner_id"]:
        notify_and_email([req["owner_id"]],
                         f"{user['name']} accepted your {req['blood_group']} request. "
                         f"Open the request to see their phone number.",
                         f"/request/{req_id}", "A donor accepted your request", req_id)
    logger.info(f"Request accepted: req={req_id} donor={user['id']}")
    flash("Thank you for accepting! The contact details are now visible below. "
          "Please call the contact person before going to the hospital.", "success")
    return redirect(url_for("request_detail", req_id=req_id, accepted=1))


@app.route("/request/<int:req_id>/decline", methods=["POST"])
@login_required
def decline(req_id):
    if not get_public_request(req_id):
        abort(404)
    result = decline_request(req_id, g.user["id"])
    if result == "accepted":
        flash("You have accepted this request. Use 'Withdraw' if you can no longer help.", "warning")
        return redirect(url_for("request_detail", req_id=req_id))
    if result == "done":
        flash("You have already reported an outcome for this request.", "warning")
        return redirect(url_for("request_detail", req_id=req_id))
    flash("Thanks for letting us know. No details were shared, and the request stays open for other donors.", "success")
    return redirect(url_for("browse_requests"))


@app.route("/request/<int:req_id>/withdraw", methods=["POST"])
@login_required
def withdraw(req_id):
    if withdraw_acceptance(req_id, g.user["id"]) != "ok":
        flash("You have not accepted this request.", "warning")
        return redirect(url_for("request_detail", req_id=req_id))
    req = get_request_by_id(req_id)
    if req["owner_id"]:
        notify_and_email([req["owner_id"]],
                         f"A donor withdrew from your {req['blood_group']} request. "
                         f"It remains visible to other donors.",
                         f"/request/{req_id}", "A donor withdrew", req_id)
    flash("You withdrew from this request. Its contact details are no longer shared with you.", "success")
    return redirect(url_for("request_detail", req_id=req_id))


def _cancel(req_id):
    req = get_request_by_id(req_id)
    if not req:
        abort(404)
    if not can_manage(req):
        abort(403)
    if req["status"] not in ACTIVE_STATUSES:
        flash("This request is already closed.", "warning")
        return redirect(url_for("request_detail", req_id=req_id))
    donor_ids = get_confirmed_donor_ids(req_id)
    update_request_status(req_id, STATUS_CANCELLED,
                          "Cancelled by admin" if g.is_admin else "Cancelled by requester")
    notify_and_email(donor_ids,
                     f"The {req['blood_group']} request at {req['hospital_name']} was cancelled. "
                     f"You no longer need to go — thank you for offering to help.",
                     f"/request/{req_id}", "Request cancelled", req_id)
    flash("Request cancelled. Donors who accepted have been notified.", "success")
    return redirect(url_for("dashboard") if g.is_admin else url_for("my_profile"))


@app.route("/request/<int:req_id>/cancel", methods=["POST"])
def cancel_request(req_id):
    if not g.get("user") and not g.get("is_admin"):
        abort(403)
    return _cancel(req_id)


@app.route("/request/<int:req_id>/close", methods=["POST"])
def close_request(req_id):
    """Kept from the first version — now means 'cancel' and is protected."""
    if not g.get("user") and not g.get("is_admin"):
        abort(403)
    return _cancel(req_id)


@app.route("/request/<int:req_id>/status", methods=["POST"])
def change_request_status(req_id):
    """Requester/admin marks a request In Progress or Completed."""
    req = get_request_by_id(req_id)
    if not req:
        abort(404)
    if not can_manage(req):
        abort(403)
    new = request.form.get("status")
    if new == STATUS_IN_PROGRESS and req["status"] == STATUS_ASSIGNED:
        update_request_status(req_id, STATUS_IN_PROGRESS, "Requester marked donation in progress")
        flash("Request marked as In Progress.", "success")
    elif new == STATUS_COMPLETED and req["status"] in ACTIVE_STATUSES:
        donor_ids = get_confirmed_donor_ids(req_id)
        update_request_status(req_id, STATUS_COMPLETED, "Requester confirmed blood received")
        notify_and_email(donor_ids,
                         f"The {req['blood_group']} request at {req['hospital_name']} is now completed. "
                         f"If you donated, please report your outcome from your dashboard.",
                         f"/request/{req_id}", "Request completed", req_id)
        flash("Request marked as Completed. Thank you for keeping it up to date!", "success")
    else:
        flash("That status change is not allowed right now.", "warning")
    return redirect(url_for("request_detail", req_id=req_id))


@app.route("/request/<int:req_id>/edit", methods=["GET", "POST"])
def edit_request(req_id):
    req = get_request_by_id(req_id)
    if not req:
        abort(404)
    if not can_manage(req):
        abort(403)
    if req["status"] not in ACTIVE_STATUSES:
        flash("Only active requests can be edited.", "warning")
        return redirect(url_for("request_detail", req_id=req_id))

    if request.method == "POST":
        data, errors = read_request_form()
        # Blood group, city and patient cannot change after donors were alerted
        for locked in ("patient_name", "blood_group", "city"):
            errors.pop(locked, None)
            data[locked] = req[locked]
        if errors:
            flash("Please fix the highlighted fields.", "error")
            return render_template("request_edit.html", req=req, form=data, errors=errors), 400
        update_request_details(req_id, data["units_needed_int"], data["urgency"],
                               data["hospital_name"], data["contact_person"],
                               data["contact_phone"], data["public_note"] or None,
                               data["private_note"] or None, data["required_by_iso"])
        notify_and_email(get_confirmed_donor_ids(req_id),
                         "The details of a request you accepted were updated. Please check them before going.",
                         f"/request/{req_id}", "Request updated", req_id)
        flash("Request updated.", "success")
        return redirect(url_for("request_detail", req_id=req_id))

    form = dict(req)
    form["required_by"] = req["required_by"] or ""
    return render_template("request_edit.html", req=req, form=form, errors={})


# ════════════════════════════════════════════════════════
# OLD EMAIL LINKS — /confirm/<alert_id>/yes|no
# Emails sent by the first version linked here. Clicking a link no
# longer changes anything (a link alone is not proof of who clicked it).
# The donor is sent to the request page to accept or decline there.
# ════════════════════════════════════════════════════════

@app.route("/confirm/<int:alert_id>/<response>")
@login_required
def confirm(alert_id, response):
    alert = get_alert_by_id(alert_id)
    if not alert or alert["donor_id"] != g.user["id"]:
        abort(404)
    flash("Please use the Accept or Decline buttons below to respond.", "warning")
    return redirect(url_for("request_detail", req_id=alert["request_id"]))


# ════════════════════════════════════════════════════════
# STAGE 3 — DONATION OUTCOME
# URL: localhost:5000/outcome/15
# PURPOSE: Donor reports what ACTUALLY happened at hospital
# SECURITY: only the donor who owns this alert, and only after accepting
#
# Three possible outcomes:
#   Donated    → Blood successfully donated → START alert pause (cooldown)
#   NotNeeded  → Blood not needed anymore   → NO cooldown
#   Rejected   → Medically rejected         → NO cooldown
# ════════════════════════════════════════════════════════

@app.route("/outcome/<int:alert_id>", methods=["GET", "POST"])
@login_required
def outcome(alert_id):
    alert = get_alert_by_id(alert_id)
    if not alert or alert["donor_id"] != g.user["id"]:
        abort(404)   # 404 (not 403) so IDs of other people's alerts are not confirmed
    if alert["stage"] != "Confirmed":
        flash("You can report an outcome only for a request you have accepted (and only once).", "warning")
        return redirect(url_for("request_detail", req_id=alert["request_id"]))

    if request.method == "POST":
        outcome_value = request.form.get("outcome", "")

        if outcome_value not in ["Donated", "NotNeeded", "Rejected"]:
            flash("Please select an outcome.", "error")
            return render_template("outcome.html", alert=alert), 400

        # THIS IS WHERE COOLDOWN IS DECIDED
        result = record_donation_outcome(alert_id, outcome_value)

        # Send thank you email to donor
        send_outcome_thankyou(g.user["email"], g.user["name"], outcome_value)

        req = get_request_by_id(alert["request_id"])
        if req["owner_id"]:
            message = f"{g.user['name']} {OUTCOME_LABELS[outcome_value]} for your {req['blood_group']} request."
            if result and result["completed"]:
                message += " Your request is now marked Completed."
            notify_and_email([req["owner_id"]], message, f"/request/{req['id']}",
                             "Donation update", req["id"])

        logger.info(f"Outcome recorded: alert={alert_id} outcome={outcome_value}")

        return render_template("outcome_done.html",
                               alert=alert,
                               outcome=outcome_value)

    return render_template("outcome.html", alert=alert)


# ════════════════════════════════════════════════════════
# ADMIN DASHBOARD
# URL: localhost:5000/dashboard
# PURPOSE: Shows all requests and donors — ADMIN ONLY
# ════════════════════════════════════════════════════════

@app.route("/dashboard")
@admin_required
def dashboard():
    stats = get_stats()

    # Request filters
    f_status = request.args.get("status", "")
    f_bg     = request.args.get("blood_group", "")
    f_city   = request.args.get("city", "").strip().lower()[:60]
    requests_ = [r for r in get_all_requests()
                 if (not f_status or r["status"] == f_status)
                 and (not f_bg or r["blood_group"] == f_bg)
                 and (not f_city or f_city in r["city"].lower())]

    # User filters
    f_role = request.args.get("role", "")
    q      = request.args.get("q", "").strip().lower()[:60]
    users  = [d for d in get_all_donors(f_role if f_role in ROLES else None)
              if not q or q in d["name"].lower() or q in d["email"].lower()
              or q in d["city"].lower()]

    logger.info("Dashboard accessed")
    return render_template("dashboard.html",
                           donors=users,
                           requests=requests_,
                           stats=stats,
                           filters={"status": f_status, "blood_group": f_bg,
                                    "city": f_city, "role": f_role, "q": q},
                           tab=request.args.get("tab", "req"))


@app.route("/admin/user/<int:donor_id>/active", methods=["POST"])
@admin_required
def admin_set_active(donor_id):
    if not get_donor_by_id(donor_id):
        abort(404)
    active = request.form.get("active") == "1"
    set_account_active(donor_id, active)
    flash("Account reactivated." if active else "Account deactivated — it can no longer log in or receive alerts.", "success")
    return redirect(url_for("dashboard", tab="don"))


# ════════════════════════════════════════════════════════
# DONOR PROFILE (admin, or the donor themself)
# URL: localhost:5000/donor/5
# PURPOSE: Shows individual donor details and cooldown status
# ════════════════════════════════════════════════════════

@app.route("/donor/<int:donor_id>")
def donor_profile(donor_id):
    if g.get("user") and g.user["id"] == donor_id:
        return redirect(url_for("my_profile"))
    if not g.get("is_admin"):
        abort(404 if g.get("user") else 403)
    donor = get_donor_by_id(donor_id)
    if not donor:
        abort(404)
    cooldown = get_cooldown_info(donor)
    return render_template("donor_profile.html",
                           donor=donor,
                           cooldown=cooldown,
                           alerts=get_donor_alerts(donor_id))


@app.route("/donor/<int:donor_id>/toggle", methods=["POST"])
def toggle(donor_id):
    is_self = g.get("user") and g.user["id"] == donor_id
    if not (is_self or g.get("is_admin")):
        abort(403)
    if not get_donor_by_id(donor_id):
        abort(404)
    toggle_availability(donor_id)
    flash("Availability updated!", "success")
    if is_self:
        return redirect(url_for("my_profile"))
    return redirect(request.form.get("back") if is_safe_next(request.form.get("back"))
                    else url_for("donor_profile", donor_id=donor_id))


# ════════════════════════════════════════════════════════
# ERROR PAGES
# PURPOSE: Friendly messages that never reveal internal details
# ════════════════════════════════════════════════════════

ERROR_TEXT = {
    400: ("Something went wrong with that form",
          "Please go back, refresh the page and try again."),
    403: ("Access denied", "You do not have permission to view this page."),
    404: ("Page not found", "The page you are looking for does not exist or is not available to you."),
    405: ("Not allowed", "That action is not allowed here."),
    413: ("Too much data", "The form you sent was too large."),
    429: ("Too many attempts", "Please wait a few minutes and try again."),
    500: ("Something went wrong", "An unexpected error happened. Please try again in a moment."),
}


@app.errorhandler(HTTPException)
def handle_http_error(e):
    code = e.code or 500
    title, text = ERROR_TEXT.get(code, (e.name, "Please try again."))
    if code == 400 and e.description == "csrf":
        title = "Your form has expired"
        text = ("For your security, forms expire when you log in, log out or stay "
                "inactive for a long time. Please go back, refresh the page and try again.")
    return render_template("error.html", code=code, title=title, text=text), code


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    logger.exception("Unhandled error")
    title, text = ERROR_TEXT[500]
    return render_template("error.html", code=500, title=title, text=text), 500


# ════════════════════════════════════════════════════════
# RUN THE APP
# ════════════════════════════════════════════════════════

if __name__ == "__main__":
    logger.info("Starting BloodConnect development server")
    # debug mode is OFF unless FLASK_DEBUG=1 is set in .env, because the
    # debug page can run code and must never be reachable by others.
    debug = os.environ.get("FLASK_DEBUG") == "1"
    # 127.0.0.1 = only this computer can open the site. Set HOST=0.0.0.0
    # in .env if you want to test from your phone on the same Wi-Fi.
    host = os.environ.get("HOST", "127.0.0.1")

    scheduler = BackgroundScheduler()
    scheduler.add_job(_expire_and_notify, 'interval', minutes=1)
    scheduler.start()
    app.run(debug=debug, port=int(os.environ.get("PORT", "5000")), host=host)
