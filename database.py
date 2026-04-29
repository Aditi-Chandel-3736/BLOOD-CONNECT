"""
database.py — BloodConnect Database Layer
==========================================
PURPOSE: This file handles ALL database operations.
Think of it as the memory of your application.
Every piece of data — donors, requests, alerts — is stored and retrieved here.

WHY NECESSARY: Without a database your data disappears when app restarts.
SQLite stores everything permanently in a single file (bloodconnect.db).
"""

import sqlite3
from datetime import datetime, timedelta
import logging

# Logger for this file
logger = logging.getLogger(__name__)

# Database file path — will be created automatically
DB_PATH = "bloodconnect.db"

# WHO guideline — whole blood donation cooldown
COOLDOWN_DAYS = 56


# ═══════════════════════════════════════════════════
# CONNECTION
# PURPOSE: Opens a connection to the database file
# WHY: Every database operation needs a connection first
# ═══════════════════════════════════════════════════

def get_db():
    """
    Opens database connection.
    row_factory = sqlite3.Row means results come back
    as dictionary-like objects so we can do row["name"]
    instead of row[0]
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ═══════════════════════════════════════════════════
# INITIALIZE DATABASE
# PURPOSE: Creates all tables if they don't exist yet
# WHY: First time app runs, database file is empty.
#      This creates the structure.
# ═══════════════════════════════════════════════════

def init_db():
    """Creates all database tables on first run."""
    conn = get_db()

    # ── DONORS TABLE ──────────────────────────────
    # Stores every person who registers as a blood donor
    conn.execute("""
        CREATE TABLE IF NOT EXISTS donors (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            email         TEXT    NOT NULL UNIQUE,
            phone         TEXT    NOT NULL,
            age           INTEGER NOT NULL,
            blood_group   TEXT    NOT NULL,
            city          TEXT    NOT NULL,
            area          TEXT    NOT NULL,
            is_available  INTEGER DEFAULT 1,
            last_donated  DATE,
            registered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            password_hash TEXT
        )
    """)
    # EXPLANATION OF COLUMNS:
    # id           — auto-incrementing unique number for each donor
    # name         — full name
    # email        — for sending alert emails
    # phone        — for patient family to call donor directly
    # age          — must be 18-65 (WHO guideline)
    # blood_group  — A+, A-, B+, B-, O+, O-, AB+, AB-
    # city         — used for location matching
    # area         — specific locality within city
    # is_available — 1=yes can donate, 0=temporarily unavailable
    # last_donated — date of ACTUAL last donation (NOT when they clicked yes)
    #                This is only set when Stage 3 confirms actual donation
    # registered_at — automatic timestamp when they registered

    # ── BLOOD REQUESTS TABLE ──────────────────────
    # Stores every emergency blood request
    conn.execute("""
        CREATE TABLE IF NOT EXISTS blood_requests (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_name    TEXT    NOT NULL,
            blood_group     TEXT    NOT NULL,
            units_needed    INTEGER NOT NULL,
            hospital_name   TEXT    NOT NULL,
            city            TEXT    NOT NULL,
            contact_person  TEXT    NOT NULL,
            contact_phone   TEXT    NOT NULL,
            urgency         TEXT    DEFAULT 'Normal',
            status          TEXT    DEFAULT 'Open',
            donors_alerted  INTEGER DEFAULT 0,
            donors_confirmed INTEGER DEFAULT 0,
            donors_donated  INTEGER DEFAULT 0,
            requested_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # EXPLANATION OF KEY COLUMNS:
    # contact_phone   — CRITICAL: this goes in every donor alert email
    #                   so donors can call patient family directly
    # urgency         — Normal / Urgent / Critical
    # status          — Open / Fulfilled / Closed
    # donors_alerted  — how many alert emails were sent
    # donors_confirmed — how many said YES they are coming
    # donors_donated  — how many ACTUALLY donated (Stage 3)

    # ── ALERTS TABLE ──────────────────────────────
    # Tracks every alert sent and the 3-stage donation process
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id        INTEGER NOT NULL,
            donor_id          INTEGER NOT NULL,
            stage             TEXT    DEFAULT 'Alerted',
            donation_outcome  TEXT    DEFAULT 'Pending',
            alerted_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
            confirmed_at      DATETIME,
            outcome_at        DATETIME,
            FOREIGN KEY (request_id) REFERENCES blood_requests(id),
            FOREIGN KEY (donor_id)   REFERENCES donors(id)
        )
    """)
    # EXPLANATION OF KEY COLUMNS:
    # stage — tracks where in the process this donor is:
    #   'Alerted'   = email sent, waiting for response
    #   'Confirmed' = donor clicked YES, going to hospital
    #   'Outcome'   = donor reported what happened at hospital
    #
    # donation_outcome — what actually happened:
    #   'Pending'       = not yet responded
    #   'Declined'      = said NO cannot come
    #   'Donated'       = ✅ successfully donated → START COOLDOWN
    #   'NotNeeded'     = reached hospital but blood not needed → NO cooldown
    #   'Rejected'      = hospital rejected donor medically → NO cooldown
    #
    # alerted_at    = when email was sent
    # confirmed_at  = when donor clicked YES
    # outcome_at    = when donor reported final outcome

    conn.commit()
    conn.close()

    # Add demo data for testing
    _add_demo_data()
    logger.info("Database initialized successfully")


def _add_demo_data():
    """Adds sample donors so dashboard is not empty on first run."""
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM donors").fetchone()[0]
    if count == 0:
        demo = [
            ("Rahul Sharma",  "rahul@test.com",  "9876543210", 25, "O+",  "Mumbai", "Andheri"),
            ("Priya Singh",   "priya@test.com",  "9876543211", 28, "O+",  "Mumbai", "Bandra"),
            ("Amit Kumar",    "amit@test.com",   "9876543212", 30, "A+",  "Mumbai", "Dadar"),
            ("Sneha Patel",   "sneha@test.com",  "9876543213", 22, "B+",  "Pune",   "Kothrud"),
            ("Vikram Rao",    "vikram@test.com", "9876543214", 35, "AB+", "Mumbai", "Worli"),
            ("Neha Joshi",    "neha@test.com",   "9876543215", 27, "O-",  "Mumbai", "Malad"),
            ("Suresh Mehta",  "suresh@test.com", "9876543216", 32, "A-",  "Mumbai", "Borivali"),
            ("Kavya Nair",    "kavya@test.com",  "9876543217", 24, "B-",  "Mumbai", "Thane"),
        ]
        for d in demo:
            conn.execute("""
                INSERT INTO donors (name,email,phone,age,blood_group,city,area)
                VALUES (?,?,?,?,?,?,?)
            """, d)
        conn.commit()
        logger.info("Demo data added — 8 sample donors created")
    conn.close()


# ═══════════════════════════════════════════════════
# DONOR FUNCTIONS
# ═══════════════════════════════════════════════════

def add_donor(name, email, phone, age, blood_group, city, area, password_hash):
    """
    PURPOSE: Saves a new donor to the database
    WHY: When someone fills the registration form,
         their details must be permanently saved
    RETURNS: donor id if successful, None if email already exists
    """
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO donors (name, email, phone, age, blood_group, city, area, password_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, email, phone, age, blood_group, city, area, password_hash))
        donor_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        logger.info(f"Donor registered: {name} | {blood_group} | {city}")
        return donor_id
    except sqlite3.IntegrityError:
        # This error happens when email already exists (UNIQUE constraint)
        logger.warning(f"Registration failed — email already exists: {email}")
        return None
    finally:
        conn.close()


def get_all_donors():
    """Returns list of all donors ordered by newest first."""
    conn = get_db()
    donors = conn.execute(
        "SELECT * FROM donors ORDER BY registered_at DESC"
    ).fetchall()
    conn.close()
    return donors


def get_donor_by_id(donor_id):
    """Returns a single donor by their ID."""
    conn = get_db()
    donor = conn.execute(
        "SELECT * FROM donors WHERE id=?", (donor_id,)
    ).fetchone()
    conn.close()
    return donor


def toggle_availability(donor_id):
    """
    PURPOSE: Switches donor between available and unavailable
    WHY: Donor may be sick, travelling, or busy temporarily.
         They can mark themselves unavailable so they don't get
         alerted when they cannot help.
    """
    conn = get_db()
    conn.execute("""
        UPDATE donors
        SET is_available = CASE WHEN is_available=1 THEN 0 ELSE 1 END
        WHERE id=?
    """, (donor_id,))
    conn.commit()
    conn.close()
    logger.info(f"Donor {donor_id} availability toggled")


def mark_actual_donation(donor_id):
    """
    PURPOSE: Records that donor ACTUALLY donated blood
    WHY: This is Stage 3 of our 3-stage process.
         ONLY when donor confirms actual donation do we:
         1. Set last_donated to today
         2. This triggers the 56 day cooldown
    IMPORTANT: This is called ONLY when outcome = 'Donated'
    NOT when donor just clicks YES to come
    """
    today = datetime.today().strftime("%Y-%m-%d")
    conn = get_db()
    conn.execute(
        "UPDATE donors SET last_donated=? WHERE id=?",
        (today, donor_id)
    )
    conn.commit()
    conn.close()
    logger.info(f"Actual donation recorded for donor {donor_id} on {today}")


# ═══════════════════════════════════════════════════
# COOLDOWN CHECK
# PURPOSE: Determines if a donor is medically eligible
# WHY: WHO guideline — whole blood donors must wait 56 days
#      between donations to allow body to replenish blood
# ═══════════════════════════════════════════════════

def get_matching_donors(blood_group, city):
    """
    PURPOSE: Finds all eligible donors for a blood request
    WHY: Not every donor can donate — must match blood group,
         be in same city, be available, and pass cooldown check

    COOLDOWN LOGIC:
    cutoff_date = today minus 56 days
    If last_donated is NULL  → never donated → ELIGIBLE
    If last_donated <= cutoff_date → donated 56+ days ago → ELIGIBLE
    If last_donated > cutoff_date  → donated too recently → NOT ELIGIBLE
    """
    cutoff = (datetime.today() - timedelta(days=COOLDOWN_DAYS)).strftime("%Y-%m-%d")

    conn = get_db()
    donors = conn.execute("""
        SELECT * FROM donors
        WHERE blood_group  = ?
        AND   LOWER(TRIM(city))  = LOWER(TRIM(?))
        AND   is_available = 1
        AND   age          BETWEEN 18 AND 65
        AND   (
                last_donated IS NULL
                OR last_donated <= ?
              )
        ORDER BY id
    """, (blood_group, city, cutoff)).fetchall()
    conn.close()

    logger.info(
        f"Matching donors found: {len(donors)} "
        f"for {blood_group} in {city} "
        f"(cooldown cutoff: {cutoff})"
    )
    return donors


def get_cooldown_info(donor):
    """
    PURPOSE: Returns human-readable cooldown status for a donor
    WHY: Shown on donor profile so they know when they
         can donate again
    """
    last = donor["last_donated"]
    if not last:
        return {
            "eligible": True,
            "status": "Never donated — eligible now",
            "days_remaining": 0,
            "eligible_from": None,
            "last_donated_display": "Never"
        }

    last_date = datetime.strptime(last, "%Y-%m-%d")
    eligible_from = last_date + timedelta(days=COOLDOWN_DAYS)
    today = datetime.today()

    if today >= eligible_from:
        return {
            "eligible": True,
            "status": "Eligible to donate",
            "days_remaining": 0,
            "eligible_from": eligible_from.strftime("%d %b %Y"),
            "last_donated_display": last_date.strftime("%d %b %Y")
        }
    else:
        days_left = (eligible_from - today).days
        return {
            "eligible": False,
            "status": f"Cooldown active — {days_left} more days",
            "days_remaining": days_left,
            "eligible_from": eligible_from.strftime("%d %b %Y"),
            "last_donated_display": last_date.strftime("%d %b %Y")
        }


# ═══════════════════════════════════════════════════
# BLOOD REQUEST FUNCTIONS
# ═══════════════════════════════════════════════════

def add_blood_request(patient_name, blood_group, units_needed,
                      hospital_name, city, contact_person,
                      contact_phone, urgency):
    """
    PURPOSE: Saves a new blood request to database
    WHY: Every emergency request must be permanently recorded
         for tracking and reporting purposes
    """
    conn = get_db()
    conn.execute("""
        INSERT INTO blood_requests
        (patient_name, blood_group, units_needed, hospital_name,
         city, contact_person, contact_phone, urgency)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (patient_name, blood_group, units_needed, hospital_name,
          city, contact_person, contact_phone, urgency))
    req_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    logger.info(f"Blood request saved: ID={req_id} | {blood_group} | {city}")
    return req_id


