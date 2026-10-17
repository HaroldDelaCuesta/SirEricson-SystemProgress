# Subdivision Portal (Windows + XAMPP)

XAMPP gives you **MySQL/MariaDB** and phpMyAdmin. The Python side is a **Flask** app.

## New install
1. XAMPP Control Panel: **Start MySQL**.
2. http://localhost/phpmyadmin > **Import** > `database.sql` > Go. (It wipes any old `subdivision_db`.)
3. In PowerShell, inside this folder:
   ```
   py -m pip install -r requirements.txt
   py -m pip install -r requirements-optional.txt     # optional, see below
   py create_admin.py        # creates the first System Administrator
   py app.py
   ```
4. Open http://127.0.0.1:5000

### Optional packages (Pillow and reportlab)
The app runs without them. If they can't be installed (slow or blocked internet), skip `requirements-optional.txt`:
- Without **Pillow**: photo uploads still work and still must be real JPG/PNG/GIF/WEBP files, but photos are not shrunk and keep hidden data such as GPS location.
- Without **reportlab** (which needs Pillow): everything works except the PDF downloads, which show a message instead.
You can install them later and just restart `py app.py`.

## Upgrading an existing install (keeps your data)
phpMyAdmin > select `subdivision_db` > **SQL** tab > paste all of `upgrade_v3.sql` > Go. Then `py -m pip install -r requirements.txt` and `py app.py`.

## Who does what
- **System administrator** `/system`: accounts, approvals, roles, password resets, settings, error log, activity log, who is logged in (force logout), login history, database backup.
- **Subdivision administrator** `/subdivision`: review reports, set category / priority / due date, assign maintenance, verify repairs, announcements and activities (pin / publish), lost & found moderation, summary report with PDF.
- **Resident** `/resident`: report problems with photos, track progress, confirm or reopen a repair and rate it, announcements and upcoming activities, lost & found (report lost items, claim found ones), edit profile.
- **Maintenance** `/maintenance`: assigned tasks (urgent first, then due date), update status with notes and completion photos, work history.
- **Security** `/security`: record found items, verify and release claims (ID check and notes are required), close lost reports.

The System Administrator creates the Subdivision Administrator, Security and Maintenance accounts (Accounts > Add account) and approves resident sign-ups.

## Everyone
Notification bell, light/dark theme toggle (top right), search and filters on every list, "Forgot password?" on the login page.

## Forgot password and email
With no mail settings, reset emails are **saved in the `outbox` folder** and printed in the PowerShell window, so you can demo it offline. To send real email, set these before `py app.py` (example for Gmail with an app password):
```
$env:SMTP_HOST="smtp.gmail.com"; $env:SMTP_PORT="587"
$env:SMTP_USER="you@gmail.com"; $env:SMTP_PASSWORD="your-app-password"
$env:PUBLIC_URL="http://127.0.0.1:5000"     # the address used in the emailed link
```

## Backup and restore
System administrator > Backup downloads one `.sql` file. Restore with phpMyAdmin > Import. Photos are files, so copy the `uploads` folder as well.

## Security notes
Server-side logins (logout kills copied cookies), 30-minute idle logout, login lockout after 5 failures, password rules (10+ characters, letters and numbers), uploaded photos are re-encoded and only visible to people allowed to see that report, no-cache headers so Back after logout shows nothing.
Debug mode is off. For development with auto-reload: `$env:FLASK_DEBUG="1"; py app.py`. Set `USE_HTTPS=1` once the site is served over HTTPS. Keep `.secret_key` private.
Database settings can be overridden with `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`.

## Not built yet (planned)
Visitor log, incident log, vehicle registration, shift reports, resident directory, monthly dues (the tables already exist in the database).
