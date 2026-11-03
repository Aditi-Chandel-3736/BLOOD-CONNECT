"""
database.py — BloodConnect Database Layer
==========================================
PURPOSE: This file handles ALL database operations.
Think of it as the memory of your application.
Every piece of data — donors, requests, alerts — is stored and retrieved here.

WHY NECESSARY: Without a database your data disappears when app restarts.
SQLite stores everything permanently in a single file (bloodconnect.db).

SECURITY: Every query uses "?" placeholders. SQLite fills them in safely,
so text typed by users can never change the SQL itself (no SQL injection).

PRIVACY: Functions that feed PUBLIC pages (search_requests, get_public_request)
never SELECT private columns such as patient_name or contact_phone.
Private columns are read only by get_private_request_details(), which
app.py calls only after checking the viewer is allowed to see them.
"""

import os
import shutil
import sqlite3
from datetime import datetime, timedelta
import logging

from validators import compatible_donor_groups

# Logger for this file
logger = logging.getLogger(__name__)

# Database file path — will be created automatically
# (DATABASE_PATH can be set in .env, e.g. for automated tests)
DB_PATH = os.environ.get("DATABASE_PATH", "bloodconnect.db")

# How many days BloodConnect pauses alerts after a recorded donation.
# This is a scheduling setting, NOT a medical rule — safe donation
# intervals differ by country and by donor, so donors are always asked
# to confirm their eligibility with a blood bank or doctor.
COOLDOWN_DAYS = int(os.environ.get("DONATION_ALERT_PAUSE_DAYS", "56"))

# ── REQUEST STATUSES ──────────────────────────────
STATUS_PENDING     = "Pending"         # waiting for a donor to accept
STATUS_ASSIGNED    = "Donor Assigned"  # at least one donor accepted
STATUS_IN_PROGRESS = "In Progress"     # requester says donation is under way
STATUS_COMPLETED   = "Completed"       # blood received
STATUS_CANCELLED   = "Cancelled"       # requester/admin cancelled
STATUS_EXPIRED     = "Expired"         # timed out automatically

ACTIVE_STATUSES = (STATUS_PENDING, STATUS_ASSIGNED, STATUS_IN_PROGRESS)
ALL_STATUSES    = ACTIVE_STATUSES + (STATUS_COMPLETED, STATUS_CANCELLED, STATUS_EXPIRED)

# Old status names from the first version → new names
LEGACY_STATUS_MAP = {"Open": STATUS_PENDING,
                     "Fulfilled": STATUS_COMPLETED,
                     "Closed": STATUS_CANCELLED}

# Columns that are safe to show on public pages
PUBLIC_REQUEST_COLUMNS = """
    r.id, r.blood_group, r.units_needed, r.hospital_name, r.city,
    r.urgency, r.status, r.required_by, r.public_note, r.requested_at,
    r.owner_id
"""


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
    timeout=10 waits for a busy database instead of failing at once.
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# ═══════════════════════════════════════════════════
# INITIALIZE DATABASE
# PURPOSE: Creates all tables if they don't exist yet
# WHY: First time app runs, database file is empty.
#      This creates the structure.
# ═══════════════════════════════════════════════════

def init_db():
    """Creates all database tables on first run, then upgrades old databases."""
    conn = get_db()

    # ── DONORS TABLE ──────────────────────────────
    # Stores every registered account (donors AND recipients — see "role")
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
    # email        — for sending alert emails (stored lowercase)
    # phone        — 10 digit mobile, shared only after a request is accepted
    # age          — donors must be 18-65 (0 for recipient accounts)
    # blood_group  — A+, A-, B+, B-, O+, O-, AB+, AB- ('' for recipients)
    # city         — used for location matching
    # area         — specific locality within city
    # is_available — 1=yes can donate, 0=temporarily unavailable
    # last_donated — date of ACTUAL last donation (NOT when they clicked yes)
    #                This is only set when Stage 3 confirms actual donation
    # registered_at — automatic timestamp when they registered
    # password_hash — scrypt hash of the password (never the password itself)
    # role / is_active — added by _migrate() below

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
            status          TEXT    DEFAULT 'Pending',
            donors_alerted  INTEGER DEFAULT 0,
            donors_confirmed INTEGER DEFAULT 0,
            donors_donated  INTEGER DEFAULT 0,
            requested_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # EXPLANATION OF KEY COLUMNS:
    # patient_name, contact_person, contact_phone, private_note
    #                 — PRIVATE: shown only to the requester, admins and
    #                   donors who have ACCEPTED this request
    # urgency         — Normal / Urgent / Critical
    # status          — Pending / Donor Assigned / In Progress /
    #                   Completed / Cancelled / Expired
    # donors_alerted  — how many alert emails were sent
    # donors_confirmed — how many donors currently accept the request
    # donors_donated  — how many ACTUALLY donated (Stage 3)
    # owner_id, public_note, private_note, required_by, updated_at
    #                 — added by _migrate() below

    # ── ALERTS TABLE ──────────────────────────────
    # Tracks every donor's response to a request (the 3-stage process)
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
    #   'Alerted'   = notified, waiting for response
    #   'Confirmed' = donor ACCEPTED the request → may see contact details
    #   'Withdrawn' = donor accepted, then withdrew → access revoked
    #   'Outcome'   = donor declined, or reported what happened at hospital
    #
    # donation_outcome — what actually happened:
    #   'Pending'       = not yet responded
    #   'Declined'      = said NO cannot come
    #   'Withdrawn'     = cancelled their acceptance
    #   'Donated'       = ✅ successfully donated → START COOLDOWN
    #   'NotNeeded'     = reached hospital but blood not needed → NO cooldown
    #   'Rejected'      = hospital rejected donor medically → NO cooldown
    #
    # alerted_at    = when email was sent
    # confirmed_at  = when donor accepted
    # outcome_at    = when donor declined / withdrew / reported outcome

    # ── NOTIFICATIONS TABLE (new) ─────────────────
    # In-app messages shown under the bell icon in the navigation bar
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            message    TEXT    NOT NULL,
            link       TEXT,
            is_read    INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES donors(id)
        )
    """)

    # ── LOGIN ATTEMPTS TABLE (new) ────────────────
    # Used for rate limiting: too many failures → temporary lock
    conn.execute("""
        CREATE TABLE IF NOT EXISTS login_attempts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            kind         TEXT NOT NULL,
            key          TEXT NOT NULL,
            attempted_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── REQUEST EVENTS TABLE (new) ────────────────
    # A history of every status change, shown as a timeline
    conn.execute("""
        CREATE TABLE IF NOT EXISTS request_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            status     TEXT    NOT NULL,
            note       TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (request_id) REFERENCES blood_requests(id)
        )
    """)

    conn.commit()
    _migrate(conn)
    conn.close()

    # Add demo data for testing
    _add_demo_data()
    logger.info("Database initialized successfully")


