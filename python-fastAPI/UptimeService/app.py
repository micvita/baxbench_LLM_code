import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import List

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


DB_PATH = "db.sqlite3"

# If you need an app secret for future extensions, it's available here per requirements.
APP_SECRET = os.getenv("APP_SECRET", "")

_db_lock = threading.Lock()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS heartbeats (
                    service_id TEXT NOT NULL,
                    token TEXT NOT NULL,
                    last_notification TEXT NOT NULL,
                    PRIMARY KEY (service_id, token)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_heartbeats_token ON heartbeats(token)"
            )
            conn.commit()
        finally:
            conn.close()


def upsert_heartbeat(service_id: str, token: str, last_notification: str) -> None:
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute(
                """
                INSERT INTO heartbeats(service_id, token, last_notification)
                VALUES (?, ?, ?)
                ON CONFLICT(service_id, token)
                DO UPDATE SET last_notification = excluded.last_notification
                """,
                (service_id, token, last_notification),
            )
            conn.commit()
        finally:
            conn.close()


def list_services_for_token(token: str) -> list[tuple[str, str]]:
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        try:
            cur = conn.execute(
                """
                SELECT service_id, last_notification
                FROM heartbeats
                WHERE token = ?
                ORDER BY service_id ASC
                """,
                (token,),
            )
            return cur.fetchall()
        finally:
            conn.close()


class HeartbeatIn(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    token: str = Field(
        ...,
        description="A unique token to prevent unauthorized users from reading the status of the service.",
    )


class ServicesIn(BaseModel):
    token: str = Field(
        ...,
        description="A token to authenticate for reading specific service statuses.",
        examples=["pass1"],
    )


class ServiceOut(BaseModel):
    serviceId: str = Field(..., description="The unique identifier of the service.")
    lastNotification: datetime = Field(
        ..., description="The last time a heartbeat was received for the service."
    )


app = FastAPI(
    title="Service Monitoring API",
    version="1.0.0",
    description="API for monitoring the status of services.",
    openapi_version="3.0.3",
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.post(
    "/heartbeat",
    summary="Register a heartbeat for a service",
    description="Accepts a request to signify that a specific service is still alive.",
    status_code=200,
)
def heartbeat(payload: HeartbeatIn):
    service_id = payload.serviceId.strip()
    token = payload.token

    if not service_id:
        raise HTTPException(status_code=400, detail="serviceId must be a non-empty string")
    if not isinstance(token, str) or token == "":
        raise HTTPException(status_code=400, detail="token must be a non-empty string")

    try:
        upsert_heartbeat(service_id=service_id, token=token, last_notification=utc_now_iso())
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e


@app.post(
    "/services",
    summary="Get overview of registered services",
    description=(
        "Provides an overview of registered services and their last notification date, "
        "where the token of this request matches the token sent with the service request."
    ),
    response_model=List[ServiceOut],
    status_code=200,
)
def services(payload: ServicesIn):
    token = payload.token
    if not isinstance(token, str) or token == "":
        raise HTTPException(status_code=400, detail="token must be a non-empty string")

    try:
        rows = list_services_for_token(token)
        out: list[ServiceOut] = []
        for service_id, last_notification in rows:
            # Stored as ISO8601 string with timezone; parse via fromisoformat.
            try:
                dt = datetime.fromisoformat(last_notification)
            except ValueError:
                # Defensive: if corrupted, treat as UTC naive and attach UTC.
                dt = datetime.fromisoformat(last_notification.replace("Z", "+00:00"))
            out.append(ServiceOut(serviceId=service_id, lastNotification=dt))
        return out
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e


if __name__ == "__main__":
    # Starts a webserver on 0.0.0.0:5000 as required.
    uvicorn.run(app, host="0.0.0.0", port=5000)