def get_all_requests():
    """Returns all blood requests, newest first."""
    conn = get_db()
    reqs = conn.execute(
        "SELECT * FROM blood_requests ORDER BY requested_at DESC"
    ).fetchall()
    conn.close()
    return reqs


def get_request_with_alerts(req_id):
    """
    PURPOSE: Returns a blood request AND all its donor alerts
    WHY: Request detail page needs both the request info
         AND the list of donors who were alerted with their responses
    """
    conn = get_db()
    req = conn.execute(
        "SELECT * FROM blood_requests WHERE id=?", (req_id,)
    ).fetchone()

    alerts = conn.execute("""
        SELECT
            a.*,
            d.name        AS donor_name,
            d.blood_group AS donor_bg,
            d.area        AS donor_area,
            d.phone       AS donor_phone
        FROM alerts a
        JOIN donors d ON a.donor_id = d.id
        WHERE a.request_id = ?
        ORDER BY a.alerted_at ASC
    """, (req_id,)).fetchall()
    conn.close()
    return req, alerts


def update_request_status(req_id, status):
    """Updates the status of a blood request."""
    conn = get_db()
    conn.execute(
        "UPDATE blood_requests SET status=? WHERE id=?",
        (status, req_id)
    )
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════
# ALERT FUNCTIONS — 3 STAGE PROCESS
# ═══════════════════════════════════════════════════