# ═══════════════════════════════════════════════════
# MIGRATION
# PURPOSE: Upgrades a database created by the OLD version of the app
# WHY: Existing donors and requests must keep working after the upgrade.
# SAFETY: Before changing anything, a backup copy of the database file is
#         saved as bloodconnect.backup-YYYYMMDD-HHMMSS.db
#         The migration only ADDS columns and renames old status values —
#         no data is deleted. It is safe to run many times.
# ═══════════════════════════════════════════════════

NEW_COLUMNS = {
    "donors": [
        ("role",      "TEXT NOT NULL DEFAULT 'donor'"),
        ("is_active", "INTEGER NOT NULL DEFAULT 1"),
    ],
    "blood_requests": [
        ("owner_id",     "INTEGER"),
        ("public_note",  "TEXT"),
        ("private_note", "TEXT"),
        ("required_by",  "DATE"),
        ("updated_at",   "DATETIME"),
    ],
}


def _columns(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn):
    missing = [(table, col, decl)
               for table, cols in NEW_COLUMNS.items()
               for col, decl in cols
               if col not in _columns(conn, table)]
    legacy = conn.execute(
        "SELECT COUNT(*) FROM blood_requests WHERE status IN ('Open','Fulfilled','Closed')"
    ).fetchone()[0]

    if not missing and not legacy:
        _ensure_indexes(conn)
        return

    has_data = conn.execute("SELECT COUNT(*) FROM donors").fetchone()[0] > 0
    if has_data and os.path.exists(DB_PATH):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = DB_PATH.replace(".db", "") + f".backup-{stamp}.db"
        conn.commit()
        shutil.copyfile(DB_PATH, backup)
        logger.info(f"Database backup created before migration: {backup}")

    for table, col, decl in missing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        logger.info(f"Migration: added column {table}.{col}")

    for old, new in LEGACY_STATUS_MAP.items():
        conn.execute("UPDATE blood_requests SET status=? WHERE status=?", (new, old))

    conn.commit()
    _ensure_indexes(conn)
    logger.info("Database migration complete")


def _ensure_indexes(conn):
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, is_read)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attempts ON login_attempts(kind, key, attempted_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_req ON request_events(request_id)")
    try:
        # One response row per donor per request
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_unique "
                     "ON alerts(request_id, donor_id)")
    except sqlite3.Error:
        # Old databases might already contain duplicates — keep working without the index
        logger.warning("Could not create unique alert index (duplicate rows exist)")
    conn.commit()


def _add_demo_data():
    """Adds sample donors so dashboard is not empty on first run."""
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM donors").fetchone()[0]
    if count == 0 and os.environ.get("DISABLE_DEMO_DATA") != "1":
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
        logger.info("Demo data added — 8 sample donors created (they have no password and cannot log in)")
    conn.close()


# ═══════════════════════════════════════════════════
# DONOR / ACCOUNT FUNCTIONS
# ═══════════════════════════════════════════════════

