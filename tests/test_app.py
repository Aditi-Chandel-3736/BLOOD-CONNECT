"""
tests/test_app.py — Automated checks for BloodConnect
======================================================
Run from the project folder with:

    python -m unittest discover -s tests -v

Each test uses a brand-new temporary database, so your real
bloodconnect.db is never touched.
"""

import os
import re
import sqlite3
import sys
import tempfile
import time
import unittest

# Make the project folder importable (app.py, database.py, ...)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Settings must exist BEFORE the app is imported
_TMP = tempfile.mkdtemp(prefix="bloodconnect-tests-")
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "test.db")
os.environ["ADMIN_PASSWORD"] = "Admin#Test2026"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["DISABLE_DEMO_DATA"] = "1"
os.environ.pop("EMAIL_ADDRESS", None)
os.environ.pop("EMAIL_PASSWORD", None)

import database                      # noqa: E402
import email_sender                  # noqa: E402
import app as app_module             # noqa: E402

STRONG = "Str0ng!Pass"


class BaseCase(unittest.TestCase):
    def setUp(self):
        # Fresh database for every test
        if os.path.exists(database.DB_PATH):
            os.remove(database.DB_PATH)
        database.init_db()
        app_module.app.config["TESTING"] = True
        self.clients = []

    # ── helpers ──────────────────────────────────
    def client(self):
        c = app_module.app.test_client()
        self.clients.append(c)
        return c

    def csrf(self, c):
        # Any page with a form gives the browser a CSRF token
        for page in ("/", "/donor/login"):
            c.get(page)
            with c.session_transaction() as s:
                if "_csrf" in s:
                    return s["_csrf"]
        raise AssertionError("no CSRF token issued")

    def post(self, c, url, data=None, **kw):
        data = dict(data or {})
        data.setdefault("csrf_token", self.csrf(c))
        return c.post(url, data=data, **kw)

    def register(self, c, email, phone, role="donor", bg="O+", city="Mumbai",
                 password=STRONG, confirm=None, age="30", name="Test User"):
        return self.post(c, "/register", {
            "role": role, "name": name, "email": email, "phone": phone, "age": age,
            "blood_group": bg, "city": city, "area": "Andheri",
            "password": password, "confirm_password": confirm if confirm is not None else password,
        })

    def login(self, c, email, password=STRONG):
        return self.post(c, "/donor/login", {"email": email, "password": password})

    def new_user(self, email, phone, **kw):
        c = self.client()
        r = self.register(c, email, phone, **kw)
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:500])
        return c

    def create_request(self, c, **over):
        data = {"patient_name": "Secret Patient", "blood_group": "O+", "units_needed": "1",
                "hospital_name": "City Hospital", "city": "Mumbai",
                "contact_person": "Private Contact", "contact_phone": "9123456789",
                "urgency": "Urgent", "public_note": "Planned surgery",
                "private_note": "Ward 7B bed 12"}
        data.update(over)
        r = self.post(c, "/request-blood", data)
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:800])
        return int(re.search(r"/request/(\d+)", r.headers["Location"]).group(1))

    def db(self, sql, args=()):
        conn = sqlite3.connect(database.DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, args).fetchall()
        conn.close()
        return rows

    PRIVATE_BITS = ("Secret Patient", "Private Contact", "9123456789", "Ward 7B")

    def assertNoPrivate(self, html):
        for bit in self.PRIVATE_BITS:
            self.assertNotIn(bit, html, f"private data leaked: {bit}")


# ════════════════════════════════════════════════════════
# 1–4  REGISTRATION, PHONE & PASSWORD VALIDATION
# ════════════════════════════════════════════════════════

