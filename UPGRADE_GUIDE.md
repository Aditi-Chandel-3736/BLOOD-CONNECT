# BloodConnect Upgrade Guide

This guide explains what was changed, how to run the upgraded app in VS Code, and how to test every feature on your own computer.

---

## 1. What I found in the original project

| Part | Technology |
|---|---|
| Backend | Python + Flask 3.0 (`app.py`) |
| Database | SQLite file `bloodconnect.db` (`database.py`) |
| Pages | Jinja2 HTML templates (`templates/`) |
| Passwords | Werkzeug hashing (scrypt) |
| Email | Gmail SMTP (`email_sender.py`) |
| Background job | APScheduler (expires old requests) |

These serious problems were fixed:

1. **The admin dashboard (`/dashboard`) had no login check.** Anyone could see every donor's email and phone number.
2. **Donor profiles (`/donor/5`) were public**, and anyone could switch any donor's availability on or off.
3. **The home page showed every patient's name and the family's phone number** to all visitors.
4. **Alert emails contained the patient's name and phone number** before the donor had agreed to anything.
5. **Email links like `/confirm/15/yes` and `/outcome/15` worked for anyone.** Changing the number in the URL let a person see another patient's details, or falsely mark a donation (which paused that donor's alerts).
6. **No form had CSRF protection**, so another website could submit forms on a logged-in user's behalf.
7. The admin password defaulted to `admin123`, and the check could be repeated without limit.
8. **Logging in as a demo donor crashed the server** (HTTP 500), because demo donors have no password.
9. The app ran with `debug=True` on `0.0.0.0`, which exposed Flask's debugger to everyone on your network.
10. Blood matching used the exact blood group only, not medical compatibility.
11. The "WHO 56-day" rule was presented as a medical fact. WHO's blood safety fact sheet does not state a universal 56-day interval, and Indian guidance differs.

---

## 2. Architecture changes (and why)

I kept Flask, SQLite and the templates. I did **not** rebuild the app. The changes below were necessary:

* **Creating a blood request now requires an account.** Without knowing who created a request, we cannot (a) let only the requester see donor responses, (b) let only them cancel or edit it, or (c) notify them when a donor accepts. It also discourages fake requests.
* **Registration has two account types: Donor and Requester.** Both live in the existing `donors` table, which gets a new `role` column. No new user table was needed.
* **Privacy is enforced on the server.** Public pages run SQL that never selects the private columns. Private details are loaded only after the `request_access()` check in `app.py` confirms the viewer is the requester, an admin, or a donor who has accepted that request. The check runs on every request, so changing an ID in the URL does not help.
* **The admin password now comes only from `.env`.** There is no default any more.

---

## 3. Files changed

### Modified files

| File | What changed |
|---|---|
| `app.py` | All routes rewritten with login checks, validation, privacy gate, accept/decline/withdraw, cancel/edit/status, notifications, error pages |
| `database.py` | Automatic safe migration, new tables, compatibility matching, privacy-safe queries, status history |
| `email_sender.py` | Emails escape user text; alert emails contain no private data; email addresses masked in logs |
| `requirements.txt` | Pinned `Werkzeug` and `apscheduler` versions (no new libraries) |
| `.gitignore` | Added `.flask_secret_key` |
| `.github/workflows/deploy.yml` | CI now runs the automated tests |
| `templates/*.html` | Redesigned every existing page |

### New files

| File | Purpose |
|---|---|
| `security.py` | CSRF protection, login decorators, rate limiting, security headers |
| `validators.py` | All server-side validation + blood compatibility table |
| `static/css/style.css` | The whole design system (colours, animations, reduced-motion support) |
| `static/js/app.js` | Phone digit limit, password meter, show/hide, double-submit prevention, counters, facts carousel |
| `static/img/favicon.svg` | Blood-drop browser icon |
| `templates/_macros.html`, `_request_fields.html` | Reusable form pieces |
| `templates/requests.html` | Search & filter page |
| `templates/edit_profile.html`, `change_password.html`, `notifications.html`, `request_edit.html`, `error.html` | New pages |
| `tests/test_app.py` | 37 automated tests |
| `.env.example` | Template for your settings |

### Deleted file

* `templates/confirm.html`. It showed patient details to anyone who opened an email link. Old email links still work: they now send the logged-in donor to the request page.

---

## 4. Database changes (automatic and safe)

You do **not** need to run anything by hand. On the first start, `init_db()` in `database.py`:

1. **Makes a backup copy** of your database, for example `bloodconnect.backup-20261103-231500.db`. This happens only if the database already has data.
2. Adds new columns:
   * `donors`: `role`, `is_active`
   * `blood_requests`: `owner_id`, `public_note`, `private_note`, `required_by`, `updated_at`
3. Renames old statuses: `Open` → `Pending`, `Fulfilled` → `Completed`, `Closed` → `Cancelled`.
4. Creates new tables: `notifications`, `login_attempts`, `request_events`.

No rows are deleted. Running it again changes nothing.

