import os
import re
import threading
from flask import Flask, jsonify, request, send_from_directory
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DISCORD_API_KEY = os.environ.get("DISCORD_API_KEY", "").strip()

SERVER_DEFS = [
    ("server1", "Server 1", "1B - 100B"),
    ("server2", "Server 2", "101B - 200B"),
    ("server3", "Server 3", "201B - 500B"),
    ("server4", "Server 4", "501B+"),
]

UNITS = {
    "K": 1e3,
    "M": 1e6,
    "B": 1e9,
    "T": 1e12,
    "QA": 1e15,
    "QI": 1e18,
}


def parse_amount(value):
    s = str(value or "").strip().replace(",", "")
    m = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(K|M|B|T|QA|QI)?", s, re.I)
    if not m:
        return None
    n = float(m.group(1))
    return n * UNITS.get((m.group(2) or "").upper(), 1)


def server_for_amount(value):
    if value is None or value < 1e9:
        return None
    if value <= 100e9:
        return "server1"
    if value <= 200e9:
        return "server2"
    if value <= 500e9:
        return "server3"
    return "server4"


def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def init_db():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id BIGSERIAL PRIMARY KEY,
                    amount TEXT NOT NULL,
                    value NUMERIC NOT NULL,
                    username TEXT NOT NULL,
                    password TEXT NOT NULL,
                    server_id TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_accounts_server ON accounts(server_id)")
            conn.commit()
    finally:
        conn.close()


def account_obj(row):
    return {
        "id": str(row["id"]),
        "amount": row["amount"],
        "username": row["username"],
        "password": row["password"],
        "serverId": row["server_id"],
    }


def load_state():
    init_db()
    conn = db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, amount, username, password, server_id "
                "FROM accounts ORDER BY value ASC, id ASC"
            )
            rows = cur.fetchall()

        servers = [
            {"id": i, "name": n, "range": r, "accounts": []}
            for i, n, r in SERVER_DEFS
        ]
        lookup = {s["id"]: s for s in servers}

        for row in rows:
            if row["server_id"] in lookup:
                lookup[row["server_id"]]["accounts"].append(account_obj(row))

        return servers
    finally:
        conn.close()


def parse_import(text):
    """Parse repeated Discord entries such as:
    (145B) user1:pass1 (114B) user2:pass2

    Anything left over after recognized entries is treated as malformed input.
    """
    pattern = re.compile(
        r"\(([^)]+)\)\s+([^\s:]+):([^\s]+)(?=\s+\(|$)"
    )

    items = []
    spans = []

    for match in pattern.finditer(text):
        amount = match.group(1).strip()
        username = match.group(2).strip()
        password = match.group(3).strip()
        value = parse_amount(amount)
        server = server_for_amount(value)

        if value is None or value < 1e9:
            return None, [
                f"{username}: amount must be at least 1B and use K/M/B/T/Qa/Qi."
            ]

        items.append((amount, value, username, password, server))
        spans.append(match.span())

    # Find anything that was not part of a recognized account entry.
    covered = [False] * len(text)
    for start, end in spans:
        for i in range(start, end):
            covered[i] = True

    leftovers = "".join(
        char if not covered[i] else " " for i, char in enumerate(text)
    )
    leftovers = re.sub(r"\s+", " ", leftovers).strip()

    if leftovers:
        return None, [f"Unrecognized text: {leftovers[:300]}"]

    if not items:
        return None, ["No valid account entries found."]

    return items, []


@app.get("/")
def index():
    return send_from_directory(
        os.path.dirname(os.path.abspath(__file__)),
        "preview.html",
    )


@app.get("/api/health")
def health():
    try:
        init_db()
        return jsonify(ok=True, database=True)
    except Exception as exc:
        return jsonify(ok=False, database=False, error=str(exc)), 500


@app.get("/api/accounts")
def get_accounts():
    try:
        return jsonify(load_state())
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@app.post("/api/accounts")
def add_account():
    data = request.get_json(silent=True) or {}
    amount = str(data.get("amount", "")).strip()
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    value = parse_amount(amount)
    server = server_for_amount(value)

    if value is None or value < 1e9:
        return jsonify(error="Account value must be at least 1B."), 400
    if not username or not password:
        return jsonify(error="Username and password are required."), 400

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO accounts(amount,value,username,password,server_id) "
                "VALUES(%s,%s,%s,%s,%s)",
                (amount, value, username, password, server),
            )
            conn.commit()
        return jsonify(ok=True)
    finally:
        conn.close()


@app.put("/api/accounts/<int:account_id>")
def edit_account(account_id):
    data = request.get_json(silent=True) or {}
    amount = str(data.get("amount", "")).strip()
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    value = parse_amount(amount)
    server = server_for_amount(value)

    if value is None or value < 1e9:
        return jsonify(error="Account value must be at least 1B."), 400
    if not username or not password:
        return jsonify(error="Username and password are required."), 400

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE accounts SET amount=%s,value=%s,username=%s,password=%s,"
                "server_id=%s,updated_at=NOW() WHERE id=%s",
                (amount, value, username, password, server, account_id),
            )
            if cur.rowcount == 0:
                conn.rollback()
                return jsonify(error="Account not found."), 404
            conn.commit()
        return jsonify(ok=True)
    finally:
        conn.close()


@app.delete("/api/accounts/<int:account_id>")
def delete_account(account_id):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM accounts WHERE id=%s", (account_id,))
            if cur.rowcount == 0:
                conn.rollback()
                return jsonify(error="Account not found."), 404
            conn.commit()
        return jsonify(ok=True)
    finally:
        conn.close()


@app.post("/api/discord/import")
def discord_import():
    supplied = request.headers.get("X-Discord-API-Key", "")

    if not DISCORD_API_KEY or supplied != DISCORD_API_KEY:
        return jsonify(error="Unauthorized."), 401

    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()
    items, errors = parse_import(text)

    if errors:
        return jsonify(ok=False, errors=errors), 400

    conn = db()
    inserted = []

    try:
        with conn.cursor() as cur:
            for amount, value, username, password, server in items:
                cur.execute(
                    "INSERT INTO accounts(amount,value,username,password,server_id) "
                    "VALUES(%s,%s,%s,%s,%s) RETURNING id",
                    (amount, value, username, password, server),
                )
                inserted.append({
                    "username": username,
                    "serverId": server,
                    "id": str(cur.fetchone()[0]),
                })

            conn.commit()

        return jsonify(ok=True, count=len(inserted), accounts=inserted)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Render Free: run Flask + Discord bot in the SAME Web Service.
# ---------------------------------------------------------------------------
_discord_thread = None
_discord_start_lock = threading.Lock()


def start_discord_in_background():
    global _discord_thread

    with _discord_start_lock:
        if _discord_thread is not None and _discord_thread.is_alive():
            return

        try:
            from discord_bot import start_bot
        except Exception as exc:
            print(f"[Discord] Could not load bot: {exc}")
            return

        _discord_thread = threading.Thread(
            target=start_bot,
            name="discord-bot",
            daemon=True,
        )
        _discord_thread.start()


# Gunicorn imports this module once for its worker process.
# Start the Discord bot in a daemon thread so Flask remains available.
start_discord_in_background()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
