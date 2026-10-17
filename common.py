"""Helpers shared by every part of the portal (database, login checks, uploads, notifications)."""
import io
import os
import re
import secrets
from datetime import datetime
from functools import wraps
from urllib.parse import urlparse

import pymysql
import pymysql.cursors
from flask import abort, flash, g, redirect, request, session, url_for
from werkzeug.security import generate_password_hash

try:                                   # Pillow is optional: it shrinks photos and strips hidden data (like GPS)
    from PIL import Image, ImageOps
    HAVE_PIL = True
except ImportError:
    Image = ImageOps = None
    HAVE_PIL = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# XAMPP defaults: user "root", empty password. Override with environment variables if you changed them.
DB_CONFIG = dict(
    host=os.environ.get("DB_HOST", "localhost"),
    user=os.environ.get("DB_USER", "root"),
    password=os.environ.get("DB_PASSWORD", ""),
    database=os.environ.get("DB_NAME", "subdivision_db"),
    charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=True,
)

APP_NAME = "Subdivision Portal"        # fallback; the live name is a setting in the database
CURRENCY = "₱"

IDLE_TIMEOUT = 30 * 60                 # seconds of inactivity before auto logout
ABSOLUTE_TIMEOUT = 8 * 3600            # longest a single login can last
LIST_LIMIT = 500                       # most rows any list page shows

ROLE_LABELS = {
    "sysadmin": "System administrator",
    "subadmin": "Subdivision administrator",
    "security": "Security personnel",
    "maintenance": "Maintenance personnel",
    "resident": "Resident",
}
ROLE_HOME = {
    "sysadmin": "sys_dashboard", "subadmin": "sub_dashboard", "security": "security_home",
    "maintenance": "maintenance_home", "resident": "resident_home",
}
PRIORITIES = ("low", "normal", "high", "urgent")
PRIORITY_ORDER = "FIELD(m.priority,'urgent','high','normal','low')"
REPORT_STATUSES = ("pending", "in_progress", "completed")
CATEGORIES = ["Plumbing", "Electrical", "Roads and drainage", "Streetlights", "Garbage collection",
              "Trees and landscaping", "Common areas", "Other"]
ID_TYPES = ["Driver's license", "Passport", "National ID / PhilID", "Voter's ID", "Company or school ID",
            "Subdivision ID", "Other government ID"]
PAY_METHODS = ["Cash", "GCash", "Bank transfer", "Check", "Other"]


# ---------- database ----------
def get_conn():
    return pymysql.connect(**DB_CONFIG)


def db():
    if "db" not in g:
        g.db = get_conn()
    return g.db


def query(sql, args=(), one=False):
    with db().cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchone() if one else cur.fetchall()


def execute(sql, args=()):
    with db().cursor() as cur:
        cur.execute(sql, args)
        return cur.lastrowid


def execute_count(sql, args=()):
    """Like execute(), but returns how many rows were changed."""
    with db().cursor() as cur:
        cur.execute(sql, args)
        return cur.rowcount


def get_setting(key, default=""):
    row = query("SELECT value FROM settings WHERE key_name=%s", (key,), one=True)
    return row["value"] if row else default


# ---------- request helpers ----------
def client_ip():
    return request.remote_addr or "0.0.0.0"


def like(text):
    """Wrap text for a LIKE search, with %, _ and \\ treated as ordinary characters."""
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def arg(name, allowed=None, default=""):
    value = request.args.get(name, "").strip()
    if allowed is not None and value not in allowed:
        return default
    return value[:120]


def parse_date(value):
    """'2026-10-31' -> '2026-10-31', anything else -> None."""
    value = (value or "").strip()
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return value
    except ValueError:
        return None


def date_arg(name):
    return parse_date(request.args.get(name, "")) or ""


def safe_back(fallback_endpoint):
    """Where to go after a form: the page we came from, but only if it's on this site."""
    ref = request.referrer
    if ref:
        parts = urlparse(ref)
        if parts.netloc == request.host:
            return parts.path + (("?" + parts.query) if parts.query else "")
    return url_for(fallback_endpoint)


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def login_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not g.get("user"):
                if not g.get("expired"):
                    flash("Please log in to continue.", "info")
                return redirect(url_for("login"))
            if roles and g.user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


# ---------- passwords ----------
COMMON_PASSWORDS = {
    "password", "password1", "password12", "password123", "12345678", "123456789", "1234567890",
    "qwerty123", "qwertyuiop", "admin12345", "admin123456", "letmein123", "iloveyou1", "welcome123",
}
DUMMY_HASH = generate_password_hash("not-a-real-password")   # keeps login timing equal for unknown users