class RegistrationTests(BaseCase):
    def test_01_valid_phone_registers_and_stores_digits(self):
        c = self.client()
        r = self.register(c, "Asha@Example.com", "9876501234")
        self.assertEqual(r.status_code, 302)
        row = self.db("SELECT * FROM donors")[0]
        self.assertEqual(row["phone"], "9876501234")
        self.assertEqual(row["email"], "asha@example.com")          # stored lowercase
        self.assertTrue(row["password_hash"].startswith("scrypt:"))  # never plain text
        self.assertNotIn(STRONG, row["password_hash"])

    def test_02_fewer_than_10_digits_rejected(self):
        r = self.register(self.client(), "a@example.com", "98765")
        self.assertEqual(r.status_code, 400)
        self.assertIn("exactly 10 digits", r.get_data(as_text=True))
        self.assertEqual(len(self.db("SELECT * FROM donors")), 0)

    def test_03_more_than_10_digits_or_non_digits_rejected_by_server(self):
        for bad in ("98765432101", "98765 43210", "+919876543210", "98765abcde", "5876543210"):
            r = self.register(self.client(), f"x{len(bad)}{bad[:3]}@example.com", bad)
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(len(self.db("SELECT * FROM donors")), 0)

    def test_03b_phone_field_limits_typing_in_browser(self):
        html = self.client().get("/register").get_data(as_text=True)
        tag = re.search(r'<input type="tel" id="phone"[^>]+>', html, re.S).group(0)
        for attr in ('maxlength="10"', 'inputmode="numeric"', 'pattern="[6-9][0-9]{9}"', "data-phone"):
            self.assertIn(attr, tag)

    def test_04_weak_and_strong_passwords(self):
        for weak in ("short1!", "alllowercase1!", "ALLUPPERCASE1!", "NoNumbers!!", "NoSpecial123"):
            r = self.register(self.client(), f"w{len(weak)}@example.com", "9876500000", password=weak)
            self.assertEqual(r.status_code, 400, weak)
            self.assertIn("Password needs", r.get_data(as_text=True))
        r = self.register(self.client(), "mismatch@example.com", "9876500001", confirm="Different!1A")
        self.assertIn("Passwords do not match", r.get_data(as_text=True))
        r = self.register(self.client(), "strong@example.com", "9876500002")
        self.assertEqual(r.status_code, 302)

    def test_duplicate_email_and_phone_prevented(self):
        self.new_user("dup@example.com", "9876500010")
        r = self.register(self.client(), "DUP@example.com", "9876500011")
        self.assertIn("already exists", r.get_data(as_text=True))
        r = self.register(self.client(), "other@example.com", "9876500010")
        self.assertIn("already registered", r.get_data(as_text=True))

    def test_name_validation(self):
        r = self.register(self.client(), "n@example.com", "9876500020", name="<script>")
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("<script>", r.get_data(as_text=True))  # escaped, never executed

    def test_recipient_account_needs_no_blood_group(self):
        c = self.client()
        r = self.register(c, "family@example.com", "9876500030", role="recipient", bg="", age="")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.db("SELECT role FROM donors")[0]["role"], "recipient")


# ════════════════════════════════════════════════════════
# 5–7  LOGIN, SESSIONS & PROTECTED PAGES
# ════════════════════════════════════════════════════════

