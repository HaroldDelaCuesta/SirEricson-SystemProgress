"""Subdivision Portal: core app (login, security checks, uploads). Each role's pages live in their own file:
sysadmin.py, subadmin.py, resident.py, maintenance.py, security_desk.py, features.py (notifications)."""
import hashlib
import hmac
import os
import re
import secrets

from flask import (Flask, abort, flash, g, redirect, render_template, request, send_from_directory,
                   session, url_for)
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

from common import *            # noqa: F401,F403  (helpers; also re-exported for create_admin.py)
from common import (ABSOLUTE_TIMEOUT, APP_NAME, BASE_DIR, CURRENCY, IDLE_TIMEOUT, ROLE_HOME, ROLE_LABELS,
                    UPLOAD_DIR, audit, client_ip, csrf_token, db, drop_user_sessions, execute, get_setting,
                    login_required, notify_role, password_problem, query, record_login, DUMMY_HASH)
from mailer import send_mail

USE_HTTPS = os.environ.get("USE_HTTPS") == "1"                 # set to 1 once the site is served over HTTPS
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://127.0.0.1:5000").rstrip("/")   # used in reset-password emails


def load_secret_key():
    """Use SECRET_KEY if set; otherwise create a random one once and keep it in .secret_key."""
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    path = os.path.join(BASE_DIR, ".secret_key")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w") as f:
        f.write(key)
    return key


app = Flask(__name__)
app.secret_key = load_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,       # JavaScript can't read the cookie
    SESSION_COOKIE_SAMESITE="Lax",      # not sent on cross-site POSTs
    SESSION_COOKIE_SECURE=USE_HTTPS,    # only over HTTPS when enabled
    MAX_CONTENT_LENGTH=40 * 1024 * 1024,
)
app.jinja_env.filters["money"] = lambda v: f"{float(v or 0):,.2f}"

LOCK_WINDOW_MIN = 15          # failed-login counting window
MAX_FAILS_PER_ACCOUNT = 5
MAX_FAILS_PER_IP = 20


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


# ---------- request hooks ----------
def is_locked(ident, ip):
    window = f"created_at >= NOW() - INTERVAL {LOCK_WINDOW_MIN} MINUTE"
    by_account = query(f"SELECT COUNT(*) AS n FROM login_attempts WHERE identifier=%s AND {window}",
                       (ident,), one=True)["n"]
    by_ip = query(f"SELECT COUNT(*) AS n FROM login_attempts WHERE ip=%s AND {window}", (ip,), one=True)["n"]
    return by_account >= MAX_FAILS_PER_ACCOUNT or by_ip >= MAX_FAILS_PER_IP


def record_failure(ident, ip):
    execute("INSERT INTO login_attempts (identifier, ip) VALUES (%s,%s)", (ident, ip))
    execute("DELETE FROM login_attempts WHERE created_at < NOW() - INTERVAL 1 DAY")


@app.before_request
def validate_session():
    """Every request: the login must still exist on the server, and the account must still be allowed in."""
    g.user = None
    g.expired = False
    if request.endpoint == "static":
        return
    token = session.get("token")
    if not token:
        return
    row = query(
        "SELECT TIMESTAMPDIFF(SECOND, s.last_seen, NOW()) AS idle, "
        "TIMESTAMPDIFF(SECOND, s.created_at, NOW()) AS age, "
        "u.id, u.full_name, u.role, u.status, u.must_change_password "
        "FROM user_sessions s JOIN users u ON u.id = s.user_id WHERE s.token=%s", (token,), one=True)

    message = None
    if not row:
        message = "Your session has ended. Please log in again."
    elif row["status"] != "active":
        message = "This account is deactivated. Contact the system administrator."
    elif row["idle"] > IDLE_TIMEOUT:
        message = "You were logged out after a period of inactivity."
    elif row["age"] > ABSOLUTE_TIMEOUT:
        message = "Your session expired. Please log in again."

    if message:
        execute("DELETE FROM user_sessions WHERE token=%s", (token,))
        session.clear()
        g.expired = True
        flash(message, "info")
        return

    if row["idle"] > 60:   # refresh "last seen" at most once a minute
        execute("UPDATE user_sessions SET last_seen=NOW() WHERE token=%s", (token,))
    g.user = row
    session.update(user_id=row["id"], role=row["role"], name=row["full_name"])