def password_problem(pw, *personal):
    """Return a message if the password is too weak, otherwise None."""
    if len(pw) < 10:
        return "Password must be at least 10 characters."
    if len(pw) > 128:
        return "Password is too long (128 characters at most)."
    if not (any(c.isalpha() for c in pw) and any(c.isdigit() for c in pw)):
        return "Password must contain both letters and numbers."
    low = pw.lower()
    if low in COMMON_PASSWORDS:
        return "That password is too common. Choose something less guessable."
    for bit in personal:
        if bit and len(bit) >= 3 and bit.lower() in low:
            return "Password can't contain your username or email name."
    return None


def drop_user_sessions(user_id, keep_token=None):
    if keep_token:
        execute("DELETE FROM user_sessions WHERE user_id=%s AND token<>%s", (user_id, keep_token))
    else:
        execute("DELETE FROM user_sessions WHERE user_id=%s", (user_id,))


# ---------- logs and notifications ----------
def audit(action, detail="", user_id=None):
    """Write one line to the activity log (who did what, and when)."""
    uid = user_id if user_id is not None else (g.user["id"] if g.get("user") else None)
    execute("INSERT INTO audit_log (user_id, action, detail, ip) VALUES (%s,%s,%s,%s)",
            (uid, action[:60], (detail or "")[:255], client_ip()))


def record_login(user_id, ident, success, reason):
    execute("INSERT INTO login_history (user_id, identifier, success, reason, ip, user_agent) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (user_id, ident[:120], 1 if success else 0, reason, client_ip(),
             request.headers.get("User-Agent", "")[:255]))


def notify(user_id, message, link=None):
    if user_id:
        execute("INSERT INTO notifications (user_id, message, link) VALUES (%s,%s,%s)",
                (user_id, message[:255], link))


def notify_role(role, message, link=None):
    execute("INSERT INTO notifications (user_id, message, link) "
            "SELECT id, %s, %s FROM users WHERE role=%s AND status='active'", (message[:255], link, role))


def log_event(request_id, text, actor_id=None):
    """Add a line to a report's timeline (what residents and staff see under 'History')."""
    actor = actor_id if actor_id is not None else (g.user["id"] if g.get("user") else None)
    execute("INSERT INTO report_history (request_id, actor_id, text) VALUES (%s,%s,%s)",
            (request_id, actor, text[:500]))


# ---------- photo uploads ----------
if HAVE_PIL:
    Image.MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_BYTES = 8 * 1024 * 1024


SIGNATURES = ((b"\xff\xd8\xff", ".jpg"), (b"\x89PNG\r\n\x1a\n", ".png"), (b"GIF87a", ".gif"), (b"GIF89a", ".gif"))


def sniff_ext(data):
    """Work out the real picture type from the first bytes of the file (not from its name)."""
    for sig, ext in SIGNATURES:
        if data.startswith(sig):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def save_image(storage):
    """Check that an upload really is a picture, strip hidden data (like GPS), shrink it, and save it
    under a random name. Returns the file name, or None if nothing was uploaded."""
    data = storage.read(MAX_IMAGE_BYTES + 1)
    if not data:
        return None
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Each photo must be 8 MB or smaller.")
    if not HAVE_PIL:   # basic check only: the file must really start like a JPG, PNG, GIF or WEBP
        ext = sniff_ext(data)
        if not ext:
            raise ValueError("Only JPG, PNG, GIF or WEBP photos are allowed.")
        name = secrets.token_hex(16) + ext
        with open(os.path.join(UPLOAD_DIR, name), "wb") as f:
            f.write(data)
        return name
    try:
        Image.open(io.BytesIO(data)).verify()
        img = Image.open(io.BytesIO(data))
        fmt = img.format
        img.load()
    except Exception:
        raise ValueError("One of the files isn't a valid image.")
    if fmt not in ("JPEG", "PNG", "GIF", "WEBP"):
        raise ValueError("Only JPG, PNG, GIF or WEBP photos are allowed.")
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        flat = Image.new("RGB", img.size, "white")
        flat.paste(img, mask=img.split()[-1])
        img = flat
    else:
        img = img.convert("RGB")
    img.thumbnail((1600, 1600))
    name = secrets.token_hex(16) + ".jpg"
    img.save(os.path.join(UPLOAD_DIR, name), "JPEG", quality=85, optimize=True)
    return name


def save_images(files, limit=4):
    files = [f for f in files if f and f.filename]
    if len(files) > limit:
        raise ValueError(f"You can attach up to {limit} photos.")
    saved = []
    try:
        for f in files:
            name = save_image(f)
            if name:
                saved.append(name)
    except ValueError:
        for name in saved:
            try:
                os.remove(os.path.join(UPLOAD_DIR, name))
            except OSError:
                pass
        raise
    return saved


def photos_for(request_id):
    return query("SELECT file, kind FROM report_photos WHERE request_id=%s ORDER BY id", (request_id,))


def history_for(request_id):
    return query("SELECT h.text, h.created_at, u.full_name AS actor FROM report_history h "
                 "LEFT JOIN users u ON u.id=h.actor_id WHERE h.request_id=%s ORDER BY h.id", (request_id,))