class AuthTests(BaseCase):
    def test_05_login_with_correct_password(self):
        self.new_user("login@example.com", "9876500100")
        c = self.client()
        r = self.login(c, "LOGIN@example.com")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.get("/my-profile").status_code, 200)

    def test_06_wrong_password_denied_and_rate_limited(self):
        self.new_user("victim@example.com", "9876500101")
        c = self.client()
        for _ in range(5):
            r = self.login(c, "victim@example.com", "Wrong!Pass1")
            self.assertEqual(r.status_code, 401)
            self.assertIn("Incorrect email or password", r.get_data(as_text=True))
        self.assertEqual(c.get("/my-profile").status_code, 302)       # still not logged in
        r = self.login(c, "victim@example.com", STRONG)                # even correct pw is paused
        self.assertEqual(r.status_code, 429)

    def test_unknown_email_gets_same_message(self):
        r = self.login(self.client(), "nobody@example.com", "Whatever1!")
        self.assertEqual(r.status_code, 401)
        self.assertIn("Incorrect email or password", r.get_data(as_text=True))

    def test_demo_donor_without_password_does_not_crash(self):
        database.add_donor("Demo", "demo@test.com", "9876500102", 30, "O+", "Mumbai", "X", None)
        r = self.login(self.client(), "demo@test.com", "anything")
        self.assertEqual(r.status_code, 401)

    def test_07_protected_pages_require_login(self):
        c = self.client()
        for url in ("/my-profile", "/request-blood", "/notifications", "/my-profile/edit",
                    "/my-profile/password", "/request/1"):
            r = c.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/donor/login", r.headers["Location"])
        r = c.get("/dashboard")
        self.assertIn("/admin/login", r.headers["Location"])
        self.assertEqual(c.get("/donor/1").status_code, 403)

    def test_donor_cannot_open_admin_dashboard(self):
        c = self.new_user("d@example.com", "9876500103")
        self.assertIn("/admin/login", c.get("/dashboard").headers["Location"])

    def test_admin_login(self):
        c = self.client()
        self.assertEqual(self.post(c, "/admin/login", {"password": "wrong"}).status_code, 401)
        r = self.post(c, "/admin/login", {"password": "Admin#Test2026"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.get("/dashboard").status_code, 200)

    def test_logout(self):
        c = self.new_user("out@example.com", "9876500104")
        self.post(c, "/logout")
        self.assertEqual(c.get("/my-profile").status_code, 302)

    def test_csrf_token_required(self):
        c = self.new_user("csrf@example.com", "9876500105")
        r = c.post("/my-profile/availability", data={"available": "0"})
        self.assertEqual(r.status_code, 400)

    def test_session_expires_after_inactivity(self):
        c = self.new_user("idle@example.com", "9876500106")
        with c.session_transaction() as s:
            s["last_seen"] = int(time.time()) - 2 * 3600
        self.assertEqual(c.get("/my-profile").status_code, 302)

    def test_password_change_signs_out_other_devices(self):
        c1 = self.new_user("pw@example.com", "9876500107")
        c2 = self.client()
        self.login(c2, "pw@example.com")
        r = self.post(c1, "/my-profile/password", {"current_password": "Bad!pass1",
                                                  "new_password": "N3w!Password", "confirm_password": "N3w!Password"})
        self.assertEqual(r.status_code, 400)
        r = self.post(c1, "/my-profile/password", {"current_password": STRONG,
                                                  "new_password": "N3w!Password", "confirm_password": "N3w!Password"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c1.get("/my-profile").status_code, 200)   # this device stays in
        self.assertEqual(c2.get("/my-profile").status_code, 302)   # other device signed out
        self.assertEqual(self.login(self.client(), "pw@example.com", "N3w!Password").status_code, 302)

    def test_open_redirect_blocked(self):
        self.new_user("redir@example.com", "9876500108")
        c = self.client()
        data = {"email": "redir@example.com", "password": STRONG, "csrf_token": self.csrf(c)}
        r = c.post("/donor/login?next=//evil.example.com", data=data)
        self.assertNotIn("evil", r.headers["Location"])

    def test_security_headers(self):
        r = self.client().get("/")
        self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")


# ════════════════════════════════════════════════════════
# 8–13  PRIVACY WORKFLOW, STATUSES & NOTIFICATIONS
# ════════════════════════════════════════════════════════

class PrivacyWorkflowTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.owner = self.new_user("owner@example.com", "9000000001", role="recipient", bg="", age="")
        self.donor = self.new_user("donor@example.com", "9000000002", bg="O-")
        self.donor2 = self.new_user("donor2@example.com", "9000000003", bg="O+")
        self.wrong_group = self.new_user("abpos@example.com", "9000000004", bg="AB+")
        self.req_id = self.create_request(self.owner)

    def test_matching_alerts_only_compatible_available_donors(self):
        alerted = {r["donor_id"] for r in self.db("SELECT donor_id FROM alerts WHERE request_id=?", (self.req_id,))}
        ids = {r["email"]: r["id"] for r in self.db("SELECT id, email FROM donors")}
        self.assertEqual(alerted, {ids["donor@example.com"], ids["donor2@example.com"]})  # O- and O+ give to O+
        notes = self.db("SELECT user_id FROM notifications WHERE message LIKE 'New %'")
        self.assertEqual({n["user_id"] for n in notes}, alerted)

    def test_08_donor_sees_only_public_details_before_accepting(self):
        for url in (f"/request/{self.req_id}", "/requests", "/", "/my-profile"):
            html = self.donor.get(url).get_data(as_text=True)
            self.assertNoPrivate(html)
        html = self.donor.get(f"/request/{self.req_id}").get_data(as_text=True)
        self.assertIn("City Hospital", html)
        self.assertIn("Accept request", html)
        # Anonymous visitors: public list only, detail page requires login
        anon = self.client()
        self.assertNoPrivate(anon.get("/requests").get_data(as_text=True))
        self.assertNoPrivate(anon.get("/").get_data(as_text=True))

    def test_09_accepting_reveals_permitted_details_and_notifies_owner(self):
        r = self.post(self.donor, f"/request/{self.req_id}/accept")
        self.assertEqual(r.status_code, 302)
        html = self.donor.get(f"/request/{self.req_id}").get_data(as_text=True)
        for bit in self.PRIVATE_BITS:
            self.assertIn(bit, html)
        self.assertEqual(self.db("SELECT status FROM blood_requests")[0]["status"], "Donor Assigned")
        owner_id = self.db("SELECT id FROM donors WHERE email='owner@example.com'")[0]["id"]
        notes = self.db("SELECT message FROM notifications WHERE user_id=?", (owner_id,))
        self.assertTrue(any("accepted your" in n["message"] for n in notes))
        # The owner now sees the donor's phone
        self.assertIn("9000000002", self.owner.get(f"/request/{self.req_id}").get_data(as_text=True))

    def test_10_declining_reveals_nothing_and_request_stays_open(self):
        r = self.post(self.donor, f"/request/{self.req_id}/decline")
        self.assertEqual(r.status_code, 302)
        self.assertNoPrivate(self.donor.get(f"/request/{self.req_id}").get_data(as_text=True))
        self.assertEqual(self.db("SELECT status FROM blood_requests")[0]["status"], "Pending")
        outcome = self.db("SELECT donation_outcome FROM alerts a JOIN donors d ON d.id=a.donor_id "
                          "WHERE d.email='donor@example.com'")[0]["donation_outcome"]
        self.assertEqual(outcome, "Declined")
        # Another donor can still accept
        self.post(self.donor2, f"/request/{self.req_id}/accept")
        self.assertIn("9123456789", self.donor2.get(f"/request/{self.req_id}").get_data(as_text=True))
        # Owner does not see the declining donor's identity
        self.assertNotIn("9000000002", self.owner.get(f"/request/{self.req_id}").get_data(as_text=True))

    def test_11_cannot_access_other_peoples_private_data_by_changing_ids(self):
        self.post(self.donor, f"/request/{self.req_id}/accept")
        alert_id = self.db("SELECT a.id FROM alerts a JOIN donors d ON d.id=a.donor_id "
                           "WHERE d.email='donor@example.com'")[0]["id"]
        # Another donor tries the first donor's outcome link
        self.assertEqual(self.donor2.get(f"/outcome/{alert_id}").status_code, 404)
        self.assertEqual(self.post(self.donor2, f"/outcome/{alert_id}", {"outcome": "Donated"}).status_code, 404)
        # Old e-mail links no longer reveal or change anything
        self.assertEqual(self.donor2.get(f"/confirm/{alert_id}/yes").status_code, 404)
        # Non-owners cannot cancel, edit or change status
        self.assertEqual(self.post(self.donor2, f"/request/{self.req_id}/cancel").status_code, 403)
        self.assertEqual(self.post(self.donor2, f"/request/{self.req_id}/close").status_code, 403)
        self.assertEqual(self.donor2.get(f"/request/{self.req_id}/edit").status_code, 403)
        self.assertEqual(self.post(self.donor2, f"/request/{self.req_id}/status", {"status": "Completed"}).status_code, 403)
        # Incompatible donor cannot accept and sees nothing private
        self.post(self.wrong_group, f"/request/{self.req_id}/accept")
        self.assertNoPrivate(self.wrong_group.get(f"/request/{self.req_id}").get_data(as_text=True))
        # Non-existent request
        self.assertEqual(self.donor2.get("/request/9999").status_code, 404)
        # Toggling another donor's availability is blocked
        other_id = self.db("SELECT id FROM donors WHERE email='donor@example.com'")[0]["id"]
        self.assertEqual(self.post(self.donor2, f"/donor/{other_id}/toggle").status_code, 403)
        self.assertEqual(self.donor2.get(f"/donor/{other_id}").status_code, 404)

    def test_units_limit_blocks_extra_acceptances(self):
        self.post(self.donor, f"/request/{self.req_id}/accept")
        self.post(self.donor2, f"/request/{self.req_id}/accept")       # units_needed = 1 → full
        self.assertNoPrivate(self.donor2.get(f"/request/{self.req_id}").get_data(as_text=True))

    def test_12_update_and_cancel_revokes_access(self):
        self.post(self.donor, f"/request/{self.req_id}/accept")
        r = self.post(self.owner, f"/request/{self.req_id}/edit", {
            "units_needed": "2", "urgency": "Critical", "hospital_name": "City Hospital",
            "contact_person": "Private Contact", "contact_phone": "9123456789",
            "public_note": "", "private_note": "Ward 7B bed 14"})
        self.assertEqual(r.status_code, 302)
        row = self.db("SELECT units_needed, urgency FROM blood_requests")[0]
        self.assertEqual((row["units_needed"], row["urgency"]), (2, "Critical"))
        # Public note may not contain a phone number
        r = self.post(self.owner, f"/request/{self.req_id}/edit", {
            "units_needed": "2", "urgency": "Critical", "hospital_name": "City Hospital",
            "contact_person": "Private Contact", "contact_phone": "9123456789",
            "public_note": "call 9876543210"})
        self.assertEqual(r.status_code, 400)

        r = self.post(self.owner, f"/request/{self.req_id}/cancel")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.db("SELECT status FROM blood_requests")[0]["status"], "Cancelled")
        self.assertNoPrivate(self.donor.get(f"/request/{self.req_id}").get_data(as_text=True))
        donor_id = self.db("SELECT id FROM donors WHERE email='donor@example.com'")[0]["id"]
        notes = self.db("SELECT message FROM notifications WHERE user_id=?", (donor_id,))
        self.assertTrue(any("cancelled" in n["message"] for n in notes))

    def test_withdraw_revokes_access_and_reopens(self):
        self.post(self.donor, f"/request/{self.req_id}/accept")
        self.post(self.donor, f"/request/{self.req_id}/withdraw")
        self.assertNoPrivate(self.donor.get(f"/request/{self.req_id}").get_data(as_text=True))
        self.assertEqual(self.db("SELECT status FROM blood_requests")[0]["status"], "Pending")

    def test_13_outcome_completes_request_and_starts_pause(self):
        self.post(self.donor, f"/request/{self.req_id}/accept")
        alert_id = self.db("SELECT a.id FROM alerts a JOIN donors d ON d.id=a.donor_id "
                           "WHERE d.email='donor@example.com'")[0]["id"]
        r = self.post(self.donor, f"/outcome/{alert_id}", {"outcome": "Donated"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("data-celebrate", r.get_data(as_text=True))
        self.assertEqual(self.db("SELECT status FROM blood_requests")[0]["status"], "Completed")
        self.assertIsNotNone(self.db("SELECT last_donated FROM donors WHERE email='donor@example.com'")[0]["last_donated"])
        # Reporting twice is refused
        self.assertEqual(self.post(self.donor, f"/outcome/{alert_id}", {"outcome": "Donated"}).status_code, 302)
        statuses = [e["status"] for e in self.db("SELECT status FROM request_events ORDER BY id")]
        self.assertEqual(statuses[:2], ["Pending", "Donor Assigned"])
        self.assertIn("Completed", statuses)
        owner_id = self.db("SELECT id FROM donors WHERE email='owner@example.com'")[0]["id"]
        self.assertTrue(self.db("SELECT 1 FROM notifications WHERE user_id=? AND message LIKE '%Completed%'", (owner_id,)))

    def test_in_progress_status(self):
        self.post(self.donor, f"/request/{self.req_id}/accept")
        self.post(self.owner, f"/request/{self.req_id}/status", {"status": "In Progress"})
        self.assertEqual(self.db("SELECT status FROM blood_requests")[0]["status"], "In Progress")

    def test_duplicate_request_prevented(self):
        r = self.post(self.owner, "/request-blood", {
            "patient_name": "Secret Patient", "blood_group": "O+", "units_needed": "1",
            "hospital_name": "city hospital", "city": "mumbai", "contact_person": "Private Contact",
            "contact_phone": "9123456789", "urgency": "Urgent"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(self.db("SELECT id FROM blood_requests")), 1)

    def test_unavailable_donor_not_alerted(self):
        self.post(self.donor, "/my-profile/availability", {"available": "0"})
        rid = self.create_request(self.owner, hospital_name="Second Hospital")
        alerted = self.db("SELECT d.email FROM alerts a JOIN donors d ON d.id=a.donor_id WHERE request_id=?", (rid,))
        self.assertEqual([a["email"] for a in alerted], ["donor2@example.com"])

    def test_filters(self):
        self.create_request(self.owner, hospital_name="Pune Clinic", city="Pune", blood_group="A+", urgency="Critical")
        html = self.client().get("/requests?city=pune&urgency=Critical").get_data(as_text=True)
        self.assertIn("Pune Clinic", html)
        self.assertNotIn("City Hospital", html)
        html = self.client().get("/requests?blood_group=O%2B").get_data(as_text=True)
        self.assertIn("City Hospital", html)
        self.assertNotIn("Pune Clinic", html)

    def test_admin_sees_everything(self):
        admin = self.client()
        self.post(admin, "/admin/login", {"password": "Admin#Test2026"})
        html = admin.get(f"/request/{self.req_id}").get_data(as_text=True)
        self.assertIn("Secret Patient", html)
        self.assertIn("Secret Patient", admin.get("/dashboard").get_data(as_text=True))


# ════════════════════════════════════════════════════════
# EMAILS & MIGRATION
# ════════════════════════════════════════════════════════

class EmailTests(BaseCase):
    def test_alert_email_has_no_private_data_and_is_escaped(self):
        sent = {}
        original = email_sender._send
        email_sender._send = lambda to, subject, html: sent.update(html=html) or True
        try:
            email_sender.send_donor_alert("d@example.com", "<b>Donor</b>", 5, "O+", 1,
                                          "<script>alert(1)</script>", "Mumbai", "Critical")
        finally:
            email_sender._send = original
        self.assertNotIn("<script>", sent["html"])
        self.assertNotIn("<b>Donor</b>", sent["html"])
        self.assertIn("/request/5", sent["html"])


class MigrationTests(unittest.TestCase):
    def test_old_database_is_upgraded_with_backup(self):
        folder = tempfile.mkdtemp(prefix="bc-migrate-")
        path = os.path.join(folder, "bloodconnect.db")
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE donors (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
              phone TEXT NOT NULL, age INTEGER NOT NULL, blood_group TEXT NOT NULL, city TEXT NOT NULL, area TEXT NOT NULL,
              is_available INTEGER DEFAULT 1, last_donated DATE, registered_at DATETIME DEFAULT CURRENT_TIMESTAMP, password_hash TEXT);
            CREATE TABLE blood_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, patient_name TEXT NOT NULL, blood_group TEXT NOT NULL,
              units_needed INTEGER NOT NULL, hospital_name TEXT NOT NULL, city TEXT NOT NULL, contact_person TEXT NOT NULL,
              contact_phone TEXT NOT NULL, urgency TEXT DEFAULT 'Normal', status TEXT DEFAULT 'Open', donors_alerted INTEGER DEFAULT 0,
              donors_confirmed INTEGER DEFAULT 0, donors_donated INTEGER DEFAULT 0, requested_at DATETIME DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL, donor_id INTEGER NOT NULL,
              stage TEXT DEFAULT 'Alerted', donation_outcome TEXT DEFAULT 'Pending', alerted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              confirmed_at DATETIME, outcome_at DATETIME);
            INSERT INTO donors (name,email,phone,age,blood_group,city,area,password_hash)
              VALUES ('Old User','Old@Example.com','9876543210',30,'O+','Mumbai','X','pbkdf2:sha256:600000$x$y');
            INSERT INTO blood_requests (patient_name,blood_group,units_needed,hospital_name,city,contact_person,contact_phone,status)
              VALUES ('P','O+',1,'H','Mumbai','C','9876543210','Open'),('P','O+',1,'H','Mumbai','C','9876543210','Fulfilled');
        """)
        conn.commit()
        conn.close()

        saved = database.DB_PATH
        database.DB_PATH = path
        try:
            database.init_db()
            database.init_db()   # running twice is safe
            donor = database.get_donor_by_email("old@example.com")   # case-insensitive
            self.assertEqual(donor["role"], "donor")
            statuses = [r["status"] for r in database.get_all_requests()]
            self.assertEqual(sorted(statuses), ["Completed", "Pending"])
        finally:
            database.DB_PATH = saved
        backups = [f for f in os.listdir(folder) if ".backup-" in f]
        self.assertEqual(len(backups), 1)


if __name__ == "__main__":
    unittest.main()