@app.before_request
def enforce_password_change():
    if g.user and g.user["must_change_password"] and \
            request.endpoint not in ("change_password", "logout", "static"):
        return redirect(url_for("change_password"))


@app.before_request
def check_csrf():
    if request.method == "POST":
        token = request.form.get("csrf_token", "")
        expected = session.get("csrf", "")
        if not token or not expected or not hmac.compare_digest(token.encode(), expected.encode()):
            abort(400, "Invalid or missing form token. Go back, refresh, and try again.")


CSP = ("default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; object-src 'none'; "
       "base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


@app.after_request
def secure_headers(resp):
    if request.endpoint != "static":
        # Stops the browser from showing a saved copy of a page when someone presses Back after logging out.
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if USE_HTTPS:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp


@app.context_processor
def inject_globals():
    name, badge, unread = APP_NAME, 0, 0
    try:
        name = get_setting("site_name", APP_NAME)
        if g.get("user"):
            unread = query("SELECT COUNT(*) AS n FROM notifications WHERE user_id=%s AND is_read=0",
                           (g.user["id"],), one=True)["n"]
            if g.user["role"] == "sysadmin":
                badge = query("SELECT COUNT(*) AS n FROM users WHERE status='pending'", one=True)["n"]
            elif g.user["role"] == "subadmin":
                badge = query("SELECT COUNT(*) AS n FROM maintenance_requests WHERE assigned_to IS NULL",
                              one=True)["n"]
            elif g.user["role"] == "security":
                badge = query("SELECT COUNT(*) AS n FROM lost_found WHERE status='claimed'", one=True)["n"]
    except Exception:
        pass
    return dict(csrf_token=csrf_token, app_name=name, role_labels=ROLE_LABELS, pending_badge=badge,
                notif_count=unread, currency=CURRENCY)


# ---------- error pages ----------
def error_page(code, message):
    return render_template("error.html", code=code, message=message), code


@app.errorhandler(403)
def forbidden(_e):
    return error_page(403, "Your account doesn't have access to this page.")


@app.errorhandler(404)
def not_found(_e):
    return error_page(404, "That page doesn't exist.")


@app.errorhandler(400)
def bad_request(e):
    return error_page(400, e.description)


@app.errorhandler(413)
def too_large(_e):
    return error_page(413, "That upload is too large. Use smaller photos (8 MB each at most).")


@app.errorhandler(Exception)
def server_error(e):
    """Anything unexpected is written to the error log the System Administrator can read."""
    if isinstance(e, HTTPException):
        return e
    app.logger.exception("Unhandled error")
    try:
        execute("INSERT INTO system_errors (user_id, path, message) VALUES (%s,%s,%s)",
                ((g.user["id"] if g.get("user") else None), request.path[:255], f"{type(e).__name__}: {e}"))
    except Exception:
        pass
    return error_page(500, "Something went wrong on our side. It has been logged.")


# ---------- login, sign-up, passwords ----------
@app.route("/")
def index():
    if g.user:
        return redirect(url_for(ROLE_HOME[g.user["role"]]))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for(ROLE_HOME[g.user["role"]]))
    if request.method == "POST":
        ident = request.form.get("identifier", "").strip().lower()[:120]
        password = request.form.get("password", "")[:200]
        ip = client_ip()

        if is_locked(ident, ip):
            record_login(None, ident, False, "locked")
            flash(f"Too many failed attempts. Please wait {LOCK_WINDOW_MIN} minutes and try again.", "error")
            return render_template("login.html"), 429

        user = query("SELECT * FROM users WHERE username=%s OR email=%s", (ident, ident), one=True)
        # Always run one hash check, so a missing account takes as long as a wrong password.
        valid = check_password_hash(user["password_hash"] if user else DUMMY_HASH, password) and user is not None

        if not valid:
            record_failure(ident, ip)
            record_login(user["id"] if user else None, ident, False, "bad_password" if user else "unknown_user")
            flash("Username or password is incorrect.", "error")
        elif user["status"] == "pending":
            record_login(user["id"], ident, False, "pending")
            flash("Your account is waiting for approval from the system administrator.", "info")
        elif user["status"] == "inactive":
            record_login(user["id"], ident, False, "inactive")
            flash("This account is deactivated. Contact the system administrator.", "error")
        else:
            execute("DELETE FROM login_attempts WHERE identifier=%s", (ident,))
            execute("DELETE FROM user_sessions WHERE last_seen < NOW() - INTERVAL 1 DAY")
            execute("DELETE FROM login_history WHERE created_at < NOW() - INTERVAL 180 DAY")
            token = secrets.token_urlsafe(32)
            execute("INSERT INTO user_sessions (token, user_id, ip, user_agent) VALUES (%s,%s,%s,%s)",
                    (token, user["id"], ip, request.headers.get("User-Agent", "")[:255]))
            session.clear()   # fresh session on every login
            session["token"] = token
            session.update(user_id=user["id"], role=user["role"], name=user["full_name"])
            execute("UPDATE users SET last_login=NOW() WHERE id=%s", (user["id"],))
            record_login(user["id"], ident, True, "ok")
            if user["must_change_password"]:
                return redirect(url_for("change_password"))
            return redirect(url_for(ROLE_HOME[user["role"]]))
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    """Public sign-up is for residents only. Other accounts are created by the system admin."""
    if get_setting("allow_registration", "1") != "1":
        return render_template("register.html", form={}, closed=True)

    form = {}
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in
                ("full_name", "username", "email", "phone", "address")}
        form["username"] = form["username"].lower()
        form["email"] = form["email"].lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        errors = []
        if not all([form["full_name"], form["username"], form["email"], form["address"]]):
            errors.append("Fill in your name, username, email, and address.")
        problem = password_problem(password, form["username"], form["email"].split("@")[0])
        if problem:
            errors.append(problem)
        if password != confirm:
            errors.append("The two passwords don't match.")
        if not errors and query("SELECT id FROM users WHERE username=%s OR email=%s",
                                (form["username"], form["email"]), one=True):
            errors.append("That username or email is already registered.")

        if errors:
            for e in errors:
                flash(e, "error")
        else:
            uid = execute(
                "INSERT INTO users (full_name, username, email, phone, address, "
                "password_hash, role, status) VALUES (%s,%s,%s,%s,%s,%s,'resident','pending')",
                (form["full_name"], form["username"], form["email"], form["phone"],
                 form["address"], generate_password_hash(password)),
            )
            audit("account.register", f"{form['username']} ({form['address']})", user_id=uid)
            notify_role("sysadmin", f"New resident sign-up waiting for approval: {form['full_name']}",
                        "/system/accounts?status=pending")
            flash("Account created. You can log in once the system administrator approves it.", "success")
            return redirect(url_for("login"))
    return render_template("register.html", form=form, closed=False)