def log_alert(req_id, donor_id):
    """
    PURPOSE: Records that an alert email was sent to a donor
    WHY: We need to track which donors were alerted for each
         request so we can show responses on the tracking page
    STAGE: This is Stage 1 — Alert Sent
    """
    conn = get_db()
    conn.execute("""
        INSERT INTO alerts (request_id, donor_id, stage)
        VALUES (?, ?, 'Alerted')
    """, (req_id, donor_id))

    conn.execute("""
        UPDATE blood_requests
        SET donors_alerted = donors_alerted + 1
        WHERE id=?
    """, (req_id,))

    alert_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    logger.info(f"Alert logged: request={req_id} donor={donor_id} alert={alert_id}")
    return alert_id


def donor_confirms_coming(alert_id):
    """
    PURPOSE: Records that donor said YES they are coming
    WHY: Stage 2 — Donor confirmed they will visit hospital
    IMPORTANT: Cooldown does NOT start here.
               We don't know yet if they actually donated.
    """
    conn = get_db()
    conn.execute("""
        UPDATE alerts
        SET stage        = 'Confirmed',
            confirmed_at = CURRENT_TIMESTAMP
        WHERE id=?
    """, (alert_id,))

    # Get request id to update confirmed count
    row = conn.execute(
        "SELECT request_id FROM alerts WHERE id=?", (alert_id,)
    ).fetchone()
    if row:
        conn.execute("""
            UPDATE blood_requests
            SET donors_confirmed = donors_confirmed + 1
            WHERE id=?
        """, (row["request_id"],))

    conn.commit()
    conn.close()
    logger.info(f"Donor confirmed coming: alert={alert_id}")


