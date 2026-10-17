"""Subdivision administrator: reports, assignments, announcements, lost & found, directory, dues, summary."""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import abort, flash, g, redirect, render_template, request, url_for

from common import (APP_NAME, LIST_LIMIT, PAY_METHODS, PRIORITIES, PRIORITY_ORDER, REPORT_STATUSES, CURRENCY,
                    arg, audit, date_arg, execute, execute_count, get_setting, history_for, like, log_event,
                    login_required, notify, notify_role, parse_date, photos_for, query, safe_back)


def get_pdf_builder():
    """PDF export needs the optional 'reportlab' package."""
    try:
        from pdf import build_pdf
        return build_pdf
    except ImportError:
        return None


def site_name():
    return get_setting("site_name", APP_NAME)


def day(value):
    return value.strftime("%b %d, %Y") if value else ""


def register(app):
    # ---------- dashboard ----------
    @app.route("/subdivision")
    @login_required("subadmin")
    def sub_dashboard():
        def count(sql):
            return query(sql, one=True)["n"]

        stats = dict(
            total=count("SELECT COUNT(*) AS n FROM maintenance_requests"),
            unassigned=count("SELECT COUNT(*) AS n FROM maintenance_requests WHERE assigned_to IS NULL"),
            in_progress=count("SELECT COUNT(*) AS n FROM maintenance_requests WHERE status='in_progress'"),
            to_verify=count("SELECT COUNT(*) AS n FROM maintenance_requests WHERE status='completed' AND verified=0"),
            overdue=count("SELECT COUNT(*) AS n FROM maintenance_requests WHERE due_date < CURDATE() AND status<>'completed'"),
            reopened=count("SELECT COUNT(*) AS n FROM maintenance_requests WHERE resident_response='reopened' AND status<>'completed'"),
            announcements=count("SELECT COUNT(*) AS n FROM announcements WHERE published=1"),
            lost_open=count("SELECT COUNT(*) AS n FROM lost_found WHERE status IN ('open','claimed')"),
        )
        base = ("SELECT m.id, m.category, m.priority, m.location, m.created_at, m.due_date, "
                "u.full_name AS resident, a.full_name AS assignee "
                "FROM maintenance_requests m JOIN users u ON u.id=m.resident_id "
                "LEFT JOIN users a ON a.id=m.assigned_to ")
        unassigned = query(base + f"WHERE m.assigned_to IS NULL ORDER BY {PRIORITY_ORDER}, m.created_at LIMIT 6")
        to_verify = query(base + "WHERE m.status='completed' AND m.verified=0 ORDER BY m.completed_at LIMIT 6")
        by_category = query("SELECT category, COUNT(*) AS n, SUM(status='completed') AS done "
                            "FROM maintenance_requests GROUP BY category ORDER BY n DESC LIMIT 8")
        return render_template("subdivision/dashboard.html", stats=stats, unassigned=unassigned,
                               to_verify=to_verify, by_category=by_category)

    # ---------- reports ----------
    def report_filters():
        return dict(q=arg("q"), status=arg("status", REPORT_STATUSES), priority=arg("priority", PRIORITIES),
                    category=arg("category"),
                    view=arg("view", ("unassigned", "to_verify", "overdue", "reopened")),
                    d1=date_arg("from"), d2=date_arg("to"))

    def report_rows(f):
        sql = ("SELECT m.*, u.full_name AS resident, u.address, a.full_name AS assignee, "
               "(m.due_date < CURDATE() AND m.status<>'completed') AS overdue "
               "FROM maintenance_requests m JOIN users u ON u.id=m.resident_id "
               "LEFT JOIN users a ON a.id=m.assigned_to WHERE 1=1")
        args = []
        if f["q"]:
            sql += (" AND (m.description LIKE %s OR m.category LIKE %s OR m.location LIKE %s OR "
                    "u.full_name LIKE %s OR u.address LIKE %s OR CAST(m.id AS CHAR)=%s)")
            args += [like(f["q"])] * 5 + [f["q"]]
        if f["status"]:
            sql += " AND m.status=%s"; args.append(f["status"])
        if f["priority"]:
            sql += " AND m.priority=%s"; args.append(f["priority"])
        if f["category"]:
            sql += " AND m.category=%s"; args.append(f["category"])
        if f["d1"]:
            sql += " AND m.created_at >= %s"; args.append(f["d1"])
        if f["d2"]:
            sql += " AND m.created_at < %s + INTERVAL 1 DAY"; args.append(f["d2"])
        sql += {"unassigned": " AND m.assigned_to IS NULL",
                "to_verify": " AND m.status='completed' AND m.verified=0",
                "overdue": " AND m.due_date < CURDATE() AND m.status<>'completed'",
                "reopened": " AND m.resident_response='reopened' AND m.status<>'completed'"}.get(f["view"], "")
        sql += f" ORDER BY {PRIORITY_ORDER}, m.due_date IS NULL, m.due_date, m.created_at DESC LIMIT {LIST_LIMIT}"
        return query(sql, args)

    @app.route("/subdivision/reports")
    @login_required("subadmin")
    def sub_reports():
        f = report_filters()
        rows = report_rows(f)
        if arg("format") == "pdf":
            build_pdf = get_pdf_builder()
            if build_pdf is None:
                flash("PDF download needs the reportlab package. Run: py -m pip install reportlab", "error")
                return redirect(request.path)
            audit("export.pdf", "reports list")
            data = [[r["id"], day(r["created_at"]), r["resident"], r["category"], r["priority"],
                     r["status"].replace("_", " "), r["assignee"] or "Unassigned", day(r["due_date"])] for r in rows]
            return build_pdf("Resident reports", f"{len(rows)} report(s) matching the current filters",
                             [dict(heading="Reports", headers=["#", "Filed", "Resident", "Category", "Priority",
                                                               "Status", "Assigned to", "Due"],
                                   rows=data, widths=[.05, .13, .18, .16, .09, .11, .17, .11])],
                             "reports.pdf", site_name(), wide=True)
        staff = query("SELECT id, full_name FROM users WHERE role='maintenance' AND status='active' ORDER BY full_name")
        cats = [(r["category"], r["category"]) for r in query("SELECT DISTINCT category FROM maintenance_requests ORDER BY category")]
        return render_template("subdivision/reports.html", rows=rows, staff=staff, cats=cats, f=f,
                               priorities=PRIORITIES, statuses=REPORT_STATUSES)

    @app.route("/subdivision/reports/<int:report_id>")
    @login_required("subadmin")
    def sub_report_detail(report_id):
        r = query("SELECT m.*, u.full_name AS resident, u.address, u.phone, a.full_name AS assignee, "
                  "(m.due_date < CURDATE() AND m.status<>'completed') AS overdue "
                  "FROM maintenance_requests m JOIN users u ON u.id=m.resident_id "
                  "LEFT JOIN users a ON a.id=m.assigned_to WHERE m.id=%s", (report_id,), one=True)
        if not r:
            abort(404)
        staff = query("SELECT id, full_name FROM users WHERE role='maintenance' AND status='active' ORDER BY full_name")
        return render_template("subdivision/report_detail.html", r=r, photos=photos_for(report_id),
                               history=history_for(report_id), staff=staff, priorities=PRIORITIES)

    @app.route("/subdivision/reports/<int:report_id>/assign", methods=["POST"])
    @login_required("subadmin")
    def sub_assign(report_id):
        old = query("SELECT * FROM maintenance_requests WHERE id=%s", (report_id,), one=True)
        if not old:
            abort(404)
        priority = request.form.get("priority", "normal")
        category = request.form.get("category", "").strip()[:60] or "Uncategorized"
        assignee = request.form.get("assigned_to", "")
        raw_due = request.form.get("due_date", "").strip()
        due = parse_date(raw_due)
        if priority not in PRIORITIES:
            abort(400, "Unknown priority.")
        if raw_due and not due:
            abort(400, "That due date isn't valid.")
        assignee_id, assignee_name = None, None
        if assignee:
            person = query("SELECT id, full_name FROM users WHERE id=%s AND role='maintenance' AND status='active'",
                           (assignee,), one=True)
            if not person:
                abort(400, "That person is not an active maintenance staff member.")
            assignee_id, assignee_name = person["id"], person["full_name"]

        execute("UPDATE maintenance_requests SET assigned_to=%s, priority=%s, category=%s, due_date=%s WHERE id=%s",
                (assignee_id, priority, category, due, report_id))
        link_staff, link_res = f"/maintenance/tasks/{report_id}", f"/resident/reports/{report_id}"
        if assignee_id != old["assigned_to"]:
            if assignee_id:
                execute("UPDATE maintenance_requests SET assigned_at=NOW() WHERE id=%s", (report_id,))
                log_event(report_id, f"Assigned to {assignee_name}")
                notify(assignee_id, f"New task #{report_id} ({category}), priority {priority}"
                                    + (f", due {due}" if due else ""), link_staff)
                notify(old["resident_id"], f"Your report #{report_id} was assigned to our maintenance team.", link_res)
            else:
                log_event(report_id, "Unassigned")
        elif assignee_id and (due != (old["due_date"].isoformat() if old["due_date"] else None)):
            notify(assignee_id, f"Task #{report_id}: due date is now {due or 'not set'}", link_staff)
        if priority != old["priority"]:
            log_event(report_id, f"Priority set to {priority}")
        if due != (old["due_date"].isoformat() if old["due_date"] else None):
            log_event(report_id, f"Due date {'set to ' + due if due else 'cleared'}")
        audit("report.assign", f"#{report_id} to {assignee_name or 'nobody'}, {priority}, due {due or '-'}")
        flash(f"Report #{report_id} updated.", "success")
        return redirect(safe_back("sub_reports"))

    @app.route("/subdivision/reports/<int:report_id>/verify", methods=["POST"])
    @login_required("subadmin")
    def sub_verify(report_id):
        r = query("SELECT resident_id FROM maintenance_requests WHERE id=%s AND status='completed'",
                  (report_id,), one=True)
        if r:
            execute("UPDATE maintenance_requests SET verified=1 WHERE id=%s", (report_id,))
            log_event(report_id, "Repair verified by the subdivision office")
            notify(r["resident_id"], f"Report #{report_id}: the repair was verified by the subdivision office.",
                   f"/resident/reports/{report_id}")
            audit("report.verify", f"#{report_id}")
            flash(f"Report #{report_id} marked as verified.", "success")
        return redirect(safe_back("sub_reports"))

    # ---------- announcements ----------
    def announcement_fields():
        kind = request.form.get("kind", "announcement")
        title = request.form.get("title", "").strip()[:160]
        body = request.form.get("body", "").strip()
        published = 1 if request.form.get("published") else 0
        pinned = 1 if request.form.get("pinned") else 0
        event_date = parse_date(request.form.get("event_date", ""))
        if kind not in ("announcement", "activity") or not title or not body:
            return None
        return kind, title, body, event_date, published, pinned

    def tell_residents(kind, title):
        notify_role("resident", f"New {'activity' if kind == 'activity' else 'announcement'}: {title}",
                    "/resident/announcements")

    @app.route("/subdivision/announcements")
    @login_required("subadmin")
    def sub_announcements():
        search, kind = arg("q"), arg("kind", ("announcement", "activity"))
        state = arg("state", ("published", "draft", "pinned"))
        sql, args = ("SELECT a.*, u.full_name AS author FROM announcements a "
                     "JOIN users u ON u.id=a.author_id WHERE 1=1"), []
        if search:
            sql += " AND (a.title LIKE %s OR a.body LIKE %s)"; args += [like(search)] * 2
        if kind:
            sql += " AND a.kind=%s"; args.append(kind)
        sql += {"published": " AND a.published=1", "draft": " AND a.published=0",
                "pinned": " AND a.pinned=1"}.get(state, "")
        sql += f" ORDER BY a.pinned DESC, a.created_at DESC LIMIT {LIST_LIMIT}"
        return render_template("subdivision/announcements.html", rows=query(sql, args), q=search, kind=kind, state=state)

    @app.route("/subdivision/announcements/new", methods=["POST"])
    @login_required("subadmin")
    def sub_announcement_new():
        fields = announcement_fields()
        if not fields:
            flash("Add a title and some text.", "error")
        else:
            aid = execute("INSERT INTO announcements (author_id, kind, title, body, event_date, published, pinned) "
                          "VALUES (%s,%s,%s,%s,%s,%s,%s)", (g.user["id"], *fields))
            audit("announcement.create", f"#{aid} {fields[1]}")
            if fields[4]:
                tell_residents(fields[0], fields[1])
            flash("Published." if fields[4] else "Saved as a draft.", "success")
        return redirect(url_for("sub_announcements"))

    @app.route("/subdivision/announcements/<int:ann_id>/edit", methods=["GET", "POST"])
    @login_required("subadmin")
    def sub_announcement_edit(ann_id):
        item = query("SELECT * FROM announcements WHERE id=%s", (ann_id,), one=True)
        if not item:
            abort(404)
        if request.method == "POST":
            fields = announcement_fields()
            if not fields:
                flash("Add a title and some text.", "error")
            else:
                execute("UPDATE announcements SET kind=%s, title=%s, body=%s, event_date=%s, published=%s, "
                        "pinned=%s WHERE id=%s", (*fields, ann_id))
                audit("announcement.edit", f"#{ann_id} {fields[1]}")
                if fields[4] and not item["published"]:
                    tell_residents(fields[0], fields[1])
                flash("Changes saved.", "success")
                return redirect(url_for("sub_announcements"))
        return render_template("subdivision/announcement_edit.html", a=item)

    @app.route("/subdivision/announcements/<int:ann_id>/toggle", methods=["POST"])
    @login_required("subadmin")
    def sub_announcement_toggle(ann_id):
        item = query("SELECT kind, title, published FROM announcements WHERE id=%s", (ann_id,), one=True)
        if not item:
            abort(404)
        execute("UPDATE announcements SET published = 1 - published WHERE id=%s", (ann_id,))
        audit("announcement.unpublish" if item["published"] else "announcement.publish", f"#{ann_id} {item['title']}")
        if not item["published"]:
            tell_residents(item["kind"], item["title"])
        return redirect(safe_back("sub_announcements"))

    @app.route("/subdivision/announcements/<int:ann_id>/pin", methods=["POST"])
    @login_required("subadmin")
    def sub_announcement_pin(ann_id):
        item = query("SELECT title, pinned FROM announcements WHERE id=%s", (ann_id,), one=True)
        if not item:
            abort(404)
        execute("UPDATE announcements SET pinned = 1 - pinned WHERE id=%s", (ann_id,))
        audit("announcement.unpin" if item["pinned"] else "announcement.pin", f"#{ann_id} {item['title']}")
        flash("Unpinned." if item["pinned"] else "Pinned to the top for residents.", "success")
        return redirect(safe_back("sub_announcements"))

    @app.route("/subdivision/announcements/<int:ann_id>/delete", methods=["POST"])
    @login_required("subadmin")
    def sub_announcement_delete(ann_id):
        item = query("SELECT title FROM announcements WHERE id=%s", (ann_id,), one=True)
        if item:
            execute("DELETE FROM announcements WHERE id=%s", (ann_id,))
            audit("announcement.delete", f"#{ann_id} {item['title']}")
            flash("Removed.", "success")
        return redirect(url_for("sub_announcements"))

    # ---------- lost & found moderation ----------
    @app.route("/subdivision/lost-found")
    @login_required("subadmin")
    def sub_lostfound():
        search, kind = arg("q"), arg("kind", ("lost", "found"))
        status = arg("status", ("open", "claimed", "released", "closed", "removed"))
        sql, args = ("SELECT l.*, r.full_name AS reporter, h.full_name AS handler FROM lost_found l "
                     "JOIN users r ON r.id=l.reported_by LEFT JOIN users h ON h.id=l.handled_by WHERE 1=1"), []
        if search:
            sql += " AND (l.item_name LIKE %s OR l.description LIKE %s OR l.location LIKE %s)"; args += [like(search)] * 3
        if kind:
            sql += " AND l.kind=%s"; args.append(kind)
        if status:
            sql += " AND l.status=%s"; args.append(status)
        sql += f" ORDER BY l.created_at DESC LIMIT {LIST_LIMIT}"
        return render_template("subdivision/lostfound.html", rows=query(sql, args), q=search, kind=kind, status=status)

    @app.route("/subdivision/lost-found/<int:item_id>/moderate", methods=["POST"])
    @login_required("subadmin")
    def sub_lostfound_toggle(item_id):
        item = query("SELECT item_name FROM lost_found WHERE id=%s", (item_id,), one=True)
        if not item:
            abort(404)
        if request.form.get("action") == "remove":
            execute("UPDATE lost_found SET status='removed' WHERE id=%s AND status<>'released'", (item_id,))
            audit("lostfound.remove", f"#{item_id} {item['item_name']}")
            flash("Post removed from the lost & found list.", "success")
        else:
            execute("UPDATE lost_found SET status='open' WHERE id=%s AND status='removed'", (item_id,))
            audit("lostfound.restore", f"#{item_id} {item['item_name']}")
            flash("Post restored.", "success")
        return redirect(safe_back("sub_lostfound"))

    # ---------- summary report (+ PDF) ----------
    def summary_data():
        today = date.today()
        d1 = date_arg("from") or today.replace(day=1).isoformat()
        d2 = date_arg("to") or today.isoformat()
        rng = lambda col: f"{col} >= %s AND {col} < %s + INTERVAL 1 DAY"
        span = (d1, d2)
        one = lambda sql, a=span: query(sql, a, one=True)
        s = dict(d1=d1, d2=d2)
        s["total"] = one(f"SELECT COUNT(*) AS n FROM maintenance_requests WHERE {rng('created_at')}")["n"]
        s["by_status"] = query(f"SELECT status, COUNT(*) AS n FROM maintenance_requests WHERE {rng('created_at')} "
                               "GROUP BY status", span)
        s["by_priority"] = query(f"SELECT priority, COUNT(*) AS n FROM maintenance_requests WHERE {rng('created_at')} "
                                 f"GROUP BY priority ORDER BY FIELD(priority,'urgent','high','normal','low')", span)
        s["by_category"] = query(f"SELECT category, COUNT(*) AS n, SUM(status='completed') AS done "
                                 f"FROM maintenance_requests WHERE {rng('created_at')} GROUP BY category "
                                 "ORDER BY n DESC", span)
        row = one(f"SELECT AVG(TIMESTAMPDIFF(HOUR, created_at, completed_at)) AS h, AVG(rating) AS r, "
                  f"SUM(resident_response='reopened') AS reopened FROM maintenance_requests WHERE {rng('created_at')}")
        s["avg_days"] = round(float(row["h"]) / 24, 1) if row["h"] is not None else None
        s["avg_rating"] = round(float(row["r"]), 1) if row["r"] is not None else None
        s["reopened"] = int(row["reopened"] or 0)
        s["overdue_now"] = query("SELECT COUNT(*) AS n FROM maintenance_requests WHERE due_date < CURDATE() "
                                 "AND status<>'completed'", one=True)["n"]
        s["lf"] = dict(
            found=one(f"SELECT COUNT(*) AS n FROM lost_found WHERE kind='found' AND {rng('created_at')}")["n"],
            lost=one(f"SELECT COUNT(*) AS n FROM lost_found WHERE kind='lost' AND {rng('created_at')}")["n"],
            claims=one(f"SELECT COUNT(*) AS n FROM lost_found WHERE {rng('claimed_at')}")["n"],
            released=one(f"SELECT COUNT(*) AS n FROM lost_found WHERE {rng('released_at')}")["n"])
        s["rows"] = query(f"SELECT m.id, m.created_at, m.category, m.priority, m.status, u.full_name AS resident, "
                          "a.full_name AS assignee FROM maintenance_requests m JOIN users u ON u.id=m.resident_id "
                          f"LEFT JOIN users a ON a.id=m.assigned_to WHERE {rng('m.created_at')} "
                          f"ORDER BY m.created_at DESC LIMIT {LIST_LIMIT}", span)
        return s

    @app.route("/subdivision/summary")
    @login_required("subadmin")
    def sub_summary():
        s = summary_data()
        if arg("format") == "pdf":
            build_pdf = get_pdf_builder()
            if build_pdf is None:
                flash("PDF download needs the reportlab package. Run: py -m pip install reportlab", "error")
                return redirect(request.path)
            audit("export.pdf", f"summary {s['d1']} to {s['d2']}")
            money = lambda v: f"PHP {float(v):,.2f}"
            na = lambda v, suffix="": "n/a" if v is None else f"{v}{suffix}"
            sections = [
                dict(heading="Overview", headers=["Measure", "Value"], widths=[.6, .4], rows=[
                    ["Reports received", s["total"]],
                    ["Average time to complete", na(s["avg_days"], " days")],
                    ["Average resident rating", na(s["avg_rating"], " / 5")],
                    ["Reports reopened by residents", s["reopened"]],
                    ["Overdue tasks right now", s["overdue_now"]]]),
                dict(heading="Reports by status", headers=["Status", "Reports"], widths=[.6, .4],
                     rows=[[r["status"].replace("_", " "), r["n"]] for r in s["by_status"]]),
                dict(heading="Reports by category", headers=["Category", "Reports", "Completed"], widths=[.5, .25, .25],
                     rows=[[r["category"], r["n"], int(r["done"] or 0)] for r in s["by_category"]]),
                dict(heading="Lost and found", headers=["Measure", "Value"], widths=[.6, .4], rows=[
                    ["Found items recorded", s["lf"]["found"]], ["Lost items reported", s["lf"]["lost"]],
                    ["Claims made", s["lf"]["claims"]], ["Items released", s["lf"]["released"]]]),
                dict(heading="All reports in this period",
                     headers=["#", "Filed", "Resident", "Category", "Priority", "Status", "Assigned to"],
                     widths=[.06, .14, .2, .17, .1, .13, .2],
                     rows=[[r["id"], day(r["created_at"]), r["resident"], r["category"], r["priority"],
                            r["status"].replace("_", " "), r["assignee"] or "Unassigned"] for r in s["rows"]]),
            ]
            return build_pdf("Subdivision summary report", f"Period: {s['d1']} to {s['d2']}", sections,
                             f"summary-{s['d1']}-to-{s['d2']}.pdf", site_name())
        return render_template("subdivision/summary.html", s=s)

