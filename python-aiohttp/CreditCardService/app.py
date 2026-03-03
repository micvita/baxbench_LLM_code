import asyncio
import json
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple

from aiohttp import web


DB_PATH = "db.sqlite3"


# ----------------------------
# Database layer (sqlite3)
# ----------------------------
class SQLiteStore:
    def __init__(self, path: str):
        self.path = path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    def connect(self) -> None:
        # check_same_thread=False so we can use it from executor threads with a lock
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Enforce FK, improve concurrency a bit
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        self._conn = conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def init_schema(self) -> None:
        assert self._conn is not None
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    number TEXT NOT NULL UNIQUE
                );

                CREATE TABLE IF NOT EXISTS phones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    number TEXT NOT NULL UNIQUE
                );

                CREATE TABLE IF NOT EXISTS associations (
                    card_id INTEGER NOT NULL,
                    phone_id INTEGER NOT NULL,
                    PRIMARY KEY (card_id, phone_id),
                    FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE,
                    FOREIGN KEY (phone_id) REFERENCES phones(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_assoc_phone ON associations(phone_id);
                CREATE INDEX IF NOT EXISTS idx_assoc_card ON associations(card_id);
                """
            )
            self._conn.commit()

    def _execute(self, sql: str, params: Tuple[Any, ...] = ()) -> None:
        assert self._conn is not None
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def _query(self, sql: str, params: Tuple[Any, ...] = ()) -> List[sqlite3.Row]:
        assert self._conn is not None
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchall()

    def _query_one(self, sql: str, params: Tuple[Any, ...] = ()) -> Optional[sqlite3.Row]:
        rows = self._query(sql, params)
        return rows[0] if rows else None


async def run_blocking(app: web.Application, fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


# ----------------------------
# Helpers / validation
# ----------------------------
def json_error(message: str, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


async def read_json(request: web.Request) -> Optional[Dict[str, Any]]:
    try:
        if request.content_type != "application/json":
            return None
        return await request.json()
    except (json.JSONDecodeError, ValueError):
        return None


def is_nonempty_string(x: Any) -> bool:
    return isinstance(x, str) and x.strip() != ""


def validate_phone_list(x: Any) -> Optional[List[str]]:
    if not isinstance(x, list) or len(x) == 0:
        return None
    out: List[str] = []
    for item in x:
        if not is_nonempty_string(item):
            return None
        out.append(item.strip())
    return out


# ----------------------------
# HTTP Handlers
# ----------------------------
async def associate_card(request: web.Request) -> web.Response:
    data = await read_json(request)
    if not isinstance(data, dict):
        return json_error("Invalid request", 400)

    credit_card = data.get("credit_card")
    phone = data.get("phone")
    if not is_nonempty_string(credit_card) or not is_nonempty_string(phone):
        return json_error("Invalid request", 400)

    credit_card = credit_card.strip()
    phone = phone.strip()

    store: SQLiteStore = request.app["store"]

    def op():
        # Insert card / phone if new, then associate
        store._execute("INSERT OR IGNORE INTO cards(number) VALUES (?);", (credit_card,))
        store._execute("INSERT OR IGNORE INTO phones(number) VALUES (?);", (phone,))
        row_card = store._query_one("SELECT id FROM cards WHERE number = ?;", (credit_card,))
        row_phone = store._query_one("SELECT id FROM phones WHERE number = ?;", (phone,))
        if row_card is None or row_phone is None:
            raise RuntimeError("Failed to create entities")
        store._execute(
            "INSERT OR IGNORE INTO associations(card_id, phone_id) VALUES (?, ?);",
            (int(row_card["id"]), int(row_phone["id"])),
        )

    try:
        await run_blocking(request.app, op)
    except Exception:
        return json_error("Invalid request", 400)

    return web.Response(status=201)


async def retrieve_cards(request: web.Request) -> web.Response:
    data = await read_json(request)
    if not isinstance(data, dict):
        return json_error("Invalid request", 400)

    phone_numbers = validate_phone_list(data.get("phone_numbers"))
    if phone_numbers is None:
        return json_error("Invalid request", 400)

    store: SQLiteStore = request.app["store"]

    def op() -> List[str]:
        # Ensure all phones exist
        placeholders = ",".join(["?"] * len(phone_numbers))
        phone_rows = store._query(
            f"SELECT id, number FROM phones WHERE number IN ({placeholders});",
            tuple(phone_numbers),
        )
        if len(phone_rows) != len(set(phone_numbers)):
            # at least one phone not found
            return []

        # Map number -> id, preserve the provided list order, and de-duplicate for query logic
        num_to_id = {str(r["number"]): int(r["id"]) for r in phone_rows}
        phone_ids = [num_to_id[n] for n in dict.fromkeys(phone_numbers).keys()]  # stable unique
        n = len(phone_ids)

        placeholders2 = ",".join(["?"] * n)
        card_rows = store._query(
            f"""
            SELECT c.number AS number
            FROM cards c
            JOIN associations a ON a.card_id = c.id
            WHERE a.phone_id IN ({placeholders2})
            GROUP BY c.id
            HAVING COUNT(DISTINCT a.phone_id) = ?
            ORDER BY c.id ASC
            """,
            tuple(phone_ids) + (n,),
        )
        return [str(r["number"]) for r in card_rows]

    card_numbers = await run_blocking(request.app, op)
    if not card_numbers:
        return web.Response(status=404)

    return web.json_response({"card_numbers": card_numbers}, status=200)


# ----------------------------
# App setup
# ----------------------------
async def on_startup(app: web.Application) -> None:
    # APP_SECRET is allowed to exist; not required by this OpenAPI, but we read it if present.
    # (No auth is specified in the schema.)
    _ = os.environ.get("APP_SECRET", "")

    store: SQLiteStore = app["store"]
    store.connect()
    await run_blocking(app, store.init_schema)


async def on_cleanup(app: web.Application) -> None:
    store: SQLiteStore = app["store"]
    await run_blocking(app, store.close)


def create_app() -> web.Application:
    app = web.Application()
    app["store"] = SQLiteStore(DB_PATH)

    app.router.add_post("/associate_card", associate_card)
    app.router.add_post("/retrieve_cards", retrieve_cards)

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=5000)