**To undo:** stop the app, delete `bloodconnect.db`, and rename the backup file to `bloodconnect.db`.

Existing accounts keep their passwords. Old requests created without an account have no owner, so only the admin can manage them.

---

## 5. Run the project in VS Code (Windows)

Open the `BLOOD-CONNECT` folder in VS Code, then open **Terminal → New Terminal** (PowerShell) and run these one at a time.

1. Create the virtual environment. It already exists on this computer, so you can skip this step here:
   ```powershell
   python -m venv venv
   ```
2. Install the libraries:
   ```powershell
   .\venv\Scripts\python -m pip install -r requirements.txt
   ```
3. Create your settings file:
   ```powershell
   Copy-Item .env.example .env
   ```
4. Open `.env` in VS Code and set `ADMIN_PASSWORD=` to a long password of your own. Save the file.
5. Start the app:
   ```powershell
   .\venv\Scripts\python app.py
   ```
6. Open **http://127.0.0.1:5000** in your browser. Press `Ctrl + C` in the terminal to stop the app.

> If you prefer to "activate" the venv (`.\venv\Scripts\Activate.ps1`) and PowerShell says scripts are disabled, use the commands above instead. They work without activation.

### Run the automated tests
```powershell
.\venv\Scripts\python -m unittest discover -s tests -v
```
The tests use a temporary database, so your real data is never touched. Expected result: `Ran 37 tests ... OK`.

---

## 6. Manual testing checklist (localhost)

The 8 demo donors cannot log in (they have no password). Create your own test accounts instead. Use a normal window for account A and a **private/incognito window** for account B, so both can be logged in at the same time.

| # | What to test | How | Expected result |
|---|---|---|---|
| 1 | Valid phone | Register with phone `9876500001` | Account created, you land on your dashboard |
| 2 | Too few digits | Type `98765` and submit | Red message "must be exactly 10 digits"; not registered |
| 3 | Too many digits | Try to type or paste `98765432101234` | Field stops at 10 digits. Pasting `+91 98765 43210` becomes `9876543210` |
| 4 | Weak / strong password | Type `abc` and watch the meter, then `Str0ng!Pass` | Checklist ticks turn green; weak password is refused |
| 5 | Correct login | Log out, then log in | Dashboard opens |
| 6 | Wrong password | Enter a wrong password 5 times | "Incorrect email or password"; the 6th try is paused for 15 minutes |
| 7 | Protected pages | Log out, open `/my-profile`, `/dashboard`, `/request-blood` | You are sent to a login page |
| 8 | Before accepting | **Window A:** register as *Requester* (city Mumbai) and create an O+ request. **Window B:** register as an *O-* or *O+* donor in Mumbai and open the request | B sees hospital, city, units and urgency, but **no** patient name or phone. Check DevTools → Network → the page response too |
| 9 | Accept | B clicks **Accept request** | Hearts animation; contact phone and patient name appear. A gets a 🔔 notification and sees B's phone |
| 10 | Decline | With a third donor, click **Decline** | Nothing private shown; request stays Pending/Donor Assigned |
| 11 | Change IDs | As a different donor, open `/outcome/1`, `/request/1/edit`, and POST cancel | "Page not found" or "Access denied" |
| 12 | Update & cancel | A clicks **Edit details**, changes units; then **Cancel request** | Changes saved. B loses access to the contact details and gets a notification |
| 13 | Status & notifications | Create a new request, B accepts → **Report outcome → I donated** | Status becomes Completed; timeline shows each step; A is notified |
| 14 | Responsive | Press `F12` → device toolbar → iPhone size | Menu becomes ☰; no sideways scrolling |
| 15 | Admin | Go to `/admin/login`, use your `.env` password | Stats, charts, user and request management |

---

## 7. Honest notes and limits

* **Emails:** Without `EMAIL_ADDRESS` / `EMAIL_PASSWORD` in `.env`, emails are skipped and a line is written to the terminal. In-app notifications (🔔) always work.
* **Password reset by email** is not implemented. It needs a working email setup to be secure. Users can change their password from the dashboard while logged in.
* **Email address** cannot be changed from the profile page, because it is the login.
* **Donation interval:** the app pauses alerts for 56 days after a recorded donation (`DONATION_ALERT_PAUSE_DAYS` in `.env`). This is a reminder setting, **not** a medical rule. The site always tells donors to confirm eligibility with a blood bank or doctor.
* **`README.md`** still describes the old version, including the "WHO 56-day" claim. It is saved in an unusual UTF-16 encoding, so I left it untouched. Consider updating it.
* **Production (Docker/Railway):** set `SECRET_KEY`, `ADMIN_PASSWORD`, `SESSION_COOKIE_SECURE=1` (with HTTPS) and `TRUST_PROXY=1` as environment variables.

### Sources for the "Did you know?" facts
* WHO — Blood safety and availability: https://www.who.int/news-room/fact-sheets/detail/blood-safety-and-availability
* NHS Blood and Transplant — Blood types: https://www.blood.co.uk/why-give-blood/blood-types/