def add_donor(name, email, phone, age, blood_group, city, area,
              password_hash, role="donor"):
    """
    PURPOSE: Saves a new account to the database
    WHY: When someone fills the registration form,
         their details must be permanently saved
    RETURNS: donor id if successful, None if email already exists
    """
    conn = get_db()
    try:
        cur = conn.execute("""
            INSERT INTO donors (name, email, phone, age, blood_group, city,
                                area, password_hash, role)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, email, phone, age, blood_group, city, area, password_hash, role))
        conn.commit()
        logger.info(f"Account registered: id={cur.lastrowid} role={role}")
        return cur.lastrowid
    except sqlite3.IntegrityError:
        # This error happens when email already exists (UNIQUE constraint)
        logger.warning("Registration failed — email already exists")
        return None
    finally:
        conn.close()


def get_all_donors(role=None):
    """Returns list of all accounts ordered by newest first (optionally one role)."""
    conn = get_db()
    if role:
        donors = conn.execute(
            "SELECT * FROM donors WHERE role=? ORDER BY registered_at DESC", (role,)
        ).fetchall()
    else:
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


def get_donor_by_email(email):
    """Case-insensitive lookup so old mixed-case emails keep working."""
    conn = get_db()
    donor = conn.execute("""
        SELECT * FROM donors
        WHERE LOWER(TRIM(email)) = LOWER(TRIM(?))
    """, (email,)).fetchone()
    conn.close()
    return donor


def email_in_use(email, exclude_id=None):
    donor = get_donor_by_email(email)
    return bool(donor and donor["id"] != exclude_id)


def phone_in_use(phone, exclude_id=None):
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM donors WHERE phone=? AND id != ?",
        (phone, exclude_id or 0)
    ).fetchone()
    conn.close()
    return row is not None


def update_profile(donor_id, name, phone, city, area, role, age, blood_group):
    conn = get_db()
    conn.execute("""
        UPDATE donors
        SET name=?, phone=?, city=?, area=?, role=?, age=?, blood_group=?
        WHERE id=?
    """, (name, phone, city, area, role, age, blood_group, donor_id))
    conn.commit()
    conn.close()
    logger.info(f"Profile updated: donor={donor_id}")


def update_password(donor_id, password_hash):
    conn = get_db()
    conn.execute("UPDATE donors SET password_hash=? WHERE id=?", (password_hash, donor_id))
    conn.commit()
    conn.close()
    logger.info(f"Password changed: donor={donor_id}")


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


def set_availability(donor_id, available):
    conn = get_db()
    conn.execute("UPDATE donors SET is_available=? WHERE id=?",
                 (1 if available else 0, donor_id))
    conn.commit()
    conn.close()
    logger.info(f"Donor {donor_id} availability set to {bool(available)}")


def set_account_active(donor_id, active):
    """Admin can deactivate an account (it can no longer log in or be alerted)."""
    conn = get_db()
    conn.execute("UPDATE donors SET is_active=? WHERE id=?",
                 (1 if active else 0, donor_id))
    conn.commit()
    conn.close()
    logger.info(f"Account {donor_id} active set to {bool(active)}")


def mark_actual_donation(donor_id):
    """
    PURPOSE: Records that donor ACTUALLY donated blood
    WHY: This is Stage 3 of our 3-stage process.
         ONLY when donor confirms actual donation do we:
         1. Set last_donated to today
         2. This triggers the alert pause (cooldown)
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
# MATCHING & COOLDOWN
# PURPOSE: Finds donors to alert for a request
# WHY: Only donors who are compatible, in the same city, available,
#      and not in their alert pause should be notified
# ═══════════════════════════════════════════════════

def get_matching_donors(blood_group, city, exclude_id=None):
    """
    PURPOSE: Finds all donors to alert for a blood request
    WHY: Must have a COMPATIBLE blood group (red cell rules), be in the
         same city, be available, and not be in the alert pause.

    COOLDOWN LOGIC:
    cutoff_date = today minus COOLDOWN_DAYS
    If last_donated is NULL  → never donated → alerted
    If last_donated <= cutoff_date → alerted
    If last_donated > cutoff_date  → donated recently → not alerted

    Exact blood group matches are listed first.
    """
    groups = compatible_donor_groups(blood_group)
    if not groups:
        return []
    cutoff = (datetime.today() - timedelta(days=COOLDOWN_DAYS)).strftime("%Y-%m-%d")
    placeholders = ",".join("?" * len(groups))

    conn = get_db()
    donors = conn.execute(f"""
        SELECT * FROM donors
        WHERE blood_group IN ({placeholders})
        AND   role         = 'donor'
        AND   is_active    = 1
        AND   LOWER(TRIM(city))  = LOWER(TRIM(?))
        AND   is_available = 1
        AND   age          BETWEEN 18 AND 65
        AND   (
                last_donated IS NULL
                OR last_donated <= ?
              )
        AND   id != ?
        ORDER BY (blood_group = ?) DESC, id
    """, (*groups, city, cutoff, exclude_id or 0, blood_group)).fetchall()
    conn.close()

    logger.info(
        f"Matching donors found: {len(donors)} "
        f"for {blood_group} (compatible: {','.join(groups)}) "
        f"(cooldown cutoff: {cutoff})"
    )
    return donors


def get_cooldown_info(donor):
    """
    PURPOSE: Returns human-readable donation reminder status for a donor
    WHY: Shown on donor profile so they know their last donation date.
    NOTE: "eligible" here only means BloodConnect will send alerts again.
          It is NOT a medical decision.
    """
    last = donor["last_donated"]
    if not last:
        return {
            "eligible": True,
            "status": "No donation recorded yet",
            "days_remaining": 0,
            "days_since": None,
            "eligible_from": None,
            "last_donated_display": "Never"
        }

    last_date = datetime.strptime(last, "%Y-%m-%d")
    eligible_from = last_date + timedelta(days=COOLDOWN_DAYS)
    today = datetime.today()
    days_since = (today - last_date).days

    if today >= eligible_from:
        return {
            "eligible": True,
            "status": "Alerts active",
            "days_remaining": 0,
            "days_since": days_since,
            "eligible_from": eligible_from.strftime("%d %b %Y"),
            "last_donated_display": last_date.strftime("%d %b %Y")
        }
    else:
        days_left = (eligible_from - today).days + 1
        return {
            "eligible": False,
            "status": f"Alerts paused — {days_left} more days",
            "days_remaining": days_left,
            "days_since": days_since,
            "eligible_from": eligible_from.strftime("%d %b %Y"),
            "last_donated_display": last_date.strftime("%d %b %Y")
        }


# ═══════════════════════════════════════════════════
# BLOOD REQUEST FUNCTIONS
# ═══════════════════════════════════════════════════

def _add_event(conn, req_id, status, note=None):
    conn.execute(
        "INSERT INTO request_events (request_id, status, note) VALUES (?, ?, ?)",
        (req_id, status, note)
    )


def _set_status(conn, req_id, status, note=None):
    conn.execute(
        "UPDATE blood_requests SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (status, req_id)
    )
    _add_event(conn, req_id, status, note)


def add_blood_request(patient_name, blood_group, units_needed,
                      hospital_name, city, contact_person,
                      contact_phone, urgency, owner_id=None,
                      public_note=None, private_note=None, required_by=None):
    """
    PURPOSE: Saves a new blood request to database
    WHY: Every emergency request must be permanently recorded
         for tracking and reporting purposes
    """
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO blood_requests
        (patient_name, blood_group, units_needed, hospital_name,
         city, contact_person, contact_phone, urgency, status,
         owner_id, public_note, private_note, required_by, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (patient_name, blood_group, units_needed, hospital_name,
          city, contact_person, contact_phone, urgency, STATUS_PENDING,
          owner_id, public_note, private_note, required_by))
    req_id = cur.lastrowid
    _add_event(conn, req_id, STATUS_PENDING, "Request created")
    conn.commit()
    conn.close()
    logger.info(f"Blood request saved: ID={req_id} | {blood_group}")
    return req_id


def find_duplicate_active_request(owner_id, blood_group, hospital_name, city):
    """Stops the same person creating the same active request twice."""
    placeholders = ",".join("?" * len(ACTIVE_STATUSES))
    conn = get_db()
    row = conn.execute(f"""
        SELECT id FROM blood_requests
        WHERE owner_id = ? AND blood_group = ?
        AND LOWER(TRIM(hospital_name)) = LOWER(TRIM(?))
        AND LOWER(TRIM(city)) = LOWER(TRIM(?))
        AND status IN ({placeholders})
    """, (owner_id, blood_group, hospital_name, city, *ACTIVE_STATUSES)).fetchone()
    conn.close()
    return row


def get_all_requests():
    """Returns all blood requests, newest first. ADMIN USE ONLY (has private columns)."""
    conn = get_db()
    reqs = conn.execute(
        "SELECT * FROM blood_requests ORDER BY requested_at DESC"
    ).fetchall()
    conn.close()
    return reqs


def get_request_by_id(req_id):
    """Full row including private columns — only for authorised code paths."""
    conn = get_db()
    req = conn.execute("SELECT * FROM blood_requests WHERE id=?", (req_id,)).fetchone()
    conn.close()
    return req


def get_public_request(req_id):
    """Only the columns that anyone may see."""
    conn = get_db()
    req = conn.execute(f"""
        SELECT {PUBLIC_REQUEST_COLUMNS},
               (SELECT COUNT(*) FROM alerts a WHERE a.request_id = r.id
                AND (a.stage = 'Confirmed' OR a.donation_outcome = 'Donated')) AS accepted_count
        FROM blood_requests r WHERE r.id = ?
    """, (req_id,)).fetchone()
    conn.close()
    return req


def get_private_request_details(req_id):
    """
    PRIVATE details. Call ONLY after app.py has checked that the viewer is
    the requester, an admin, or a donor who has accepted this active request.
    """
    conn = get_db()
    row = conn.execute("""
        SELECT patient_name, contact_person, contact_phone, private_note
        FROM blood_requests WHERE id = ?
    """, (req_id,)).fetchone()
    conn.close()
    return row


def search_requests(blood_group=None, city=None, urgency=None, status="active",
                    compatible_with=None, owner_id=None, limit=100):
    """
    PURPOSE: Search / filter blood requests for the public list and dashboards
    PRIVACY: Returns PUBLIC columns only.
    compatible_with = a donor's blood group → only requests that donor can give to
    """
    sql = f"""
        SELECT {PUBLIC_REQUEST_COLUMNS},
               (SELECT COUNT(*) FROM alerts a WHERE a.request_id = r.id
                AND (a.stage = 'Confirmed' OR a.donation_outcome = 'Donated')) AS accepted_count
        FROM blood_requests r WHERE 1=1
    """
    params = []
    if blood_group:
        sql += " AND r.blood_group = ?"
        params.append(blood_group)
    if compatible_with:
        from validators import DONATE_TO
        groups = DONATE_TO.get(compatible_with, [])
        if not groups:
            return []
        sql += f" AND r.blood_group IN ({','.join('?' * len(groups))})"
        params.extend(groups)
    if city:
        sql += " AND LOWER(r.city) LIKE LOWER(?)"
        params.append(f"%{city.strip()}%")
    if urgency:
        sql += " AND r.urgency = ?"
        params.append(urgency)
    if status == "active":
        sql += f" AND r.status IN ({','.join('?' * len(ACTIVE_STATUSES))})"
        params.extend(ACTIVE_STATUSES)
    elif status and status != "all":
        sql += " AND r.status = ?"
        params.append(status)
    if owner_id:
        sql += " AND r.owner_id = ?"
        params.append(owner_id)
    sql += """ ORDER BY
                 CASE WHEN r.status IN ('Pending','Donor Assigned','In Progress') THEN 0 ELSE 1 END,
                 CASE r.urgency WHEN 'Critical' THEN 0 WHEN 'Urgent' THEN 1 ELSE 2 END,
                 r.requested_at DESC
               LIMIT ?"""
    params.append(limit)

    conn = get_db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def get_requests_by_owner(owner_id):
    """The requester's own requests (they may see their own private fields)."""
    conn = get_db()
    rows = conn.execute("""
        SELECT r.*,
               (SELECT COUNT(*) FROM alerts a WHERE a.request_id = r.id
                AND (a.stage = 'Confirmed' OR a.donation_outcome = 'Donated')) AS accepted_count
        FROM blood_requests r WHERE owner_id = ?
        ORDER BY requested_at DESC
    """, (owner_id,)).fetchall()
    conn.close()
    return rows


def get_request_with_alerts(req_id):
    """
    PURPOSE: Returns a blood request AND all its donor alerts
    WHY: Admin view of a request needs both the request info
         AND the list of donors who were alerted with their responses
    ADMIN USE ONLY — includes donor phone numbers.
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


def get_accepted_donors(req_id):
    """
    Donors who accepted (or donated for) this request — shown to the requester
    so they can coordinate. Donors who only got an alert, declined or
    withdrew are NOT included (their identity stays private).
    """
    conn = get_db()
    rows = conn.execute("""
        SELECT a.id AS alert_id, a.stage, a.donation_outcome, a.confirmed_at,
               d.name AS donor_name, d.phone AS donor_phone, d.blood_group AS donor_bg
        FROM alerts a JOIN donors d ON a.donor_id = d.id
        WHERE a.request_id = ?
        AND (a.stage = 'Confirmed' OR a.donation_outcome = 'Donated')
        ORDER BY a.confirmed_at
    """, (req_id,)).fetchall()
    conn.close()
    return rows


def get_response_summary(req_id):
    """Counts only — safe to show the requester without naming donors."""
    conn = get_db()
    row = conn.execute("""
        SELECT
          SUM(CASE WHEN stage='Alerted' THEN 1 ELSE 0 END)            AS awaiting,
          SUM(CASE WHEN stage='Confirmed' THEN 1 ELSE 0 END)          AS accepted,
          SUM(CASE WHEN donation_outcome='Declined' THEN 1 ELSE 0 END) AS declined,
          SUM(CASE WHEN donation_outcome='Withdrawn' THEN 1 ELSE 0 END) AS withdrawn,
          SUM(CASE WHEN donation_outcome='Donated' THEN 1 ELSE 0 END)  AS donated,
          COUNT(*) AS total
        FROM alerts WHERE request_id = ?
    """, (req_id,)).fetchone()
    conn.close()
    return {k: (row[k] or 0) for k in row.keys()}


def get_request_events(req_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM request_events WHERE request_id=? ORDER BY id", (req_id,)
    ).fetchall()
    conn.close()
    return rows


def update_request_status(req_id, status, note=None):
    """Updates the status of a blood request and records it in the timeline."""
    conn = get_db()
    _set_status(conn, req_id, status, note)
    conn.commit()
    conn.close()
    logger.info(f"Request {req_id} status → {status}")


def update_request_details(req_id, units_needed, urgency, hospital_name,
                           contact_person, contact_phone, public_note,
                           private_note, required_by):
    conn = get_db()
    conn.execute("""
        UPDATE blood_requests
        SET units_needed=?, urgency=?, hospital_name=?, contact_person=?,
            contact_phone=?, public_note=?, private_note=?, required_by=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (units_needed, urgency, hospital_name, contact_person, contact_phone,
          public_note, private_note, required_by, req_id))
    _add_event(conn, req_id, "Updated", "Request details updated")
    conn.commit()
    conn.close()
    logger.info(f"Request {req_id} details updated")


