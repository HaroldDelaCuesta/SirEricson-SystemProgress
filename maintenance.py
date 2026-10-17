"""Maintenance personnel: assigned tasks, status updates with photos, work history."""
from flask import abort, flash, g, redirect, render_template, request, url_for

from common import (LIST_LIMIT, PRIORITIES, PRIORITY_ORDER, REPORT_STATUSES, arg, audit, date_arg, execute,
                    history_for, like, log_event, login_required, notify, notify_role, photos_for, query, save_images)

ORDER = {"pending": 0, "in_progress": 1, "completed": 2}


def register(app):
    def my_task(task_id):
        t = query("SELECT m.*, (m.due_date < CURDATE() AND m.status<>'completed') AS overdue "
                  "FROM maintenance_requests m WHERE m.id=%s AND m.assigned_to=%s", (task_id, g.user["id"]), one=True)
        if not t:
            abort(404)      # not yours: same answer as "doesn't exist"
        return t

    @app.route("/maintenance")
    @login_required("maintenance")
    def maintenance_home():
        me = g.user["id"]
        one = lambda sql: query(sql, (me,), one=True)["n"]
        stats = dict(
            open=one("SELECT COUNT(*) AS n FROM maintenance_requests WHERE assigned_to=%s AND status<>'completed'"),
            overdue=one("SELECT COUNT(*) AS n FROM maintenance_requests WHERE assigned_to=%s AND status<>'completed' AND due_date < CURDATE()"),
            soon=one("SELECT COUNT(*) AS n FROM maintenance_requests WHERE assigned_to=%s AND status<>'completed' "
                     "AND due_date BETWEEN CURDATE() AND CURDATE() + INTERVAL 3 DAY"),
            reopened=one("SELECT COUNT(*) AS n FROM maintenance_requests WHERE assigned_to=%s AND status<>'completed' AND resident_response='reopened'"),
            done_month=one("SELECT COUNT(*) AS n FROM maintenance_requests WHERE assigned_to=%s AND status='completed' "
                           "AND completed_at >= DATE_FORMAT(CURDATE(), '%%Y-%%m-01')"))
        upnext = query(f"SELECT m.id, m.category, m.priority, m.location, m.due_date, m.status, m.resident_response, "
                       f"(m.due_date < CURDATE()) AS overdue FROM maintenance_requests m "
                       f"WHERE m.assigned_to=%s AND m.status<>'completed' "
                       f"ORDER BY {PRIORITY_ORDER}, m.due_date IS NULL, m.due_date, m.created_at LIMIT 5", (me,))
        return render_template("maintenance/home.html", stats=stats, upnext=upnext)

    @app.route("/maintenance/tasks")
    @login_required("maintenance")
    def maintenance_tasks():
        search, priority = arg("q"), arg("priority", PRIORITIES)
        view = arg("status", ("open", "pending", "in_progress", "all"), "open")
        sql = ("SELECT m.*, (m.due_date < CURDATE() AND m.status<>'completed') AS overdue "
               "FROM maintenance_requests m WHERE m.assigned_to=%s AND m.status<>'completed'")
        args = [g.user["id"]]
        if view in ("pending", "in_progress"):
            sql += " AND m.status=%s"; args.append(view)
        if priority:
            sql += " AND m.priority=%s"; args.append(priority)
        if search:
            sql += " AND (m.description LIKE %s OR m.category LIKE %s OR m.location LIKE %s OR CAST(m.id AS CHAR)=%s)"
            args += [like(search)] * 3 + [search]
        # most urgent first, then the nearest due date, then oldest
        sql += f" ORDER BY {PRIORITY_ORDER}, m.due_date IS NULL, m.due_date, m.created_at LIMIT {LIST_LIMIT}"
        return render_template("maintenance/tasks.html", rows=query(sql, args), q=search, priority=priority,
                               view=view, priorities=PRIORITIES)

    @app.route("/maintenance/tasks/<int:task_id>")
    @login_required("maintenance")
    def maintenance_task(task_id):
        t = my_task(task_id)
        photos = photos_for(task_id)
        return render_template("maintenance/task.html", t=t,
                               report_photos=[p for p in photos if p["kind"] == "report"],
                               done_photos=[p for p in photos if p["kind"] == "completion"],
                               history=history_for(task_id), order=ORDER)

    @app.route("/maintenance/tasks/<int:task_id>/update", methods=["POST"])
    @login_required("maintenance")
    def maintenance_task_update(task_id):
        t = my_task(task_id)
        back = redirect(url_for("maintenance_task", task_id=task_id))
        new_status = request.form.get("status", t["status"])
        notes = request.form.get("notes", "").strip()[:2000]
        if new_status not in REPORT_STATUSES:
            abort(400, "Unknown status.")
        if t["status"] == "completed":
            flash("This task is already completed.", "info")
            return back
        if ORDER[new_status] < ORDER[t["status"]]:
            flash("A task can't move backwards. Keep it In Progress and add a note instead.", "error")
            return back
        try:
            files = save_images(request.files.getlist("photos"), limit=4)
        except ValueError as e:
            flash(str(e), "error")
            return back
        have_photos = query("SELECT COUNT(*) AS n FROM report_photos WHERE request_id=%s AND kind='completion'",
                            (task_id,), one=True)["n"]
        needs_new = t["resident_response"] == "reopened"   # a reopened job needs fresh proof
        if new_status == "completed" and not (files or (have_photos and not needs_new)):
            flash("Add at least one photo showing the completed repair before marking it Completed.", "error")
            return back

        for name in files:
            execute("INSERT INTO report_photos (request_id, file, kind, uploaded_by) VALUES (%s,%s,'completion',%s)",
                    (task_id, name, g.user["id"]))
        if files:
            log_event(task_id, f"Maintenance added {len(files)} photo(s)")
        if notes and notes != (t["work_notes"] or ""):
            execute("UPDATE maintenance_requests SET work_notes=%s WHERE id=%s", (notes, task_id))
            log_event(task_id, f"Note from maintenance: {notes[:400]}")

        if new_status != t["status"]:
            link_res, link_sub = f"/resident/reports/{task_id}", f"/subdivision/reports/{task_id}"
            if new_status == "in_progress":
                execute("UPDATE maintenance_requests SET status='in_progress' WHERE id=%s", (task_id,))
                log_event(task_id, "Work started")
                notify(t["resident_id"], f"Work has started on your report #{task_id}.", link_res)
            else:
                execute("UPDATE maintenance_requests SET status='completed', completed_at=NOW(), "
                        "resident_response='none', verified=0 WHERE id=%s", (task_id,))
                log_event(task_id, "Marked completed by maintenance")
                notify(t["resident_id"], f"Report #{task_id} was completed. Please confirm it's fixed.", link_res)
                notify_role("subadmin", f"Task #{task_id} was completed and is ready to verify.", link_sub)
            audit("task.status", f"#{task_id} -> {new_status}")
        flash("Task updated.", "success")
        return back

    @app.route("/maintenance/history")
    @login_required("maintenance")
    def maintenance_history():
        search, d1, d2 = arg("q"), date_arg("from"), date_arg("to")
        sql = "SELECT * FROM maintenance_requests WHERE assigned_to=%s AND status='completed'"
        args = [g.user["id"]]
        if search:
            sql += " AND (description LIKE %s OR category LIKE %s OR location LIKE %s OR CAST(id AS CHAR)=%s)"
            args += [like(search)] * 3 + [search]
        if d1:
            sql += " AND completed_at >= %s"; args.append(d1)
        if d2:
            sql += " AND completed_at < %s + INTERVAL 1 DAY"; args.append(d2)
        rows = query(sql + f" ORDER BY completed_at DESC LIMIT {LIST_LIMIT}", args)
        summary = query("SELECT COUNT(*) AS n, AVG(rating) AS avg_rating FROM maintenance_requests "
                        "WHERE assigned_to=%s AND status='completed'", (g.user["id"],), one=True)
        return render_template("maintenance/history.html", rows=rows, q=search, d1=d1, d2=d2, summary=summary)
