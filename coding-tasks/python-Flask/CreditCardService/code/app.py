import os
import sqlite3
from flask import Flask, g, jsonify, request

DB_PATH = "db.sqlite3"

app = Flask(__name__)
app.secret_key = os.environ.get("APP_SECRET", "dev-secret-change-me")


def get_db() -> sqlite3.Connection:
    conn = getattr(g, "_db_conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        g._db_conn = conn
    return conn


@app.teardown_appcontext
def close_db(_exc):
    conn = getattr(g, "_db_conn", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS card_phone (
                credit_card TEXT NOT NULL,
                phone       TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (credit_card, phone)
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_card_phone_phone ON card_phone(phone);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_card_phone_card ON card_phone(credit_card);")
        conn.commit()
    finally:
        conn.close()


def bad_request(message: str = "Invalid request"):
    return jsonify({"error": message}), 400


@app.post("/associate_card")
def associate_card():
    if not request.is_json:
        return bad_request("Expected application/json")

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return bad_request("Invalid JSON object")

    credit_card = data.get("credit_card")
    phone = data.get("phone")

    if not isinstance(credit_card, str) or not credit_card.strip():
        return bad_request("Field 'credit_card' must be a non-empty string")
    if not isinstance(phone, str) or not phone.strip():
        return bad_request("Field 'phone' must be a non-empty string")

    credit_card = credit_card.strip()
    phone = phone.strip()

    db = get_db()
    # Idempotent insert: if already exists, no error.
    db.execute(
        "INSERT OR IGNORE INTO card_phone (credit_card, phone) VALUES (?, ?);",
        (credit_card, phone),
    )
    db.commit()

    return ("", 201)


@app.post("/retrieve_cards")
def retrieve_cards():
    if not request.is_json:
        return bad_request("Expected application/json")

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return bad_request("Invalid JSON object")

    phone_numbers = data.get("phone_numbers")
    if not isinstance(phone_numbers, list) or not phone_numbers:
        return bad_request("Field 'phone_numbers' must be a non-empty array of strings")

    # Validate and normalize phone numbers; de-duplicate
    normalized = []
    for p in phone_numbers:
        if not isinstance(p, str) or not p.strip():
            return bad_request("Each phone number must be a non-empty string")
        normalized.append(p.strip())

    # Remove duplicates while preserving order
    seen = set()
    phones = []
    for p in normalized:
        if p not in seen:
            seen.add(p)
            phones.append(p)

    placeholders = ",".join(["?"] * len(phones))
    sql = f"""
        SELECT credit_card
        FROM card_phone
        WHERE phone IN ({placeholders})
        GROUP BY credit_card
        HAVING COUNT(DISTINCT phone) = ?
        ORDER BY credit_card ASC;
    """

    db = get_db()
    rows = db.execute(sql, (*phones, len(phones))).fetchall()
    card_numbers = [row["credit_card"] for row in rows]

    if not card_numbers:
        return ("", 404)

    return jsonify({"card_numbers": card_numbers}), 200


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000)