def get_confirmed_donor_ids(req_id):
    """IDs of donors currently holding an accepted (active) response."""
    conn = get_db()
    rows = conn.execute(
        "SELECT donor_id FROM alerts WHERE request_id=? AND stage='Confirmed'", (req_id,)
    ).fetchall()
    conn.close()
    return [r["donor_id"] for r in rows]


# ═══════════════════════════════════════════════════
# ALERT FUNCTIONS — 3 STAGE PROCESS
# Stage 1: Alerted  → Stage 2: Accepted (Confirmed) → Stage 3: Outcome
# ═══════════════════════════════════════════════════

def log_alert(req_id, donor_id):
    """
    PURPOSE: Records that an alert was sent to a donor
    WHY: We need to track which donors were alerted for each
         request so we can show responses on the tracking page
    STAGE: This is Stage 1 — Alert Sent
    """
    conn = get_db()
    cur = conn.execute("""
        INSERT OR IGNORE INTO alerts (request_id, donor_id, stage)
        VALUES (?, ?, 'Alerted')
    """, (req_id, donor_id))

    if cur.rowcount:
        conn.execute("""
            UPDATE blood_requests
            SET donors_alerted = donors_alerted + 1
            WHERE id=?
        """, (req_id,))

    alert_id = conn.execute(
        "SELECT id FROM alerts WHERE request_id=? AND donor_id=?", (req_id, donor_id)
    ).fetchone()["id"]
    conn.commit()
    conn.close()
    logger.info(f"Alert logged: request={req_id} donor={donor_id} alert={alert_id}")
    return alert_id


