"""Resident pages: reports, tracking, announcements, lost & found, dues, profile."""
from flask import abort, flash, g, redirect, render_template, request, url_for

from common import (CATEGORIES, LIST_LIMIT, REPORT_STATUSES, arg, audit, date_arg, execute, history_for, like,
                    log_event, login_required, notify, notify_role, photos_for, query, save_image, save_images)


def register(app):
    def my_report(report_id):
        r = query("SELECT m.*, a.full_name AS assignee FROM maintenance_requests m "
                  "LEFT JOIN users a ON a.id=m.assigned_to WHERE m.id=%s AND m.resident_id=%s",
                  (report_id, g.user["id"]), one=True)
        if not r:
            abort(404)
        return r

    # ---------- home ----------
    @app.route("/resident")
    @login_required("resident")
    def resident_home():
        me = g.user["id"]
        one = lambda sql: query(sql, (me,), one=True)["n"]
        stats = dict(
            total=one("SELECT COUNT(*) AS n FROM maintenance_requests WHERE resident_id=%s"),
            open=one("SELECT COUNT(*) AS n FROM maintenance_requests WHERE resident_id=%s AND status<>'completed'"),
            confirm=one("SELECT COUNT(*) AS n FROM maintenance_requests WHERE resident_id=%s "
                        "AND status='completed' AND resident_response='none'"),)
        news = query("SELECT * FROM announcements WHERE published=1 ORDER BY pinned DESC, created_at DESC LIMIT 3")
        upcoming = query("SELECT * FROM announcements WHERE published=1 AND kind='activity' "
                         "AND event_date >= CURDATE() ORDER BY event_date LIMIT 3")
        reports = query("SELECT id, category, status, created_at, resident_response FROM maintenance_requests "
                        "WHERE resident_id=%s ORDER BY created_at DESC LIMIT 4", (me,))
        return render_template("resident/home.html", stats=stats, news=news, upcoming=upcoming, reports=reports)

    # ---------- reports ----------
    @app.route("/resident/reports")
    @login_required("resident")
    def resident_reports():
        search, status = arg("q"), arg("status", REPORT_STATUSES)
        category, d1, d2 = arg("category"), date_arg("from"), date_arg("to")
        view = arg("view", ("confirm",))
        sql = "SELECT * FROM maintenance_requests WHERE resident_id=%s"
        args = [g.user["id"]]
        if search:
            sql += " AND (description LIKE %s OR category LIKE %s OR location LIKE %s OR CAST(id AS CHAR)=%s)"
            args += [like(search)] * 3 + [search]
        if status:
            sql += " AND status=%s"; args.append(status)
        if category:
            sql += " AND category=%s"; args.append(category)
        if d1:
            sql += " AND created_at >= %s"; args.append(d1)
        if d2:
            sql += " AND created_at < %s + INTERVAL 1 DAY"; args.append(d2)
        if view == "confirm":
            sql += " AND status='completed' AND resident_response='none'"
        sql += f" ORDER BY created_at DESC LIMIT {LIST_LIMIT}"
        cats = [(c, c) for c in sorted({*CATEGORIES, *[r["category"] for r in query(
            "SELECT DISTINCT category FROM maintenance_requests WHERE resident_id=%s", (g.user["id"],))]})]
        return render_template("resident/reports.html", rows=query(sql, args), q=search, status=status,
                               category=category, d1=d1, d2=d2, view=view, cats=cats, statuses=REPORT_STATUSES)

    @app.route("/resident/reports/new", methods=["GET", "POST"])
    @login_required("resident")
    def resident_report_new():
        me = query("SELECT address FROM users WHERE id=%s", (g.user["id"],), one=True)
        form = {"location": me["address"] or ""}
        if request.method == "POST":
            form = {k: request.form.get(k, "").strip() for k in ("category", "location", "description")}
            if form["category"] not in CATEGORIES:
                flash("Choose a category.", "error")
            elif len(form["description"]) < 10:
                flash("Describe the problem in a few words (at least 10 characters).", "error")
            else:
                try:
                    photos = save_images(request.files.getlist("photos"), limit=4)
                except ValueError as e:
                    flash(str(e), "error")
                else:
                    rid = execute("INSERT INTO maintenance_requests (resident_id, category, location, description) "
                                  "VALUES (%s,%s,%s,%s)", (g.user["id"], form["category"], form["location"][:160],
                                                           form["description"]))
                    for name in photos:
                        execute("INSERT INTO report_photos (request_id, file, kind, uploaded_by) VALUES (%s,%s,'report',%s)",
                                (rid, name, g.user["id"]))
                    log_event(rid, "Report submitted" + (f" with {len(photos)} photo(s)" if photos else ""))
                    notify_role("subadmin", f"New report #{rid}: {form['category']} from {g.user['full_name']}",
                                f"/subdivision/reports/{rid}")
                    audit("report.submit", f"#{rid} {form['category']}")
                    flash("Report submitted. You can follow its progress here.", "success")
                    return redirect(url_for("resident_report_detail", report_id=rid))
        return render_template("resident/report_new.html", form=form, categories=CATEGORIES)

    @app.route("/resident/reports/<int:report_id>")
    @login_required("resident")
    def resident_report_detail(report_id):
        r = my_report(report_id)
        photos = photos_for(report_id)
        return render_template("resident/report_detail.html", r=r,
                               report_photos=[p for p in photos if p["kind"] == "report"],
                               done_photos=[p for p in photos if p["kind"] == "completion"],
                               history=history_for(report_id))

    @app.route("/resident/reports/<int:report_id>/confirm", methods=["POST"])
    @login_required("resident")
    def resident_report_confirm(report_id):
        r = my_report(report_id)
        if r["status"] != "completed" or r["resident_response"] != "none":
            flash("There's nothing to confirm on this report right now.", "info")
            return redirect(url_for("resident_report_detail", report_id=report_id))
        rating = request.form.get("rating", "")
        rating = int(rating) if rating.isdigit() and 1 <= int(rating) <= 5 else None
        feedback = request.form.get("feedback", "").strip()[:1000] or None
        execute("UPDATE maintenance_requests SET resident_response='confirmed', rating=%s, feedback=%s WHERE id=%s",
                (rating, feedback, report_id))
        log_event(report_id, "Resident confirmed the problem is fixed" + (f" (rated {rating}/5)" if rating else ""))
        text = f"Report #{report_id}: the resident confirmed it's fixed" + (f", rated {rating}/5." if rating else ".")
        notify_role("subadmin", text, f"/subdivision/reports/{report_id}")
        notify(r["assigned_to"], text, f"/maintenance/tasks/{report_id}")
        flash("Thanks for confirming!", "success")
        return redirect(url_for("resident_report_detail", report_id=report_id))

    @app.route("/resident/reports/<int:report_id>/reopen", methods=["POST"])
    @login_required("resident")
    def resident_report_reopen(report_id):
        r = my_report(report_id)
        note = request.form.get("note", "").strip()[:1000]
        if r["status"] != "completed":
            flash("Only completed reports can be reopened.", "error")
        elif len(note) < 5:
            flash("Please tell us what is still wrong.", "error")
        else:
            execute("UPDATE maintenance_requests SET status='pending', resident_response='reopened', reopen_note=%s, "
                    "verified=0, completed_at=NULL, rating=NULL, feedback=NULL WHERE id=%s", (note, report_id))
            log_event(report_id, f"Resident reopened the report: {note}")
            text = f"Report #{report_id} was reopened by the resident: {note[:120]}"
            notify_role("subadmin", text, f"/subdivision/reports/{report_id}")
            notify(r["assigned_to"], text, f"/maintenance/tasks/{report_id}")
            audit("report.reopen", f"#{report_id}")
            flash("Report reopened. We'll take another look.", "success")
        return redirect(url_for("resident_report_detail", report_id=report_id))

    # ---------- announcements and activities ----------
    @app.route("/resident/announcements")
    @login_required("resident")
    def resident_announcements():
        search, kind = arg("q"), arg("kind", ("announcement", "activity"))
        when = arg("when", ("upcoming",))
        sql, args = "SELECT * FROM announcements WHERE published=1", []
        if search:
            sql += " AND (title LIKE %s OR body LIKE %s)"; args += [like(search)] * 2
        if kind:
            sql += " AND kind=%s"; args.append(kind)
        if when:
            sql += " AND event_date >= CURDATE()"
        sql += f" ORDER BY pinned DESC, COALESCE(event_date, DATE(created_at)) DESC, created_at DESC LIMIT {LIST_LIMIT}"
        return render_template("resident/announcements.html", rows=query(sql, args), q=search, kind=kind, when=when)

    # ---------- lost and found ----------
    @app.route("/resident/lost-found")
    @login_required("resident")
    def resident_lostfound():
        me, search = g.user["id"], arg("q")
        sql = ("SELECT id, item_name, description, location, photo, status, claimant_id, created_at FROM lost_found "
               "WHERE kind='found' AND status IN ('open','claimed')")
        args = []
        if search:
            sql += " AND (item_name LIKE %s OR description LIKE %s OR location LIKE %s)"; args += [like(search)] * 3
        sql += f" ORDER BY created_at DESC LIMIT {LIST_LIMIT}"
        mine = query("SELECT * FROM lost_found WHERE ((kind='lost' AND reported_by=%s) OR claimant_id=%s) "
                     "AND status<>'removed' ORDER BY created_at DESC", (me, me))
        return render_template("resident/lostfound.html", found=query(sql, args), mine=mine, q=search, me=me)

    @app.route("/resident/lost-found/new", methods=["POST"])
    @login_required("resident")
    def resident_lost_new():
        f = {k: request.form.get(k, "").strip() for k in ("item_name", "description", "location")}
        if not f["item_name"] or len(f["description"]) < 5:
            flash("Give the item a name and describe it (color, brand, markings).", "error")
        else:
            try:
                photo = save_image(request.files["photo"]) if request.files.get("photo") and request.files["photo"].filename else None
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("resident_lostfound"))
            lid = execute("INSERT INTO lost_found (reported_by, kind, item_name, description, location, photo) "
                          "VALUES (%s,'lost',%s,%s,%s,%s)", (g.user["id"], f["item_name"][:120], f["description"],
                                                             f["location"][:160], photo))
            notify_role("security", f"Lost item reported: {f['item_name']}", "/security/found?kind=lost")
            audit("lostfound.report_lost", f"#{lid} {f['item_name']}")
            flash("Lost item reported. Security will watch for it.", "success")
        return redirect(url_for("resident_lostfound"))

    @app.route("/resident/lost-found/<int:item_id>/claim", methods=["POST"])
    @login_required("resident")
    def resident_claim(item_id):
        item = query("SELECT item_name FROM lost_found WHERE id=%s AND kind='found' AND status='open'",
                     (item_id,), one=True)
        details = request.form.get("claim_details", "").strip()[:1000]
        if not item:
            flash("That item is no longer available to claim.", "error")
        elif len(details) < 10:
            flash("Describe something only the owner would know (marks, contents, where you lost it).", "error")
        else:
            execute("UPDATE lost_found SET status='claimed', claimant_id=%s, claim_details=%s, claimed_at=NOW() "
                    "WHERE id=%s AND status='open'", (g.user["id"], details, item_id))
            notify_role("security", f"Claim on '{item['item_name']}' by {g.user['full_name']} needs verification",
                        "/security/found?view=claims")
            audit("lostfound.claim", f"#{item_id} {item['item_name']}")
            flash("Claim sent. Bring a valid ID to the guardhouse so security can verify and release it.", "success")
        return redirect(url_for("resident_lostfound"))

    @app.route("/resident/lost-found/<int:item_id>/withdraw", methods=["POST"])
    @login_required("resident")
    def resident_claim_withdraw(item_id):
        execute("UPDATE lost_found SET status='open', claimant_id=NULL, claim_details=NULL, claimed_at=NULL "
                "WHERE id=%s AND claimant_id=%s AND status='claimed'", (item_id, g.user["id"]))
        flash("Claim withdrawn.", "success")
        return redirect(url_for("resident_lostfound"))

    @app.route("/resident/lost-found/<int:item_id>/close", methods=["POST"])
    @login_required("resident")
    def resident_lost_close(item_id):
        execute("UPDATE lost_found SET status='closed' WHERE id=%s AND reported_by=%s AND kind='lost' "
                "AND status='open'", (item_id, g.user["id"]))
        flash("Marked as found. Glad you got it back!", "success")
        return redirect(url_for("resident_lostfound"))

    # ---------- profile ----------
    @app.route("/resident/profile", methods=["GET", "POST"])
    @login_required("resident")
    def resident_profile():
        me = query("SELECT * FROM users WHERE id=%s", (g.user["id"],), one=True)
        if request.method == "POST":
            f = {k: request.form.get(k, "").strip() for k in ("full_name", "email", "phone", "address")}
            f["email"] = f["email"].lower()
            if not all([f["full_name"], f["email"], f["address"]]) or "@" not in f["email"]:
                flash("Name, a valid email, and address are required.", "error")
            elif query("SELECT id FROM users WHERE email=%s AND id<>%s", (f["email"], me["id"]), one=True):
                flash("That email is already used by another account.", "error")
            else:
                execute("UPDATE users SET full_name=%s, email=%s, phone=%s, address=%s WHERE id=%s",
                        (f["full_name"][:120], f["email"][:120], f["phone"][:30], f["address"][:160], me["id"]))
                audit("profile.update", "")
                flash("Profile updated.", "success")
                return redirect(url_for("resident_profile"))
            me.update(f)
        return render_template("resident/profile.html", me=me)
