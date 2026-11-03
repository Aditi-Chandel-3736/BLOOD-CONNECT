"""
validators.py — BloodConnect Input Validation & Blood Compatibility
====================================================================
PURPOSE: Every piece of data a user types is checked HERE on the server.
WHY NECESSARY: Browser checks (HTML/JavaScript) can be skipped by anyone
who knows how, so the server must never trust what it receives.

Each validate_* function returns an error message (string) when the
value is wrong, or None when the value is fine.
"""

import re
from datetime import date, datetime, timedelta

BLOOD_GROUPS   = ["A+", "A-", "B+", "B-", "O+", "O-", "AB+", "AB-"]
URGENCY_LEVELS = ["Normal", "Urgent", "Critical"]
ROLES          = ["donor", "recipient"]

# ═══════════════════════════════════════════════════
# BLOOD GROUP COMPATIBILITY (red blood cells only)
# PURPOSE: Which donor groups can give red cells to which patient groups
# SOURCE: Standard ABO / RhD red cell compatibility rules used by
#         blood services (e.g. NHS Blood and Transplant, Red Cross).
# IMPORTANT: This is an information aid. The hospital blood bank always
#            does its own grouping and cross-matching before transfusion.
# ═══════════════════════════════════════════════════

# patient (recipient) group → donor groups whose red cells are compatible
RECEIVE_FROM = {
    "O-":  ["O-"],
    "O+":  ["O+", "O-"],
    "A-":  ["A-", "O-"],
    "A+":  ["A+", "A-", "O+", "O-"],
    "B-":  ["B-", "O-"],
    "B+":  ["B+", "B-", "O+", "O-"],
    "AB-": ["AB-", "A-", "B-", "O-"],
    "AB+": ["AB+", "AB-", "A+", "A-", "B+", "B-", "O+", "O-"],
}

# donor group → patient groups the donor's red cells are compatible with
DONATE_TO = {
    bg: [patient for patient, donors in RECEIVE_FROM.items() if bg in donors]
    for bg in BLOOD_GROUPS
}


def compatible_donor_groups(patient_group):
    """Donor blood groups that can give red cells to this patient group."""
    return RECEIVE_FROM.get(patient_group, [])


def can_donate(donor_group, patient_group):
    """True if a donor's red cells are compatible with the patient's group."""
    return donor_group in RECEIVE_FROM.get(patient_group, [])


# ═══════════════════════════════════════════════════
# FIELD VALIDATORS
# ═══════════════════════════════════════════════════

# Indian mobile numbers: exactly 10 digits, first digit 6, 7, 8 or 9
PHONE_RE = re.compile(r"^[6-9]\d{9}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
PLACE_RE = re.compile(r"^[^\W_][\w .,'()/&#:-]*$", re.UNICODE)
# Something that looks like a phone number or email inside free text
CONTACT_IN_TEXT_RE = re.compile(r"(\d[\s-]?){7,}|[^@\s]+@[^@\s]+\.\w+")


def validate_name(name, label="Name"):
    if not name:
        return f"{label} is required."
    if len(name) < 2 or len(name) > 60:
        return f"{label} must be between 2 and 60 characters."
    for ch in name:
        if not (ch.isalpha() or ch in " .'-"):
            return f"{label} can only contain letters, spaces, dots, apostrophes and hyphens."
    if sum(ch.isalpha() for ch in name) < 2:
        return f"{label} must contain at least 2 letters."
    return None


def normalize_email(email):
    """Emails are case-insensitive, so we always store them in lowercase."""
    return (email or "").strip().lower()


def validate_email(email):
    if not email:
        return "Email is required."
    if len(email) > 254 or not EMAIL_RE.match(email):
        return "Please enter a valid email address, for example name@example.com."
    return None


def validate_phone(phone, label="Phone number"):
    """
    PURPOSE: Accepts ONLY a 10-digit Indian mobile number.
    No spaces, no +91, no letters — the browser removes those while
    typing, and the server rejects anything that still slips through.
    """
    if not phone:
        return f"{label} is required."
    if not phone.isdigit():
        return f"{label} must contain digits only (no spaces, + or letters)."
    if len(phone) != 10:
        return f"{label} must be exactly 10 digits — you entered {len(phone)}."
    if not PHONE_RE.match(phone):
        return f"{label} must be a valid Indian mobile number starting with 6, 7, 8 or 9."
    return None


PASSWORD_RULES = [
    (lambda p: len(p) >= 8,                      "at least 8 characters"),
    (lambda p: re.search(r"[A-Z]", p),           "one uppercase letter (A-Z)"),
    (lambda p: re.search(r"[a-z]", p),           "one lowercase letter (a-z)"),
    (lambda p: re.search(r"\d", p),              "one number (0-9)"),
    (lambda p: re.search(r"[^A-Za-z0-9\s]", p),  "one special character (e.g. ! @ # $ %)"),
]


def validate_password(password):
    if not password:
        return "Password is required."
    if len(password) > 128:
        return "Password must be 128 characters or fewer."
    missing = [text for rule, text in PASSWORD_RULES if not rule(password)]
    if missing:
        return "Password needs " + ", ".join(missing) + "."
    return None


def validate_age(age_str):
    """RETURNS: (age, error)"""
    try:
        age = int(age_str)
    except (TypeError, ValueError):
        return None, "Please enter your age as a number."
    if age < 18 or age > 65:
        return None, "Donors must be between 18 and 65 years old to register."
    return age, None


def validate_blood_group(bg):
    if bg not in BLOOD_GROUPS:
        return "Please select a blood group."
    return None


def validate_place(value, label, max_len=60):
    if not value:
        return f"{label} is required."
    if len(value) < 2 or len(value) > max_len:
        return f"{label} must be between 2 and {max_len} characters."
    if not PLACE_RE.match(value):
        return f"{label} contains characters that are not allowed."
    return None


def validate_units(units_str):
    """RETURNS: (units, error)"""
    try:
        units = int(units_str)
    except (TypeError, ValueError):
        return None, "Units needed must be a number."
    if units < 1 or units > 10:
        return None, "Units needed must be between 1 and 10."
    return units, None


def validate_urgency(urgency):
    if urgency not in URGENCY_LEVELS:
        return "Please choose an urgency level."
    return None


def validate_note(note, label, public=False, max_len=300):
    if not note:
        return None
    if len(note) > max_len:
        return f"{label} must be {max_len} characters or fewer."
    if public and CONTACT_IN_TEXT_RE.search(note):
        return (f"{label} is visible to everyone — please do not include phone "
                f"numbers or email addresses. Donors see contact details only after accepting.")
    return None


def validate_required_by(value):
    """RETURNS: (date_string_or_None, error). Optional field."""
    if not value:
        return None, None
    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None, "Please enter a valid date."
    if d < date.today():
        return None, "The 'needed by' date cannot be in the past."
    if d > date.today() + timedelta(days=60):
        return None, "The 'needed by' date must be within the next 60 days."
    return d.isoformat(), None