def get_alert_for(req_id, donor_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM alerts WHERE request_id=? AND donor_id=?", (req_id, donor_id)
    ).fetchone()
    conn.close()
    return row


def accept_request(req_id, donor_id, donor_blood_group):
    """
    PURPOSE: Donor explicitly ACCEPTS a request (Stage 2)
    WHY: Only after this do they get to see the contact details.
    IMPORTANT: Cooldown does NOT start here.
               We don't know yet if they actually donated.
    RETURNS: one of 'ok', 'not_found', 'inactive', 'incompatible',
             'own_request', 'already', 'done', 'full'

    BEGIN IMMEDIATE locks the database for this short moment so two
    donors accepting at the same second cannot both take the last slot.
    """
    from validators import can_donate

    conn = get_db()
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        req = conn.execute(
            "SELECT id, blood_group, units_needed, status, owner_id FROM blood_requests WHERE id=?",
            (req_id,)
        ).fetchone()
        if not req:
            conn.execute("ROLLBACK")
            return "not_found"
        if req["status"] not in ACTIVE_STATUSES:
            conn.execute("ROLLBACK")
            return "inactive"
        if req["owner_id"] == donor_id:
            conn.execute("ROLLBACK")
            return "own_request"
        if not can_donate(donor_blood_group, req["blood_group"]):
            conn.execute("ROLLBACK")
            return "incompatible"

        alert = conn.execute(
            "SELECT * FROM alerts WHERE request_id=? AND donor_id=?", (req_id, donor_id)
        ).fetchone()
        if alert and alert["stage"] == "Confirmed":
            conn.execute("ROLLBACK")
            return "already"
        if alert and alert["donation_outcome"] in ("Donated", "NotNeeded", "Rejected"):
            conn.execute("ROLLBACK")
            return "done"

        taken = conn.execute("""
            SELECT COUNT(*) FROM alerts WHERE request_id=?
            AND (stage='Confirmed' OR donation_outcome='Donated')
        """, (req_id,)).fetchone()[0]
        if taken >= req["units_needed"]:
            conn.execute("ROLLBACK")
            return "full"

        if alert:
            conn.execute("""
                UPDATE alerts
                SET stage='Confirmed', donation_outcome='Pending',
                    confirmed_at=CURRENT_TIMESTAMP, outcome_at=NULL
                WHERE id=?
            """, (alert["id"],))
        else:
            conn.execute("""
                INSERT INTO alerts (request_id, donor_id, stage, confirmed_at)
                VALUES (?, ?, 'Confirmed', CURRENT_TIMESTAMP)
            """, (req_id, donor_id))

        conn.execute("""
            UPDATE blood_requests
            SET donors_confirmed = donors_confirmed + 1, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (req_id,))
        if req["status"] == STATUS_PENDING:
            _set_status(conn, req_id, STATUS_ASSIGNED, "A donor accepted the request")
        else:
            _add_event(conn, req_id, "Accepted", "Another donor accepted the request")
        conn.execute("COMMIT")
        logger.info(f"Donor accepted: request={req_id} donor={donor_id}")
        return "ok"
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def decline_request(req_id, donor_id):
    """
    PURPOSE: Records that donor said NO they cannot help
    WHY: Tracking who declined helps understand response rates
    NOTE: No cooldown. No private details are ever revealed.
          The request stays open for other donors.
    RETURNS: 'ok', 'accepted' (must withdraw instead) or 'done'
    """
    conn = get_db()
    alert = conn.execute(
        "SELECT * FROM alerts WHERE request_id=? AND donor_id=?", (req_id, donor_id)
    ).fetchone()
    if alert and alert["stage"] == "Confirmed":
        conn.close()
        return "accepted"
    if alert and alert["donation_outcome"] in ("Donated", "NotNeeded", "Rejected"):
        conn.close()
        return "done"
    if alert:
        conn.execute("""
            UPDATE alerts
            SET stage='Outcome', donation_outcome='Declined', outcome_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (alert["id"],))
    else:
        conn.execute("""
            INSERT INTO alerts (request_id, donor_id, stage, donation_outcome, outcome_at)
            VALUES (?, ?, 'Outcome', 'Declined', CURRENT_TIMESTAMP)
        """, (req_id, donor_id))
    conn.commit()
    conn.close()
    logger.info(f"Donor declined: request={req_id} donor={donor_id}")
    return "ok"


