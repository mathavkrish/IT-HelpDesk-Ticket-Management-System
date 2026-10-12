import sqlite3

import os
import secrets
from functools import wraps
from getpass import getpass

import csv
import io

from flask import Response


from flask import session
from werkzeug.security import generate_password_hash, check_password_hash

from pathlib import Path

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, abort
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "local-dev-only-change-before-deployment")

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "helpdesk.db"

CATEGORIES = ["Hardware", "Software", "Network", "Other"]
PRIORITIES = ["Low", "Medium", "High"]


def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with get_db() as db:
        
        db.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_name TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                category TEXT NOT NULL,
                priority TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Open',
                created_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
            )
        """)
        
        columns = [
            row["name"]
            for row in db.execute("PRAGMA table_info(tickets)")
        ]

        if "assigned_to" not in columns:
            db.execute(
                "ALTER TABLE tickets ADD COLUMN assigned_to TEXT"
            )

def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "admin_id" not in session:
            flash("Please login as admin first.", "warning")
            return redirect(url_for("admin_login"))

        return view(*args, **kwargs)

    return wrapped_view

@app.route("/")
def index():
    with get_db() as db:
        tickets = db.execute(
            "SELECT * FROM tickets ORDER BY id DESC"
        ).fetchall()

    return render_template("index.html", tickets=tickets)


@app.route("/tickets/new", methods=["GET", "POST"])
def new_ticket():
    if request.method == "POST":
        employee_name = request.form.get("employee_name", "").strip()
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "").strip()
        priority = request.form.get("priority", "").strip()

        errors = []

        if not employee_name:
            errors.append("Employee name is required.")
        elif len(employee_name) > 100:
            errors.append("Employee name must be 100 characters or fewer.")

        if not title:
            errors.append("Issue title is required.")
        elif len(title) > 150:
            errors.append("Issue title must be 150 characters or fewer.")

        if not description:
            errors.append("Issue description is required.")
        elif len(description) > 5000:
            errors.append("Description must be 5000 characters or fewer.")

        if category not in CATEGORIES:
            errors.append("Please select a valid category.")

        if priority not in PRIORITIES:
            errors.append("Please select a valid priority.")

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template(
                "new_ticket.html",
                categories=CATEGORIES,
                priorities=PRIORITIES,
                form=request.form
            ), 400

        with get_db() as db:
            cursor = db.execute(
                """
                INSERT INTO tickets
                    (employee_name, title, description, category, priority)
                VALUES (?, ?, ?, ?, ?)
                """,
                (employee_name, title, description, category, priority)
            )
            ticket_id = cursor.lastrowid

        flash(f"Ticket #{ticket_id} created successfully!", "success")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    return render_template(
        "new_ticket.html",
        categories=CATEGORIES,
        priorities=PRIORITIES,
        form={}
    )


@app.route("/tickets/<int:ticket_id>")
def ticket_detail(ticket_id):
    with get_db() as db:
        ticket = db.execute(
            "SELECT * FROM tickets WHERE id = ?",
            (ticket_id,)
        ).fetchone()

    if ticket is None:
        abort(404)

    return render_template("ticket_detail.html", ticket=ticket)


@app.errorhandler(404)
def not_found(error):
    return render_template("404.html"), 404


@app.route("/tickets/<int:ticket_id>/status", methods=["POST"])
@admin_required
def update_ticket_status(ticket_id):
    new_status = request.form.get("status", "").strip()

    allowed_statuses = ["Open", "In Progress", "Resolved"]

    if new_status not in allowed_statuses:
        flash("Invalid ticket status.", "danger")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    with get_db() as db:
        ticket = db.execute(
            "SELECT id FROM tickets WHERE id = ?",
            (ticket_id,)
        ).fetchone()

        if ticket is None:
            abort(404)

        db.execute(
            "UPDATE tickets SET status = ? WHERE id = ?",
            (new_status, ticket_id)
        )

    flash("Ticket status updated successfully!", "success")

    return redirect(url_for("ticket_detail", ticket_id=ticket_id))




@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        with get_db() as db:
            admin = db.execute(
                "SELECT * FROM admins WHERE username = ?",
                (username,)
            ).fetchone()

        if admin and check_password_hash(
            admin["password_hash"], password
        ):
            session.clear()
            session["admin_id"] = admin["id"]
            session["admin_username"] = admin["username"]

            flash("Admin login successful!", "success")
            return redirect(url_for("index"))

        flash("Invalid username or password.", "danger")

    return render_template("admin_login.html")


@app.route("/admin/logout", methods=["POST"])
@admin_required
def admin_logout():
    session.clear()
    flash("Logged out successfully.", "success")
    return redirect(url_for("admin_login"))


def create_admin():
    username = input("Enter admin username: ").strip()

    if not username:
        print("Username cannot be empty.")
        return

    password = getpass("Enter admin password: ")
    confirm_password = getpass("Confirm password: ")

    if len(password) < 12:
        print("Password must have at least 12 characters.")
        return

    if password != confirm_password:
        print("Passwords do not match.")
        return

    password_hash = generate_password_hash(password)

    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO admins (username, password_hash) VALUES (?, ?)",
                (username, password_hash)
            )
        print("Admin account created successfully!")
    except sqlite3.IntegrityError:
        print("This username already exists.")


@app.route("/tickets/<int:ticket_id>/assign", methods=["POST"])
@admin_required
def assign_ticket(ticket_id):
    technician = request.form.get("assigned_to", "").strip()

    if not technician or len(technician) > 100:
        flash("Enter a valid technician name.", "danger")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    with get_db() as db:
        ticket = db.execute(
            "SELECT id FROM tickets WHERE id = ?",
            (ticket_id,)
        ).fetchone()

        if ticket is None:
            abort(404)

        db.execute(
            "UPDATE tickets SET assigned_to = ? WHERE id = ?",
            (technician, ticket_id)
        )

    flash("Ticket assigned successfully!", "success")
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))

@app.route("/admin/analytics")
@admin_required
def analytics():
    with get_db() as db:
        status_rows = db.execute("""
            SELECT status, COUNT(*) AS count
            FROM tickets
            GROUP BY status
        """).fetchall()

        priority_rows = db.execute("""
            SELECT priority, COUNT(*) AS count
            FROM tickets
            GROUP BY priority
        """).fetchall()

        category_rows = db.execute("""
            SELECT category, COUNT(*) AS count
            FROM tickets
            GROUP BY category
        """).fetchall()

        technician_rows = db.execute("""
            SELECT assigned_to, COUNT(*) AS count
            FROM tickets
            WHERE assigned_to IS NOT NULL
              AND TRIM(assigned_to) != ''
            GROUP BY assigned_to
            ORDER BY count DESC
        """).fetchall()

        total_tickets = db.execute(
            "SELECT COUNT(*) FROM tickets"
        ).fetchone()[0]

    status_counts = {
        "Open": 0,
        "In Progress": 0,
        "Resolved": 0
    }

    for row in status_rows:
        status_counts[row["status"]] = row["count"]

    priority_counts = {
        "Low": 0,
        "Medium": 0,
        "High": 0
    }

    for row in priority_rows:
        priority_counts[row["priority"]] = row["count"]

    category_counts = {
        row["category"]: row["count"]
        for row in category_rows
    }

    technician_counts = {
        row["assigned_to"]: row["count"]
        for row in technician_rows
    }

    return render_template(
        "analytics.html",
        total_tickets=total_tickets,
        status_counts=status_counts,
        priority_counts=priority_counts,
        category_counts=category_counts,
        technician_counts=technician_counts
    )

def get_filtered_tickets(args):
    search = args.get("search", "").strip()
    status = args.get("status", "").strip()
    priority = args.get("priority", "").strip()
    category = args.get("category", "").strip()
    technician = args.get("technician", "").strip()

    query = "SELECT * FROM tickets WHERE 1=1"
    params = []

    if search:
        query += """
            AND (
                title LIKE ?
                OR description LIKE ?
                OR employee_name LIKE ?
            )
        """
        keyword = f"%{search}%"
        params.extend([keyword, keyword, keyword])

    if status in ["Open", "In Progress", "Resolved"]:
        query += " AND status = ?"
        params.append(status)

    if priority in ["Low", "Medium", "High"]:
        query += " AND priority = ?"
        params.append(priority)

    if category in CATEGORIES:
        query += " AND category = ?"
        params.append(category)

    if technician:
        query += " AND assigned_to LIKE ?"
        params.append(f"%{technician}%")

    query += " ORDER BY id DESC"

    with get_db() as db:
        return db.execute(query, params).fetchall()


@app.route("/admin/tickets")
@admin_required
def admin_tickets():
    tickets = get_filtered_tickets(request.args)

    return render_template(
        "admin_tickets.html",
        tickets=tickets,
        filters=request.args,
        categories=CATEGORIES
    )


@app.route("/admin/tickets/export")
@admin_required
def export_tickets():
    tickets = get_filtered_tickets(request.args)

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Ticket ID",
        "Employee",
        "Title",
        "Description",
        "Category",
        "Priority",
        "Status",
        "Assigned Technician",
        "Created At"
    ])

    for ticket in tickets:
        writer.writerow([
            ticket["id"],
            ticket["employee_name"],
            ticket["title"],
            ticket["description"],
            ticket["category"],
            ticket["priority"],
            ticket["status"],
            ticket["assigned_to"] or "",
            ticket["created_at"]
        ])

    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                "attachment; filename=helpdesk_tickets.csv"
        }
    )


if __name__ == "__main__":
    init_db()

    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "create-admin":
        create_admin()
    else:
        app.run(debug=True)
