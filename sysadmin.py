"""System administrator: accounts, roles, passwords, settings, error log, audit log, sessions, backup."""
import hashlib
import secrets
from datetime import datetime

from flask import (Response, abort, flash, g, redirect, render_template, request, session,
                   stream_with_context, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from common import (APP_NAME, IDLE_TIMEOUT, LIST_LIMIT, ROLE_LABELS, arg, audit, date_arg, drop_user_sessions,
                    execute, get_conn, get_setting, like, login_required, notify, password_problem, query,
                    safe_back)


def register(app):
    # ---------- dashboard ----------
    @app.route("/system")
    @login_required("sysadmin")
    def sys_dashboard():
        counts = {r["role"]: r["n"] for r in query(
            "SELECT role, COUNT(*) AS n FROM users WHERE status='active' GROUP BY role")}
        stats = dict(
            counts=counts, total=sum(counts.values()),
            pending=query("SELECT COUNT(*) AS n FROM users WHERE status='pending'", one=True)["n"],
            inactive=query("SELECT COUNT(*) AS n FROM users WHERE status='inactive'", one=True)["n"],
            errors=query("SELECT COUNT(*) AS n FROM system_errors "
                         "WHERE created_at >= NOW() - INTERVAL 7 DAY", one=True)["n"],
            online=query("SELECT COUNT(DISTINCT user_id) AS n FROM user_sessions "
                         "WHERE last_seen >= NOW() - INTERVAL %s SECOND", (IDLE_TIMEOUT,), one=True)["n"],
            failed_logins=query("SELECT COUNT(*) AS n FROM login_history WHERE success=0 "
                                "AND created_at >= NOW() - INTERVAL 1 DAY", one=True)["n"],
        )
        pending = query("SELECT id, full_name, address, email, created_at FROM users "
                        "WHERE status='pending' ORDER BY created_at LIMIT 8")
        errors = query("SELECT path, message, created_at FROM system_errors ORDER BY created_at DESC LIMIT 5")
        activity = query("SELECT a.action, a.detail, a.created_at, u.full_name FROM audit_log a "
                         "LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 6")
        return render_template("system/dashboard.html", stats=stats, pending=pending, errors=errors,
                               activity=activity)

    # ---------- accounts ----------
    @app.route("/system/accounts")
    @login_required("sysadmin")
    def sys_accounts():
        role = arg("role", ROLE_LABELS)
        status = arg("status", ("pending", "active", "inactive"))
        search = arg("q")
        sql, args = "SELECT * FROM users WHERE 1=1", []
        if role:
            sql += " AND role=%s"; args.append(role)
        if status:
            sql += " AND status=%s"; args.append(status)
        if search:
            sql += " AND (full_name LIKE %s OR username LIKE %s OR email LIKE %s OR address LIKE %s)"
            args += [like(search)] * 4
        sql += f" ORDER BY FIELD(status,'pending','active','inactive'), created_at DESC LIMIT {LIST_LIMIT}"
        return render_template("system/accounts.html", users=query(sql, args), role=role, status=status, q=search)

    @app.route("/system/accounts/new", methods=["POST"])
    @login_required("sysadmin")
    def sys_create_user():
        f = {k: request.form.get(k, "").strip() for k in
             ("full_name", "username", "email", "phone", "address", "role")}
        f["username"], f["email"] = f["username"].lower(), f["email"].lower()
        password = request.form.get("password", "")
        problem = password_problem(password, f["username"], f["email"].split("@")[0])

        if f["role"] not in ROLE_LABELS or not all([f["full_name"], f["username"], f["email"]]):
            flash("Fill in name, username, email, and role.", "error")
        elif problem:
            flash(problem, "error")
        elif query("SELECT id FROM users WHERE username=%s OR email=%s", (f["username"], f["email"]), one=True):
            flash("That username or email is already registered.", "error")
        else:
            execute(
                "INSERT INTO users (full_name, username, email, phone, address, password_hash, role, status, "
                "must_change_password) VALUES (%s,%s,%s,%s,%s,%s,%s,'active',1)",
                (f["full_name"], f["username"], f["email"], f["phone"], f["address"],
                 generate_password_hash(password), f["role"]))
            audit("account.create", f"{f['username']} as {ROLE_LABELS[f['role']]}")
            flash(f"Account created for {f['full_name']}.", "success")
        return redirect(url_for("sys_accounts"))

    @app.route("/system/accounts/<int:user_id>/status", methods=["POST"])
    @login_required("sysadmin")
    def sys_set_status(user_id):
        action = request.form.get("action")
        new_status = {"approve": "active", "activate": "active", "deactivate": "inactive"}.get(action)
        if not new_status:
            abort(400, "Unknown action.")
        target = query("SELECT username FROM users WHERE id=%s", (user_id,), one=True)
        if not target:
            abort(404)
        if user_id == g.user["id"]:
            flash("You can't change your own account status.", "error")
        else:
            execute("UPDATE users SET status=%s WHERE id=%s", (new_status, user_id))
            if new_status == "inactive":
                drop_user_sessions(user_id)   # kick them out immediately
            audit(f"account.{action}", target["username"])
            flash({"approve": "Account approved.", "activate": "Account reactivated.",
                   "deactivate": "Account deactivated."}[action], "success")
        return redirect(safe_back("sys_accounts"))

    @app.route("/system/accounts/<int:user_id>/reset", methods=["POST"])
    @login_required("sysadmin")
    def sys_reset_password(user_id):
        user = query("SELECT full_name, username FROM users WHERE id=%s", (user_id,), one=True)
        if not user:
            abort(404)
        temp = secrets.token_urlsafe(8)
        execute("UPDATE users SET password_hash=%s, must_change_password=1 WHERE id=%s",
                (generate_password_hash(temp), user_id))
        drop_user_sessions(user_id)
        audit("account.reset_password", user["username"])
        flash(f"Temporary password for {user['full_name']}: {temp}  (shown once, share it privately. "
              "They must choose a new one at next login.)", "success")
        return redirect(url_for("sys_accounts"))

    # ---------- settings ----------
    @app.route("/system/settings", methods=["GET", "POST"])
    @login_required("sysadmin")
    def sys_settings():
        if request.method == "POST":
            site_name = request.form.get("site_name", "").strip()[:60]
            allow = "1" if request.form.get("allow_registration") else "0"
            if not site_name:
                flash("The system name can't be empty.", "error")
            else:
                for key, value in (("site_name", site_name), ("allow_registration", allow)):
                    execute("REPLACE INTO settings (key_name, value) VALUES (%s,%s)", (key, value))
                audit("settings.update", f"name={site_name}, sign-up {'open' if allow == '1' else 'closed'}")
                flash("Settings saved.", "success")
            return redirect(url_for("sys_settings"))
        return render_template("system/settings.html", site_name=get_setting("site_name", APP_NAME),
                               allow_registration=get_setting("allow_registration", "1") == "1")

    # ---------- error log ----------
    @app.route("/system/errors")
    @login_required("sysadmin")
    def sys_errors():
        search, d1, d2 = arg("q"), date_arg("from"), date_arg("to")
        sql, args = ("SELECT e.*, u.full_name FROM system_errors e LEFT JOIN users u ON u.id=e.user_id "
                     "WHERE 1=1"), []
        if search:
            sql += " AND (e.path LIKE %s OR e.message LIKE %s)"; args += [like(search)] * 2
        if d1:
            sql += " AND e.created_at >= %s"; args.append(d1)
        if d2:
            sql += " AND e.created_at < %s + INTERVAL 1 DAY"; args.append(d2)
        sql += f" ORDER BY e.created_at DESC LIMIT {LIST_LIMIT}"
        return render_template("system/errors.html", rows=query(sql, args), q=search, d1=d1, d2=d2)

    # ---------- audit log ----------
    @app.route("/system/audit")
    @login_required("sysadmin")
    def sys_audit():
        search, d1, d2 = arg("q"), date_arg("from"), date_arg("to")
        who = arg("user")
        sql, args = ("SELECT a.*, u.full_name, u.role FROM audit_log a LEFT JOIN users u ON u.id=a.user_id "
                     "WHERE 1=1"), []
        if search:
            sql += " AND (a.action LIKE %s OR a.detail LIKE %s)"; args += [like(search)] * 2
        if who.isdigit():
            sql += " AND a.user_id=%s"; args.append(int(who))
        if d1:
            sql += " AND a.created_at >= %s"; args.append(d1)
        if d2:
            sql += " AND a.created_at < %s + INTERVAL 1 DAY"; args.append(d2)
        sql += f" ORDER BY a.id DESC LIMIT {LIST_LIMIT}"
        people = [(str(p["id"]), p["full_name"]) for p in query("SELECT id, full_name FROM users ORDER BY full_name")]
        return render_template("system/audit.html", rows=query(sql, args), people=people, q=search,
                               who=who, d1=d1, d2=d2)

    # ---------- who is logged in now ----------
    @app.route("/system/sessions")
    @login_required("sysadmin")
    def sys_sessions():
        rows = query(
            "SELECT LEFT(SHA2(s.token,256),16) AS handle, s.created_at, s.last_seen, s.ip, s.user_agent, "
            "u.id AS user_id, u.full_name, u.username, u.role, "
            "TIMESTAMPDIFF(MINUTE, s.last_seen, NOW()) AS idle_min "
            "FROM user_sessions s JOIN users u ON u.id=s.user_id "
            "WHERE s.last_seen >= NOW() - INTERVAL %s SECOND ORDER BY s.last_seen DESC", (IDLE_TIMEOUT,))
        mine = hashlib.sha256(session["token"].encode()).hexdigest()[:16]
        return render_template("system/sessions.html", rows=rows, mine=mine)

    @app.route("/system/sessions/<handle>/end", methods=["POST"])
    @login_required("sysadmin")
    def sys_end_session(handle):
        if len(handle) != 16 or any(c not in "0123456789abcdef" for c in handle):
            abort(404)
        if handle == hashlib.sha256(session["token"].encode()).hexdigest()[:16]:
            flash("That's your own login. Use Log out instead.", "error")
        else:
            row = query("SELECT u.username FROM user_sessions s JOIN users u ON u.id=s.user_id "
                        "WHERE LEFT(SHA2(s.token,256),16)=%s", (handle,), one=True)
            execute("DELETE FROM user_sessions WHERE LEFT(SHA2(token,256),16)=%s", (handle,))
            if row:
                audit("session.force_logout", row["username"])
            flash("That login was ended. They'll be asked to log in again.", "success")
        return redirect(url_for("sys_sessions"))

    @app.route("/system/accounts/<int:user_id>/logout-all", methods=["POST"])
    @login_required("sysadmin")
    def sys_logout_all(user_id):
        user = query("SELECT username FROM users WHERE id=%s", (user_id,), one=True)
        if not user:
            abort(404)
        if user_id == g.user["id"]:
            flash("Use Log out for your own account.", "error")
        else:
            drop_user_sessions(user_id)
            audit("session.force_logout_all", user["username"])
            flash(f"{user['username']} was logged out everywhere.", "success")
        return redirect(url_for("sys_sessions"))

    # ---------- login history ----------
    @app.route("/system/logins")
    @login_required("sysadmin")
    def sys_logins():
        search, d1, d2 = arg("q"), date_arg("from"), date_arg("to")
        who, result = arg("user"), arg("result", ("ok", "failed"))
        sql, args = ("SELECT h.*, u.full_name, u.username FROM login_history h "
                     "LEFT JOIN users u ON u.id=h.user_id WHERE 1=1"), []
        if search:
            sql += " AND (h.identifier LIKE %s OR h.ip LIKE %s)"; args += [like(search)] * 2
        if who.isdigit():
            sql += " AND h.user_id=%s"; args.append(int(who))
        if result:
            sql += " AND h.success=%s"; args.append(1 if result == "ok" else 0)
        if d1:
            sql += " AND h.created_at >= %s"; args.append(d1)
        if d2:
            sql += " AND h.created_at < %s + INTERVAL 1 DAY"; args.append(d2)
        sql += f" ORDER BY h.id DESC LIMIT {LIST_LIMIT}"
        people = [(str(p["id"]), f"{p['full_name']} (@{p['username']})")
                  for p in query("SELECT id, full_name, username FROM users ORDER BY full_name")]
        return render_template("system/logins.html", rows=query(sql, args), people=people, q=search,
                               who=who, result=result, d1=d1, d2=d2)

    # ---------- database backup ----------
    NO_DATA = {"user_sessions", "password_resets", "login_attempts"}   # never put live logins in a backup

    def dump_sql():
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                yield (f"-- {get_setting('site_name', APP_NAME)} backup, {datetime.now():%Y-%m-%d %H:%M}\n"
                       "-- Restore: phpMyAdmin > Import. Uploaded photos live in the 'uploads' folder; copy it too.\n"
                       "SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS=0;\n\n")
                cur.execute("SHOW FULL TABLES WHERE Table_type='BASE TABLE'")
                tables = [list(r.values())[0] for r in cur.fetchall()]
                for table in tables:
                    cur.execute(f"SHOW CREATE TABLE `{table}`")
                    yield f"DROP TABLE IF EXISTS `{table}`;\n{cur.fetchone()['Create Table']};\n\n"
                    if table in NO_DATA:
                        continue
                    cur.execute(f"SELECT * FROM `{table}`")
                    cols = None
                    while True:
                        rows = cur.fetchmany(300)
                        if not rows:
                            break
                        if cols is None:
                            cols = ", ".join(f"`{c}`" for c in rows[0].keys())
                        values = ",\n".join("(" + ",".join(conn.escape(v) for v in r.values()) + ")" for r in rows)
                        yield f"INSERT INTO `{table}` ({cols}) VALUES\n{values};\n"
                    yield "\n"
                yield "SET FOREIGN_KEY_CHECKS=1;\n"
        finally:
            conn.close()

    @app.route("/system/backup", methods=["GET", "POST"])
    @login_required("sysadmin")
    def sys_backup():
        if request.method == "POST":
            row = query("SELECT password_hash FROM users WHERE id=%s", (g.user["id"],), one=True)
            if not check_password_hash(row["password_hash"], request.form.get("password", "")):
                audit("backup.denied", "wrong password")
                flash("Password incorrect. No backup was made.", "error")
                return redirect(url_for("sys_backup"))
            audit("backup.download", "full database")
            name = f"backup-{datetime.now():%Y%m%d-%H%M}.sql"
            return Response(stream_with_context(dump_sql()), mimetype="application/sql",
                            headers={"Content-Disposition": f'attachment; filename="{name}"'})
        last = query("SELECT created_at FROM audit_log WHERE action='backup.download' ORDER BY id DESC LIMIT 1",
                     one=True)
        return render_template("system/backup.html", last=last)