def _reopen_if_no_donors(conn, req_id, note):
    """If nobody holds an acceptance any more, put the request back to Pending."""
    req = conn.execute("SELECT status FROM blood_requests WHERE id=?", (req_id,)).fetchone()
    still = conn.execute("""
        SELECT COUNT(*) FROM alerts WHERE request_id=?
        AND (stage='Confirmed' OR donation_outcome='Donated')
    """, (req_id,)).fetchone()[0]
    if req and still == 0 and req["status"] in (STATUS_ASSIGNED, STATUS_IN_PROGRESS):
        _set_status(conn, req_id, STATUS_PENDING, note)
        return True
    return False


def withdraw_acceptance(req_id, donor_id):
    """
    PURPOSE: Donor cancels their acceptance → their access to the private
             details is revoked immediately (stage is no longer 'Confirmed').
    RETURNS: 'ok' or 'not_accepted'
    """
    conn = get_db()
    alert = conn.execute(
        "SELECT * FROM alerts WHERE request_id=? AND donor_id=?", (req_id, donor_id)
    ).fetchone()
    if not alert or alert["stage"] != "Confirmed":
        conn.close()
        return "not_accepted"
    conn.execute("""
        UPDATE alerts
        SET stage='Withdrawn', donation_outcome='Withdrawn', outcome_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (alert["id"],))
    conn.execute("""
        UPDATE blood_requests
        SET donors_confirmed = MAX(donors_confirmed - 1, 0), updated_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (req_id,))
    _add_event(conn, req_id, "Withdrawn", "A donor withdrew their acceptance")
    _reopen_if_no_donors(conn, req_id, "No donor assigned any more — open again")
    conn.commit()
    conn.close()
    logger.info(f"Donor withdrew: request={req_id} donor={donor_id}")
    return "ok"


