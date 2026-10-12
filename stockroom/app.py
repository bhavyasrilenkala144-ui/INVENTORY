"""Stockroom - inventory management website (Flask).

Runs locally with SQLite (zero setup) and on Vercel with Postgres
(set the DATABASE_URL environment variable).
"""

import csv
import hashlib
import hmac
import io
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:  # load a local .env file if python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    PG_ERRORS = (psycopg2.IntegrityError,)
except ImportError:  # only needed when DATABASE_URL is set
    psycopg2 = None
    PG_ERRORS = ()

from flask import (Flask, Response, abort, flash, redirect, render_template,
                   request, session, url_for)

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
ON_VERCEL = bool(os.environ.get("VERCEL"))
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_PG = bool(DATABASE_URL)
# Vercel's disk is read-only except /tmp, and /tmp is wiped often.
SQLITE_PATH = "/tmp/inventory.db" if ON_VERCEL else str(BASE_DIR / "inventory.db")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
AUTH_ON = bool(ADMIN_PASSWORD) or ON_VERCEL   # always require login online
SEED_DEMO = os.environ.get("SEED_DEMO") == "1" or "--demo" in sys.argv
TEMPORARY_DATA = ON_VERCEL and not USE_PG

# static_folder="public" lets the same CSS work locally and on Vercel's CDN.
app = Flask(__name__, static_folder="public", static_url_path="")
app.config["SECRET_KEY"] = (os.environ.get("SECRET_KEY")
                            or hashlib.sha256(b"stockroom:" + ADMIN_PASSWORD.encode()).hexdigest())
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = ON_VERCEL

try:
    APP_TZ = ZoneInfo(os.environ.get("APP_TIMEZONE", "UTC"))
except Exception:
    APP_TZ = timezone.utc

# --------------------------------------------------------------------------
# Tiny database layer: same code for SQLite (local) and Postgres (Vercel)
# --------------------------------------------------------------------------
INTEGRITY_ERRORS = (sqlite3.IntegrityError,) + PG_ERRORS

SCHEMA_SQLITE = [
    """CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'General',
        supplier TEXT NOT NULL DEFAULT '',
        quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
        reorder_level INTEGER NOT NULL DEFAULT 10,
        unit_price REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS movements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        delta INTEGER NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL)""",
]
SCHEMA_PG = [s.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
              .replace("unit_price REAL", "unit_price NUMERIC(12,2)")
             for s in SCHEMA_SQLITE]


def _sql(sql):
    """Write SQL with :named params; convert to %(named)s for Postgres."""
    return re.sub(r":(\w+)", r"%(\1)s", sql) if USE_PG else sql


class Tx:
    def __init__(self, conn):
        self.conn = conn

    def run(self, sql, **params):
        cur = self.conn.cursor()
        cur.execute(_sql(sql), params)
        return cur

    def all(self, sql, **params):
        return [dict(r) for r in self.run(sql, **params).fetchall()]

    def one(self, sql, **params):
        row = self.run(sql, **params).fetchone()
        return dict(row) if row else None

    def insert(self, sql, **params):
        """INSERT and return the new row id."""
        if USE_PG:
            return self.run(sql + " RETURNING id", **params).fetchone()["id"]
        return self.run(sql, **params).lastrowid