@app.route("/logout", methods=["POST"])
def logout():
    if session.get("token"):   # end the login on the server too, so a copied cookie stops working
        execute("DELETE FROM user_sessions WHERE token=%s", (session["token"],))
    session.clear()
    flash("You've been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/account/password", methods=["GET", "POST"])
@login_required()
def change_password():
    forced = bool(g.user["must_change_password"])
    if request.method == "POST":
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        confirm = request.form.get("confirm", "")
        row = query("SELECT password_hash, username, email FROM users WHERE id=%s", (g.user["id"],), one=True)
        problem = password_problem(new, row["username"], row["email"].split("@")[0])

        if not check_password_hash(row["password_hash"], current):
            flash("Your current password is incorrect.", "error")
        elif new != confirm:
            flash("The two new passwords don't match.", "error")
        elif check_password_hash(row["password_hash"], new):
            flash("Choose a password different from your current one.", "error")
        elif problem:
            flash(problem, "error")
        else:
            execute("UPDATE users SET password_hash=%s, must_change_password=0 WHERE id=%s",
                    (generate_password_hash(new), g.user["id"]))
            drop_user_sessions(g.user["id"], keep_token=session["token"])
            audit("password.change", "")
            flash("Password changed. Any other devices were logged out.", "success")
            return redirect(url_for(ROLE_HOME[g.user["role"]]))
    return render_template("password.html", forced=forced)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Self-service reset: we email a one-time link that works for 30 minutes."""
    if g.user:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()[:120]
        ip = client_ip()
        by_ip = query("SELECT COUNT(*) AS n FROM audit_log WHERE action='password.forgot' AND ip=%s "
                      "AND created_at > NOW() - INTERVAL 1 HOUR", (ip,), one=True)["n"]
        by_email = query("SELECT COUNT(*) AS n FROM audit_log WHERE action='password.forgot' AND detail=%s "
                         "AND created_at > NOW() - INTERVAL 1 HOUR", (email,), one=True)["n"]
        audit("password.forgot", email)
        user = query("SELECT id, full_name, email FROM users WHERE email=%s AND status='active'",
                     (email,), one=True)
        if user and by_ip < 10 and by_email < 3:
            raw = secrets.token_urlsafe(32)
            execute("INSERT INTO password_resets (user_id, token_hash, expires_at, ip) "
                    "VALUES (%s,%s,NOW() + INTERVAL 30 MINUTE,%s)", (user["id"], sha256(raw), ip))
            link = f"{PUBLIC_URL}{url_for('reset_password', token=raw)}"
            try:
                send_mail(user["email"], f"{get_setting('site_name', APP_NAME)}: reset your password",
                          f"Hello {user['full_name']},\n\nSomeone asked to reset the password for this account. "
                          f"If it was you, open this link within 30 minutes:\n\n{link}\n\n"
                          "If it wasn't you, ignore this email. Your password stays the same.\n")
            except Exception as exc:
                app.logger.exception("Could not send reset email")
                execute("INSERT INTO system_errors (user_id, path, message) VALUES (%s,%s,%s)",
                        (user["id"], "/forgot-password", f"Email failed: {type(exc).__name__}: {exc}"))
        # Same answer whether or not the address exists, so nobody can use this to find accounts.
        flash("If that email belongs to an active account, we've sent a reset link. It works for 30 minutes.", "info")
        return redirect(url_for("login"))
    return render_template("forgot.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    row = query("SELECT pr.id, pr.user_id, u.username, u.email FROM password_resets pr "
                "JOIN users u ON u.id=pr.user_id WHERE pr.token_hash=%s AND pr.used_at IS NULL "
                "AND pr.expires_at > NOW() AND u.status='active'", (sha256(token),), one=True)
    if not row:
        return render_template("reset.html", invalid=True), 400
    if request.method == "POST":
        new = request.form.get("new", "")
        confirm = request.form.get("confirm", "")
        problem = password_problem(new, row["username"], row["email"].split("@")[0])
        if new != confirm:
            flash("The two passwords don't match.", "error")
        elif problem:
            flash(problem, "error")
        else:
            execute("UPDATE users SET password_hash=%s, must_change_password=0 WHERE id=%s",
                    (generate_password_hash(new), row["user_id"]))
            execute("UPDATE password_resets SET used_at=NOW() WHERE user_id=%s AND used_at IS NULL",
                    (row["user_id"],))
            drop_user_sessions(row["user_id"])
            execute("DELETE FROM login_attempts WHERE identifier IN (%s,%s)", (row["username"], row["email"]))
            audit("password.reset", "via email link", user_id=row["user_id"])
            flash("Password updated. Log in with your new password.", "success")
            return redirect(url_for("login"))
    return render_template("reset.html", invalid=False)


# ---------- uploaded photos (only people allowed to see a report may see its photos) ----------
@app.route("/uploads/<name>")
@login_required()
def uploaded(name):
    if not re.fullmatch(r"[0-9a-f]{32}\.(jpg|png|gif|webp)", name):
        abort(404)
    u, allowed = g.user, False
    report = query("SELECT m.resident_id, m.assigned_to FROM report_photos p "
                   "JOIN maintenance_requests m ON m.id=p.request_id WHERE p.file=%s", (name,), one=True)
    if report:
        allowed = (u["role"] == "subadmin"
                   or (u["role"] == "resident" and report["resident_id"] == u["id"])
                   or (u["role"] == "maintenance" and report["assigned_to"] == u["id"]))
    elif u["role"] in ("resident", "security", "subadmin"):
        allowed = bool(query("SELECT id FROM lost_found WHERE photo=%s AND status<>'removed'", (name,), one=True))
    if not allowed:
        abort(404)
    mime = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}[name.rsplit(".", 1)[1]]
    return send_from_directory(UPLOAD_DIR, name, mimetype=mime, max_age=0)


# ---------- each role's pages ----------
import features, maintenance, resident, security_desk, subadmin, sysadmin   # noqa: E402

for module in (sysadmin, subadmin, resident, maintenance, security_desk, features):
    module.register(app)


if __name__ == "__main__":
    # Debug mode (auto-reload + interactive debugger) is OFF unless you set FLASK_DEBUG=1
    app.run(host="127.0.0.1", port=5000, debug=os.environ.get("FLASK_DEBUG") == "1")