def donor_declines(alert_id):
    """
    PURPOSE: Records that donor said NO they cannot come
    WHY: Tracking who declined helps understand response rates
    NOTE: No cooldown. Declining has no medical implications.
    """
    conn = get_db()
    conn.execute("""
        UPDATE alerts
        SET stage           = 'Outcome',
            donation_outcome = 'Declined',
            outcome_at       = CURRENT_TIMESTAMP
        WHERE id=?
    """, (alert_id,))
    conn.commit()
    conn.close()
    logger.info(f"Donor declined: alert={alert_id}")


def record_donation_outcome(alert_id, outcome):
    """
    PURPOSE: Records what ACTUALLY happened when donor visited hospital
    WHY: This is Stage 3 — the most important step.
         Only here do we know if blood was actually donated.

    OUTCOME OPTIONS:
    'Donated'   → Donor successfully donated blood
                  → START 56 day cooldown NOW
                  → Update request status to Fulfilled

    'NotNeeded' → Donor reached hospital but blood was already
                  found from another source / patient no longer needed it
                  → NO cooldown — donor is still healthy and eligible
                  → Donor gets full credit for showing up

    'Rejected'  → Hospital/doctor found donor medically unfit
                  (low hemoglobin, on medication, fever, etc.)
                  → NO cooldown — they didn't actually donate
                  → Donor should consult doctor

    WHY THIS MATTERS:
    Without this stage, we would wrongly start cooldown when:
    - Blood was no longer needed (patient got blood elsewhere)
    - Doctor rejected the donor
    Both situations are common in real emergencies.
    """
    conn = get_db()

    # Update alert with outcome
    conn.execute("""
        UPDATE alerts
        SET stage            = 'Outcome',
            donation_outcome = ?,
            outcome_at       = CURRENT_TIMESTAMP
        WHERE id=?
    """, (outcome, alert_id))

    if outcome == "Donated":
        # Get donor ID from this alert
        row = conn.execute(
            "SELECT donor_id, request_id FROM alerts WHERE id=?",
            (alert_id,)
        ).fetchone()

        if row:
            # START COOLDOWN — set last_donated to today
            today = datetime.today().strftime("%Y-%m-%d")
            conn.execute(
                "UPDATE donors SET last_donated=? WHERE id=?",
                (today, row["donor_id"])
            )
            # Update donated count on request
            conn.execute("""
                UPDATE blood_requests
                SET donors_donated = donors_donated + 1,
                    status = 'Fulfilled'
                WHERE id=?
            """, (row["request_id"],))
            logger.info(
                f"Donation confirmed: donor={row['donor_id']} "
                f"request={row['request_id']} — cooldown started"
            )

    elif outcome == "NotNeeded":
        logger.info(f"Blood not needed at hospital: alert={alert_id} — NO cooldown")

    elif outcome == "Rejected":
        logger.info(f"Donor medically rejected: alert={alert_id} — NO cooldown")

    conn.commit()
    conn.close()


