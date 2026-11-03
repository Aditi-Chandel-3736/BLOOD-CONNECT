"""
security.py — BloodConnect Security Helpers
============================================
PURPOSE: Small, reusable protections used by app.py:

1. CSRF protection  — every POST form must carry a secret token that only
                      our own pages know. Stops other websites from
                      submitting forms on a logged-in user's behalf.
2. Login decorators — @login_required / @admin_required block protected
                      pages for visitors who are not signed in.
3. Rate limiting    — too many failed attempts → temporary lock.
4. Safe redirects   — "?next=" can only point inside this website.
5. Secret key       — loaded from .env, or generated once and saved locally.
"""

import hashlib
import hmac
import os
import secrets
from functools import wraps

from flask import (session, request, redirect, url_for, flash, abort, g)
from markupsafe import Markup, escape

from database import record_attempt, count_recent_attempts, clear_attempts


# ═══════════════════════════════════════════════════
# SECRET KEY
# WHY: Flask signs the session cookie with this key. If someone knows it,
#      they can forge a login. It must be long, random and private.
# ═══════════════════════════════════════════════════

def load_secret_key(path=".flask_secret_key"):
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    # No SECRET_KEY in .env → create one random key and keep it in a local
    # file (listed in .gitignore) so logins survive restarts.
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key)
    except FileExistsError:
        # Another worker created it at the same moment — use theirs
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return key


# ═══════════════════════════════════════════════════
# CSRF PROTECTION
# ═══════════════════════════════════════════════════

def get_csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def csrf_field():
    """Used in templates as {{ csrf_field() }} inside every POST form."""
    return Markup('<input type="hidden" name="csrf_token" value="%s">'
                  % escape(get_csrf_token()))


def csrf_protect():
    """Called before every request. Rejects POSTs without a valid token."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        expected = session.get("_csrf", "")
        if not sent or not expected or not hmac.compare_digest(sent, expected):
            abort(400, description="csrf")


# ═══════════════════════════════════════════════════
# AUTH HELPERS
# ═══════════════════════════════════════════════════

def auth_version(donor):
    """
    A fingerprint of the account's password hash, stored in the session.
    When the password changes, the fingerprint changes and every OTHER
    logged-in session for that account is signed out automatically.
    """
    return hashlib.sha256((donor["password_hash"] or "").encode()).hexdigest()[:16]


def is_safe_next(target):
    """Only allow redirects to pages on this site (prevents open redirects)."""
    return bool(target) and target.startswith("/") and not target.startswith("//") \
        and "\\" not in target


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.get("user"):
            flash("Please log in to continue.", "warning")
            return redirect(url_for("donor_login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.get("is_admin"):
            flash("Admin access only. Please log in as admin.", "warning")
            return redirect(url_for("admin_login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapped


# ═══════════════════════════════════════════════════
# RATE LIMITING
# ═══════════════════════════════════════════════════

def client_ip():
    return request.remote_addr or "unknown"


def is_rate_limited(kind, key, limit, minutes):
    return count_recent_attempts(kind, key, minutes) >= limit


def hit(kind, key):
    record_attempt(kind, key)


def reset(kind, key):
    clear_attempts(kind, key)


# ═══════════════════════════════════════════════════
# SECURITY HEADERS
# ═══════════════════════════════════════════════════

CSP = ("default-src 'self'; "
       "script-src 'self'; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; "
       "img-src 'self' data:; "
       "form-action 'self'; "
       "frame-ancestors 'none'; "
       "base-uri 'self'")


def add_security_headers(response):
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    # Pages with personal data must not be stored by the browser cache
    if g.get("user") or g.get("is_admin"):
        response.headers["Cache-Control"] = "no-store"
    return response