def _connect():
    if USE_PG:
        if psycopg2 is None:
            raise RuntimeError("DATABASE_URL is set but psycopg2 is not installed.")
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def tx():
    """One connection, one transaction: commits on success, rolls back on error."""
    conn = _connect()
    try:
        yield Tx(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


DEMO = [
    ("TS-BLK-M", "Cotton T-shirt, black, M", "Apparel", "Loom & Co", 48, 20, 8.50),
    ("TS-BLK-L", "Cotton T-shirt, black, L", "Apparel", "Loom & Co", 6, 20, 8.50),
    ("HD-GRY-L", "Fleece hoodie, grey, L", "Apparel", "Loom & Co", 0, 10, 24.00),
    ("MUG-350", "Ceramic mug, 350 ml", "Kitchen", "Kiln Works", 120, 40, 3.20),
    ("BTL-750", "Steel water bottle, 750 ml", "Kitchen", "Kiln Works", 31, 25, 11.75),
    ("NB-A5-DOT", "Dotted notebook, A5", "Stationery", "Paperline", 200, 60, 2.40),
    ("PEN-GEL-BL", "Gel pen, blue (box of 12)", "Stationery", "Paperline", 14, 15, 6.90),
    ("TAPE-48", "Packing tape, 48 mm", "Packaging", "BoxMart", 75, 30, 1.35),
]

_ready = False


def ensure_db():
    """Create tables (and demo data if requested) once per server instance."""
    global _ready
    if _ready:
        return
    with tx() as t:
        for stmt in (SCHEMA_PG if USE_PG else SCHEMA_SQLITE):
            t.run(stmt)
        if SEED_DEMO and t.one("SELECT COUNT(*) AS n FROM products")["n"] == 0:
            for sku, name, cat, sup, qty, reorder, price in DEMO:
                pid = t.insert(
                    "INSERT INTO products (sku, name, category, supplier, quantity, "
                    "reorder_level, unit_price, created_at) VALUES "
                    "(:sku, :name, :cat, :sup, :qty, :reorder, :price, :now)",
                    sku=sku, name=name, cat=cat, sup=sup, qty=qty,
                    reorder=reorder, price=price, now=now())
                if qty:
                    t.run("INSERT INTO movements (product_id, delta, reason, created_at) "
                          "VALUES (:pid, :d, 'Opening stock', :now)",
                          pid=pid, d=qty, now=now())
    _ready = True


# --------------------------------------------------------------------------
# Template helpers
# --------------------------------------------------------------------------
def stock_status(quantity, reorder_level):
    if quantity == 0:
        return "out"
    if quantity <= reorder_level:
        return "low"
    return "ok"


@app.template_filter("money")
def money(value):
    return f"{value:,.2f}"


@app.template_filter("ts")
def ts(value):
    """Stored as UTC text; shown in APP_TIMEZONE."""
    try:
        dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.astimezone(APP_TZ).strftime("%d %b %Y, %H:%M")
    except (TypeError, ValueError):
        return value


@app.context_processor
def inject_helpers():
    return {"stock_status": stock_status, "auth_on": AUTH_ON,
            "logged_in": bool(session.get("auth")),
            "temporary_data": TEMPORARY_DATA}


# --------------------------------------------------------------------------
# Startup + login guard
# --------------------------------------------------------------------------
@app.before_request
def before():
    if request.endpoint in (None, "static"):
        return None
    ensure_db()
    if AUTH_ON and request.endpoint != "login" and not session.get("auth"):
        return redirect(url_for("login"))
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if not ADMIN_PASSWORD:
            error = ("ADMIN_PASSWORD is not set on the server. Add it in your "
                     "Vercel project's Environment Variables, then redeploy.")
        elif hmac.compare_digest(request.form.get("password", "").encode(),
                                 ADMIN_PASSWORD.encode()):
            session["auth"] = True
            return redirect(url_for("dashboard"))
        else:
            error = "That password isn't right. Try again."
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------------------------
# Form parsing
# --------------------------------------------------------------------------
def parse_product_form(form):
    errors = []
    data = {
        "sku": form.get("sku", "").strip().upper(),
        "name": form.get("name", "").strip(),
        "category": form.get("category", "").strip() or "General",
        "supplier": form.get("supplier", "").strip(),
    }
    if not data["sku"]:
        errors.append("Enter a SKU.")
    if not data["name"]:
        errors.append("Enter a product name.")
    for field, label, cast in (("quantity", "Quantity", int),
                               ("reorder_level", "Reorder level", int),
                               ("unit_price", "Unit price", float)):
        raw = form.get(field, "").strip() or "0"
        try:
            value = cast(raw)
            if value < 0:
                raise ValueError
            data[field] = value
        except ValueError:
            errors.append(f"{label} must be a number that is 0 or higher.")
            data[field] = raw
    return data, errors


def get_product(t, product_id):
    product = t.one("SELECT * FROM products WHERE id = :id", id=product_id)
    if product is None:
        abort(404)
    return product


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------
@app.route("/")
def dashboard():
    with tx() as t:
        totals = t.one("""SELECT COUNT(*) AS products,
                                 COALESCE(SUM(quantity), 0) AS units,
                                 COALESCE(SUM(quantity * unit_price), 0) AS value
                          FROM products""")
        needs_restock = t.all("""SELECT * FROM products WHERE quantity <= reorder_level
                                 ORDER BY quantity ASC, name LIMIT 8""")
        restock_count = t.one("SELECT COUNT(*) AS n FROM products "
                              "WHERE quantity <= reorder_level")["n"]
        recent = t.all("""SELECT m.*, p.name, p.sku FROM movements m
                          JOIN products p ON p.id = m.product_id
                          ORDER BY m.id DESC LIMIT 8""")
    return render_template("dashboard.html", totals=totals,
                           needs_restock=needs_restock,
                           restock_count=restock_count, recent=recent)


@app.route("/products")
def products():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    status = request.args.get("status", "").strip()

    sql = "SELECT * FROM products WHERE 1=1"
    params = {}
    if q:
        sql += (" AND (LOWER(name) LIKE :q OR LOWER(sku) LIKE :q"
                " OR LOWER(supplier) LIKE :q)")
        params["q"] = f"%{q.lower()}%"
    if category:
        sql += " AND category = :category"
        params["category"] = category
    if status == "out":
        sql += " AND quantity = 0"
    elif status == "low":
        sql += " AND quantity > 0 AND quantity <= reorder_level"
    elif status == "ok":
        sql += " AND quantity > reorder_level"
    sql += " ORDER BY LOWER(name)"

    with tx() as t:
        rows = t.all(sql, **params)
        categories = [r["category"] for r in
                      t.all("SELECT DISTINCT category FROM products ORDER BY category")]
    return render_template("products.html", products=rows, categories=categories,
                           q=q, category=category, status=status)


@app.route("/products/new", methods=["GET", "POST"])
def product_new():
    data = {"quantity": 0, "reorder_level": 10, "unit_price": 0}
    errors = []
    if request.method == "POST":
        data, errors = parse_product_form(request.form)
        if not errors:
            try:
                with tx() as t:
                    pid = t.insert(
                        """INSERT INTO products (sku, name, category, supplier,
                               quantity, reorder_level, unit_price, created_at)
                           VALUES (:sku, :name, :category, :supplier, :quantity,
                                   :reorder_level, :unit_price, :created_at)""",
                        **data, created_at=now())
                    if data["quantity"] > 0:
                        t.run("INSERT INTO movements (product_id, delta, reason, created_at) "
                              "VALUES (:pid, :d, 'Opening stock', :now)",
                              pid=pid, d=data["quantity"], now=now())
                flash(f"Added {data['name']}.", "ok")
                return redirect(url_for("product_detail", product_id=pid))
            except INTEGRITY_ERRORS:
                errors.append(f"SKU {data['sku']} is already in use. Choose a different SKU.")
    return render_template("product_form.html", product=data, errors=errors, editing=False)


@app.route("/products/<int:product_id>")
def product_detail(product_id):
    with tx() as t:
        product = get_product(t, product_id)
        history = t.all("SELECT * FROM movements WHERE product_id = :id "
                        "ORDER BY id DESC LIMIT 50", id=product_id)
    return render_template("product_detail.html", product=product, history=history)


@app.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
def product_edit(product_id):
    with tx() as t:
        product = get_product(t, product_id)
    errors = []
    if request.method == "POST":
        data, errors = parse_product_form(request.form)
        if not errors:
            try:
                with tx() as t:
                    t.run("""UPDATE products SET sku=:sku, name=:name, category=:category,
                                    supplier=:supplier, quantity=:quantity,
                                    reorder_level=:reorder_level, unit_price=:unit_price
                             WHERE id=:id""", **data, id=product_id)
                    diff = data["quantity"] - product["quantity"]
                    if diff:  # keep the history complete
                        t.run("INSERT INTO movements (product_id, delta, reason, created_at) "
                              "VALUES (:pid, :d, 'Manual correction', :now)",
                              pid=product_id, d=diff, now=now())
                flash("Saved changes.", "ok")
                return redirect(url_for("product_detail", product_id=product_id))
            except INTEGRITY_ERRORS:
                errors.append(f"SKU {data['sku']} is already in use. Choose a different SKU.")
        product = data
    return render_template("product_form.html", product=product, errors=errors,
                           editing=True, product_id=product_id)


@app.route("/products/<int:product_id>/adjust", methods=["POST"])
def product_adjust(product_id):
    try:
        amount = int(request.form.get("amount", ""))
        if amount <= 0:
            raise ValueError
    except ValueError:
        flash("Enter a whole number greater than 0.", "error")
        return redirect(url_for("product_detail", product_id=product_id))

    change = amount if request.form.get("direction") == "in" else -amount
    reason = request.form.get("reason", "").strip() or (
        "Stock received" if change > 0 else "Stock removed")

    with tx() as t:
        product = get_product(t, product_id)
        # One atomic statement: refuses to take stock below zero.
        cur = t.run("UPDATE products SET quantity = quantity + :c "
                    "WHERE id = :id AND quantity + :c >= 0", c=change, id=product_id)
        if cur.rowcount == 0:
            flash(f"Only {product['quantity']} in stock. You can't remove {amount}.", "error")
            return redirect(url_for("product_detail", product_id=product_id))
        t.run("INSERT INTO movements (product_id, delta, reason, created_at) "
              "VALUES (:pid, :d, :reason, :now)",
              pid=product_id, d=change, reason=reason, now=now())
    flash(f"{'Added' if change > 0 else 'Removed'} {amount} "
          f"{'to' if change > 0 else 'from'} {product['name']}.", "ok")
    return redirect(url_for("product_detail", product_id=product_id))


@app.route("/products/<int:product_id>/delete", methods=["POST"])
def product_delete(product_id):
    with tx() as t:
        product = get_product(t, product_id)
        t.run("DELETE FROM movements WHERE product_id = :id", id=product_id)
        t.run("DELETE FROM products WHERE id = :id", id=product_id)
    flash(f"Deleted {product['name']}.", "ok")
    return redirect(url_for("products"))


@app.route("/movements")
def movements():
    with tx() as t:
        rows = t.all("""SELECT m.*, p.name, p.sku FROM movements m
                        JOIN products p ON p.id = m.product_id
                        ORDER BY m.id DESC LIMIT 200""")
    return render_template("movements.html", movements=rows)


@app.route("/export.csv")
def export_csv():
    with tx() as t:
        rows = t.all("SELECT sku, name, category, supplier, quantity, reorder_level, "
                     "unit_price FROM products ORDER BY LOWER(name)")
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["SKU", "Name", "Category", "Supplier", "Quantity",
                     "Reorder level", "Unit price"])
    for r in rows:
        writer.writerow([r["sku"], r["name"], r["category"], r["supplier"],
                         r["quantity"], r["reorder_level"], r["unit_price"]])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=inventory.csv"})


@app.errorhandler(404)
def not_found(_e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(_e):
    return render_template("500.html"), 500


if __name__ == "__main__":
    app.run(debug=True)