def get_alert_by_id(alert_id):
    """
    PURPOSE: Gets full alert details including donor and request info
    WHY: Confirmation page needs patient details to show donor,
         and outcome page needs donor name to show patient
    """
    conn = get_db()
    alert = conn.execute("""
        SELECT
            a.*,
            d.name        AS donor_name,
            d.phone       AS donor_phone,
            r.patient_name,
            r.blood_group,
            r.hospital_name,
            r.contact_phone,
            r.contact_person,
            r.urgency,
            r.units_needed,
            r.city
        FROM alerts a
        JOIN donors  d ON a.donor_id   = d.id
        JOIN blood_requests r ON a.request_id = r.id
        WHERE a.id=?
    """, (alert_id,)).fetchone()
    conn.close()
    return alert


# ═══════════════════════════════════════════════════
# STATS FUNCTION
# PURPOSE: Returns counts for dashboard display
# WHY: Home page and dashboard show live statistics
# ═══════════════════════════════════════════════════

def get_stats():
    conn = get_db()
    active   = conn.execute("SELECT COUNT(*) FROM donors WHERE is_available=1").fetchone()[0]
    open_r   = conn.execute("SELECT COUNT(*) FROM blood_requests WHERE status='Open'").fetchone()[0]
    done     = conn.execute("SELECT COUNT(*) FROM blood_requests WHERE status='Fulfilled'").fetchone()[0]
    cities   = conn.execute("SELECT COUNT(DISTINCT city) FROM donors").fetchone()[0]
    donated  = conn.execute("SELECT COUNT(*) FROM alerts WHERE donation_outcome='Donated'").fetchone()[0]
    conn.close()
    return {
        "active_donors": active,
        "open_requests":  open_r,
        "fulfilled":      done,
        "cities":         cities,
        "total_donations": donated
    }

def expire_old_requests():
    """
    PURPOSE: Automatically closes requests that are too old.
    WHY: Blood emergencies are time sensitive. A request older
    than 24-72 hours is no longer relevant. Keeping old requests
    open wastes donor time and damages trust.
    """
    conn = get_db()
    conn.execute("""
        UPDATE blood_requests
        SET status = 'Expired'
        WHERE status = 'Open'
        AND (
            (urgency = 'Critical' AND
             requested_at <= datetime('now', '-24 hours'))
            OR
            (urgency = 'Urgent' AND
             requested_at <= datetime('now', '-48 hours'))
            OR
            (urgency = 'Normal' AND
             requested_at <= datetime('now', '-72 hours'))
        )
    """)
    conn.commit()
    conn.close()
    logger.info("Old requests expired automatically")

def get_donor_by_email(email):
    conn = get_db()
    conn.row_factory = sqlite3.Row

    donor = conn.execute("""
        SELECT * FROM donors
        WHERE email = ?
    """, (email,)).fetchone()

    conn.close()
    return donor

def get_donor_alerts(donor_id):
    conn = get_db()
    conn.row_factory = sqlite3.Row

    alerts = conn.execute("""
        SELECT a.*, br.blood_group, br.city, br.urgency
        FROM alerts a
        JOIN blood_requests br ON a.request_id = br.id
        WHERE a.donor_id = ?
        ORDER BY a.sent_at DESC
    """, (donor_id,)).fetchall()

    conn.close()
    return alerts