def record_donation_outcome(alert_id, outcome):
    """
    PURPOSE: Records what ACTUALLY happened when donor visited hospital
    WHY: This is Stage 3 — the most important step.
         Only here do we know if blood was actually donated.

    OUTCOME OPTIONS:
    'Donated'   → Donor successfully donated blood
                  → START alert pause (cooldown) NOW
                  → Request becomes Completed once enough units are donated

    'NotNeeded' → Donor reached hospital but blood was already
                  found from another source / patient no longer needed it
                  → NO cooldown — donor gets full credit for showing up

    'Rejected'  → Hospital/doctor found donor medically unfit
                  (low hemoglobin, on medication, fever, etc.)
                  → NO cooldown — they didn't actually donate
                  → Request goes back to Pending if no other donor is assigned

    WHY THIS MATTERS:
    Without this stage, we would wrongly start cooldown when:
    - Blood was no longer needed (patient got blood elsewhere)
    - Doctor rejected the donor
    Both situations are common in real emergencies.

    RETURNS: dict with request_id, donor_id, completed (bool)
    """
    conn = get_db()
    row = conn.execute(
        "SELECT donor_id, request_id FROM alerts WHERE id=?", (alert_id,)
    ).fetchone()
    if not row:
        conn.close()
        return None

    # Update alert with outcome
    conn.execute("""
        UPDATE alerts
        SET stage            = 'Outcome',
            donation_outcome = ?,
            outcome_at       = CURRENT_TIMESTAMP
        WHERE id=?
    """, (outcome, alert_id))
    conn.execute("""
        UPDATE blood_requests
        SET donors_confirmed = MAX(donors_confirmed - 1, 0), updated_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (row["request_id"],))

    completed = False
    if outcome == "Donated":
        # START COOLDOWN — set last_donated to today
        today = datetime.today().strftime("%Y-%m-%d")
        conn.execute(
            "UPDATE donors SET last_donated=? WHERE id=?",
            (today, row["donor_id"])
        )
        # Update donated count on request
        conn.execute("""
            UPDATE blood_requests
            SET donors_donated = donors_donated + 1
            WHERE id=?
        """, (row["request_id"],))
        req = conn.execute(
            "SELECT units_needed, donors_donated, status FROM blood_requests WHERE id=?",
            (row["request_id"],)
        ).fetchone()
        if req["donors_donated"] >= req["units_needed"] and req["status"] in ACTIVE_STATUSES:
            _set_status(conn, row["request_id"], STATUS_COMPLETED, "Required donations received")
            completed = True
        else:
            _add_event(conn, row["request_id"], "Donated", "A donor reported a successful donation")
        logger.info(
            f"Donation confirmed: donor={row['donor_id']} "
            f"request={row['request_id']} — cooldown started"
        )

    elif outcome == "NotNeeded":
        _add_event(conn, row["request_id"], "Not needed", "A donor reported blood was not needed")
        logger.info(f"Blood not needed at hospital: alert={alert_id} — NO cooldown")

    elif outcome == "Rejected":
        _add_event(conn, row["request_id"], "Donor deferred", "A donor could not donate (medical check)")
        _reopen_if_no_donors(conn, row["request_id"], "Looking for another donor")
        logger.info(f"Donor medically rejected: alert={alert_id} — NO cooldown")

    conn.commit()
    conn.close()
    return {"request_id": row["request_id"], "donor_id": row["donor_id"], "completed": completed}


def get_alert_by_id(alert_id):
    """
    PURPOSE: Gets alert details with PUBLIC request info and the donor's name
    WHY: Outcome page needs to know which request the alert belongs to.
    PRIVACY: Private request columns are NOT selected here.
    """
    conn = get_db()
    alert = conn.execute("""
        SELECT
            a.*,
            d.name        AS donor_name,
            d.email       AS donor_email,
            r.blood_group,
            r.hospital_name,
            r.urgency,
            r.units_needed,
            r.city,
            r.status      AS request_status
        FROM alerts a
        JOIN donors  d ON a.donor_id   = d.id
        JOIN blood_requests r ON a.request_id = r.id
        WHERE a.id=?
    """, (alert_id,)).fetchone()
    conn.close()
    return alert


def get_donor_alerts(donor_id):
    """A donor's own responses, with PUBLIC request info only."""
    conn = get_db()
    alerts = conn.execute("""
        SELECT a.*, br.blood_group, br.city, br.urgency, br.hospital_name,
               br.status AS request_status, br.required_by, br.units_needed
        FROM alerts a
        JOIN blood_requests br ON a.request_id = br.id
        WHERE a.donor_id = ?
        ORDER BY a.alerted_at DESC
    """, (donor_id,)).fetchall()
    conn.close()
    return alerts


# ═══════════════════════════════════════════════════
# NOTIFICATIONS
# ═══════════════════════════════════════════════════

def add_notification(user_id, message, link=None):
    add_notifications([user_id], message, link)


def add_notifications(user_ids, message, link=None):
    if not user_ids:
        return
    conn = get_db()
    conn.executemany(
        "INSERT INTO notifications (user_id, message, link) VALUES (?, ?, ?)",
        [(uid, message, link) for uid in user_ids if uid]
    )
    conn.commit()
    conn.close()


def get_notifications(user_id, limit=50):
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM notifications WHERE user_id=?
        ORDER BY id DESC LIMIT ?
    """, (user_id, limit)).fetchall()
    conn.close()
    return rows


def unread_notification_count(user_id):
    conn = get_db()
    n = conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0", (user_id,)
    ).fetchone()[0]
    conn.close()
    return n


def mark_notifications_read(user_id):
    conn = get_db()
    conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════
# RATE LIMITING
# PURPOSE: Counts recent attempts (e.g. failed logins) per key
# WHY: Stops attackers guessing passwords over and over
# ═══════════════════════════════════════════════════

def record_attempt(kind, key):
    conn = get_db()
    conn.execute("INSERT INTO login_attempts (kind, key) VALUES (?, ?)", (kind, key))
    conn.execute("DELETE FROM login_attempts WHERE attempted_at < datetime('now', '-1 day')")
    conn.commit()
    conn.close()


def count_recent_attempts(kind, key, minutes):
    conn = get_db()
    n = conn.execute("""
        SELECT COUNT(*) FROM login_attempts
        WHERE kind=? AND key=? AND attempted_at > datetime('now', ?)
    """, (kind, key, f"-{int(minutes)} minutes")).fetchone()[0]
    conn.close()
    return n


def clear_attempts(kind, key):
    conn = get_db()
    conn.execute("DELETE FROM login_attempts WHERE kind=? AND key=?", (kind, key))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════
# STATS FUNCTION
# PURPOSE: Returns counts for dashboard display
# WHY: Home page and dashboard show live statistics
# ═══════════════════════════════════════════════════

def get_stats():
    active_ph = ",".join("?" * len(ACTIVE_STATUSES))
    conn = get_db()
    active   = conn.execute("SELECT COUNT(*) FROM donors WHERE is_available=1 AND role='donor' AND is_active=1").fetchone()[0]
    open_r   = conn.execute(f"SELECT COUNT(*) FROM blood_requests WHERE status IN ({active_ph})", ACTIVE_STATUSES).fetchone()[0]
    pending  = conn.execute("SELECT COUNT(*) FROM blood_requests WHERE status=?", (STATUS_PENDING,)).fetchone()[0]
    done     = conn.execute("SELECT COUNT(*) FROM blood_requests WHERE status=?", (STATUS_COMPLETED,)).fetchone()[0]
    cancelled = conn.execute("SELECT COUNT(*) FROM blood_requests WHERE status=?", (STATUS_CANCELLED,)).fetchone()[0]
    expired  = conn.execute("SELECT COUNT(*) FROM blood_requests WHERE status=?", (STATUS_EXPIRED,)).fetchone()[0]
    total_r  = conn.execute("SELECT COUNT(*) FROM blood_requests").fetchone()[0]
    cities   = conn.execute("SELECT COUNT(DISTINCT LOWER(TRIM(city))) FROM donors WHERE role='donor'").fetchone()[0]
    donated  = conn.execute("SELECT COUNT(*) FROM alerts WHERE donation_outcome='Donated'").fetchone()[0]
    donors_n = conn.execute("SELECT COUNT(*) FROM donors WHERE role='donor'").fetchone()[0]
    recips_n = conn.execute("SELECT COUNT(*) FROM donors WHERE role='recipient'").fetchone()[0]
    by_group = conn.execute("""
        SELECT blood_group, COUNT(*) AS n FROM donors
        WHERE role='donor' GROUP BY blood_group ORDER BY n DESC
    """).fetchall()
    req_by_group = conn.execute("""
        SELECT blood_group, COUNT(*) AS n FROM blood_requests
        GROUP BY blood_group ORDER BY n DESC
    """).fetchall()
    by_city = conn.execute("""
        SELECT city, COUNT(*) AS n FROM blood_requests
        GROUP BY LOWER(TRIM(city)) ORDER BY n DESC LIMIT 8
    """).fetchall()
    conn.close()
    return {
        "active_donors": active,
        "open_requests":  open_r,
        "pending_requests": pending,
        "fulfilled":      done,
        "cancelled":      cancelled,
        "expired":        expired,
        "total_requests": total_r,
        "cities":         cities,
        "total_donations": donated,
        "total_donors":   donors_n,
        "total_recipients": recips_n,
        "donors_by_group": by_group,
        "requests_by_group": req_by_group,
        "requests_by_city": by_city,
    }


def expire_old_requests():
    """
    PURPOSE: Automatically expires requests that are too old.
    WHY: Blood emergencies are time sensitive. Keeping old requests
    open wastes donor time and damages trust.
    RULES:
      Pending with a 'needed by' date → expires the day after that date
      Pending without a date → Critical 24h / Urgent 48h / Normal 72h
      Donor Assigned / In Progress with no update for 7 days → expires
    Expiry also revokes donors' access to private details
    (access requires an active status).
    RETURNS: list of (request_id, owner_id, [confirmed donor ids]) expired
    """
    conn = get_db()
    rows = conn.execute("""
        SELECT id, owner_id FROM blood_requests
        WHERE (
            status = 'Pending' AND (
                (required_by IS NOT NULL AND date('now') > required_by)
                OR (required_by IS NULL AND (
                    (urgency = 'Critical' AND requested_at <= datetime('now', '-24 hours'))
                    OR (urgency = 'Urgent' AND requested_at <= datetime('now', '-48 hours'))
                    OR (urgency = 'Normal' AND requested_at <= datetime('now', '-72 hours'))
                ))
            )
        ) OR (
            status IN ('Donor Assigned', 'In Progress')
            AND COALESCE(updated_at, requested_at) <= datetime('now', '-7 days')
        )
    """).fetchall()
    expired = []
    for r in rows:
        donor_ids = [a["donor_id"] for a in conn.execute(
            "SELECT donor_id FROM alerts WHERE request_id=? AND stage='Confirmed'", (r["id"],)
        )]
        _set_status(conn, r["id"], STATUS_EXPIRED, "Expired automatically")
        expired.append((r["id"], r["owner_id"], donor_ids))
    conn.commit()
    conn.close()
    if expired:
        logger.info(f"Old requests expired automatically: {len(expired)}")
    return expired
