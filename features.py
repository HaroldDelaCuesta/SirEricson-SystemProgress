"""Features every role shares: the notification bell and the notifications page."""
from flask import g, jsonify, redirect, render_template, request, url_for

from common import arg, execute, login_required, query, safe_back


def ago(minutes):
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 60 * 24:
        return f"{minutes // 60} h ago"
    return f"{minutes // (60 * 24)} d ago"


def register(app):
    @app.route("/notifications")
    @login_required()
    def notifications_page():
        view = arg("view", ("unread",))
        sql = ("SELECT *, TIMESTAMPDIFF(MINUTE, created_at, NOW()) AS mins FROM notifications WHERE user_id=%s")
        if view:
            sql += " AND is_read=0"
        rows = query(sql + " ORDER BY id DESC LIMIT 200", (g.user["id"],))
        for r in rows:
            r["ago"] = ago(r["mins"])
        return render_template("notifications.html", rows=rows, view=view)

    @app.route("/notifications/recent")
    @login_required()
    def notifications_recent():
        rows = query("SELECT id, message, is_read, TIMESTAMPDIFF(MINUTE, created_at, NOW()) AS mins "
                     "FROM notifications WHERE user_id=%s ORDER BY id DESC LIMIT 8", (g.user["id"],))
        unread = query("SELECT COUNT(*) AS n FROM notifications WHERE user_id=%s AND is_read=0",
                       (g.user["id"],), one=True)["n"]
        return jsonify(count=unread, items=[
            dict(message=r["message"], read=bool(r["is_read"]), when=ago(r["mins"]),
                 url=url_for("notification_open", nid=r["id"])) for r in rows])

    @app.route("/notifications/<int:nid>/open")
    @login_required()
    def notification_open(nid):
        n = query("SELECT link FROM notifications WHERE id=%s AND user_id=%s", (nid, g.user["id"]), one=True)
        if not n:
            return redirect(url_for("notifications_page"))
        execute("UPDATE notifications SET is_read=1 WHERE id=%s", (nid,))
        link = n["link"] or ""
        # only ever send people to pages on this site
        if link.startswith("/") and not link.startswith("//"):
            return redirect(link)
        return redirect(url_for("notifications_page"))

    @app.route("/notifications/read-all", methods=["POST"])
    @login_required()
    def notifications_read_all():
        execute("UPDATE notifications SET is_read=1 WHERE user_id=%s AND is_read=0", (g.user["id"],))
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(ok=True)
        return redirect(safe_back("notifications_page"))
