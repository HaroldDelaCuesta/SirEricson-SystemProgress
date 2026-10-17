"""Security personnel: found items and claims, visitor log, incidents, vehicles, shift reports."""
import re
from datetime import date, datetime

from flask import abort, flash, g, redirect, render_template, request, url_for

from common import (ID_TYPES, LIST_LIMIT, arg, audit, date_arg, execute, like, login_required, notify,
                    notify_role, parse_date, query, safe_back, save_image)

INCIDENT_CATEGORIES = ["Noise complaint", "Suspicious person or activity", "Vehicle or parking",
                       "Theft or break-in", "Fire or safety hazard", "Trespassing",
                       "Dispute between residents", "Other"]
SHIFTS = ("morning", "afternoon", "night")


def register(app):
    # ---------- dashboard ----------
    @app.route("/security")
    @login_required("security")
    def security_home():
        one = lambda sql: query(sql, one=True)["n"]
        stats = dict(
            held=one("SELECT COUNT(*) AS n FROM lost_found WHERE kind='found' AND status='open'"),
            claims=one("SELECT COUNT(*) AS n FROM lost_found WHERE status='claimed'"),
            released=one("SELECT COUNT(*) AS n FROM lost_found WHERE status='released'"),
            lost=one("SELECT COUNT(*) AS n FROM lost_found WHERE kind='lost' AND status='open'"))
        claims = query("SELECT l.id, l.item_name, u.full_name AS claimant, l.claimed_at FROM lost_found l "
                       "JOIN users u ON u.id=l.claimant_id WHERE l.status='claimed' ORDER BY l.claimed_at LIMIT 5")
        return render_template("security/home.html", stats=stats, claims=claims)

    # ---------- found items, lost reports, claims ----------
    @app.route("/security/found")
    @login_required("security")
    def security_found():
        search, kind = arg("q"), arg("kind", ("lost", "found"))
        status = arg("status", ("open", "claimed", "released", "closed", "removed"))
        view = arg("view", ("claims",))
        sql = ("SELECT l.*, r.full_name AS reporter, c.full_name AS claimant, c.address AS claimant_address "
               "FROM lost_found l JOIN users r ON r.id=l.reported_by LEFT JOIN users c ON c.id=l.claimant_id "
               "WHERE l.status<>'removed'")
        args = []
        if view == "claims":
            sql += " AND l.status='claimed'"
        if search:
            sql += (" AND (l.item_name LIKE %s OR l.description LIKE %s OR l.location LIKE %s "
                    "OR l.finder_name LIKE %s)"); args += [like(search)] * 4
        if kind:
            sql += " AND l.kind=%s"; args.append(kind)
        if status:
            sql += " AND l.status=%s"; args.append(status)
        sql += f" ORDER BY (l.status='claimed') DESC, l.created_at DESC LIMIT {LIST_LIMIT}"
        return render_template("security/found.html", rows=query(sql, args), q=search, kind=kind, status=status,
                               view=view, id_types=ID_TYPES)

    @app.route("/security/found/new", methods=["POST"])
    @login_required("security")
    def security_found_new():
        f = {k: request.form.get(k, "").strip() for k in ("item_name", "description", "location", "finder_name")}
        if not f["item_name"] or len(f["description"]) < 5:
            flash("Give the item a name and describe it.", "error")
            return redirect(url_for("security_found"))
        try:
            up = request.files.get("photo")
            photo = save_image(up) if up and up.filename else None
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("security_found"))
        lid = execute("INSERT INTO lost_found (reported_by, handled_by, kind, item_name, description, location, "
                      "finder_name, photo) VALUES (%s,%s,'found',%s,%s,%s,%s,%s)",
                      (g.user["id"], g.user["id"], f["item_name"][:120], f["description"], f["location"][:160],
                       f["finder_name"][:120] or None, photo))
        # tell residents who reported something similar as lost
        words = [w for w in re.findall(r"[A-Za-z0-9]+", f["item_name"]) if len(w) > 3][:4]
        if words:
            clause = " OR ".join(["item_name LIKE %s"] * len(words))
            for m in query(f"SELECT DISTINCT reported_by FROM lost_found WHERE kind='lost' AND status='open' "
                           f"AND ({clause}) LIMIT 10", [like(w) for w in words]):
                notify(m["reported_by"], f"A found item may match your lost report: {f['item_name']}",
                       "/resident/lost-found")
        audit("lostfound.record_found", f"#{lid} {f['item_name']}")
        flash("Found item recorded.", "success")
        return redirect(url_for("security_found"))

    @app.route("/security/found/<int:item_id>/release", methods=["POST"])
    @login_required("security")
    def security_release(item_id):
        item = query("SELECT * FROM lost_found WHERE id=%s", (item_id,), one=True)
        if not item:
            abort(404)
        id_type = request.form.get("id_type", "")
        released_to = request.form.get("released_to", "").strip()[:120]
        notes = request.form.get("verification_notes", "").strip()[:1000]
        # The verification steps are required, not optional: the server refuses a release without all of them.
        if item["status"] != "claimed" or item["kind"] != "found":
            flash("Only an item with a pending claim can be released.", "error")
        elif id_type not in ID_TYPES or not released_to or len(notes) < 5:
            flash("Record which ID was shown, who it was released to, and your verification notes.", "error")
        elif not (request.form.get("confirm_id") and request.form.get("confirm_details")):
            flash("Tick both checks: the ID matches the person, and their description matches the item.", "error")
        else:
            execute("UPDATE lost_found SET status='released', id_checked=%s, released_to=%s, verification_notes=%s, "
                    "released_at=NOW(), handled_by=%s WHERE id=%s", (id_type, released_to, notes, g.user["id"], item_id))
            notify(item["claimant_id"], f"Your claim was verified and '{item['item_name']}' has been released to you.",
                   "/resident/lost-found")
            audit("lostfound.release", f"#{item_id} {item['item_name']} to {released_to} ({id_type})")
            flash("Item released and the record saved.", "success")
        return redirect(safe_back("security_found"))

    @app.route("/security/found/<int:item_id>/reject", methods=["POST"])
    @login_required("security")
    def security_reject(item_id):
        item = query("SELECT * FROM lost_found WHERE id=%s AND status='claimed'", (item_id,), one=True)
        reason = request.form.get("reason", "").strip()[:500]
        if not item:
            flash("There's no pending claim on that item.", "error")
        elif len(reason) < 5:
            flash("Give a short reason so the resident understands.", "error")
        else:
            execute("UPDATE lost_found SET status='open', claimant_id=NULL, claim_details=NULL, claimed_at=NULL, "
                    "verification_notes=%s, handled_by=%s WHERE id=%s",
                    (f"Earlier claim rejected: {reason}", g.user["id"], item_id))
            notify(item["claimant_id"], f"Your claim on '{item['item_name']}' was not verified: {reason}",
                   "/resident/lost-found")
            audit("lostfound.reject_claim", f"#{item_id} {item['item_name']}: {reason}")
            flash("Claim rejected. The item is available again.", "success")
        return redirect(safe_back("security_found"))

    @app.route("/security/found/<int:item_id>/close", methods=["POST"])
    @login_required("security")
    def security_close_lost(item_id):
        execute("UPDATE lost_found SET status='closed', handled_by=%s WHERE id=%s AND kind='lost' AND status='open'",
                (g.user["id"], item_id))
        audit("lostfound.close", f"#{item_id}")
        flash("Lost report closed.", "success")
        return redirect(safe_back("security_found"))

