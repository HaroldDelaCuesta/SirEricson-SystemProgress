"""Run once after importing database.sql:  py create_admin.py
Creates the first System Administrator. That account then creates every other account
(including the Subdivision Administrator) from the Accounts page."""
from werkzeug.security import generate_password_hash
from app import get_conn, password_problem

name = input("Full name: ").strip()
username = input("Username: ").strip().lower()
email = input("Email: ").strip().lower()
print("Password rules: at least 10 characters, with letters and numbers.")
password = input("Password (shown as you type): ")
problem = password_problem(password, username, email.split("@")[0])
if problem:
    raise SystemExit(problem)

conn = get_conn()
with conn.cursor() as cur:
    cur.execute(
        "INSERT INTO users (full_name, username, email, password_hash, role, status) "
        "VALUES (%s,%s,%s,%s,'sysadmin','active')",
        (name, username, email, generate_password_hash(password)),
    )
conn.close()
print("System administrator created. You can now log in.")
