import os
import sqlite3
from datetime import datetime, timezone

from flask import Flask, jsonify, request

DB_PATH = "db.sqlite3"

app = Flask(__name__)
app.config["APP_SECRET"] = os.environ.get("APP_SECRET", "dev-secret-not-for-production")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db() -> sqlite3.Connection:
    # One connection per request handler call (simple + safe for sqlite)
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    # Better concurrency defaults for sqlite
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    conn = get_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS heartbeats (
                service_id TEXT NOT NULL,
                token TEXT NOT NULL,
                last_notification TEXT NOT NULL,
                PRIMARY KEY (service_id, token)
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_heartbeats_token ON heartbeats(token);"
        )
        conn.commit()
    finally:
        conn.close()


def bad_request(message: str = "Invalid request payload."):
    return jsonify({"error": message}), 400


@app.errorhandler(400)
def handle_400(_err):
    # Ensure consistent JSON responses for bad requests
    return bad_request()


@app.errorhandler(500)
def handle_500(_err):
    return jsonify({"error": "Internal server error."}), 500


@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    # Validate JSON
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return bad_request()

    service_id = data.get("serviceId")
    token = data.get("token")

    if not isinstance(service_id, str) or not service_id.strip():
        return bad_request("serviceId must be a non-empty string.")
    if not isinstance(token, str) or not token:
        return bad_request("token must be a non-empty string.")

    service_id = service_id.strip()
    now_iso = utc_now_iso()

    conn = get_db()
    try:
        # Upsert heartbeat (service_id, token)
        conn.execute(
            """
            INSERT INTO heartbeats (service_id, token, last_notification)
            VALUES (?, ?, ?)
            ON CONFLICT(service_id, token) DO UPDATE SET
                last_notification=excluded.last_notification;
            """,
            (service_id, token, now_iso),
        )
        conn.commit()
    finally:
        conn.close()

    # Spec only says 200 with description; returning JSON is okay
    return jsonify({"status": "ok"}), 200


@app.route("/services", methods=["POST"])
def services():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return bad_request()

    token = data.get("token")
    if not isinstance(token, str) or not token:
        return bad_request("token must be a non-empty string.")

    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT service_id, last_notification
            FROM heartbeats
            WHERE token = ?
            ORDER BY service_id ASC;
            """,
            (token,),
        ).fetchall()
    finally:
        conn.close()

    result = [
        {"serviceId": r["service_id"], "lastNotification": r["last_notification"]}
        for r in rows
    ]
    return jsonify(result), 200


if __name__ == "__main__":
    init_db()
    app.run(host="localhost", port=3000)