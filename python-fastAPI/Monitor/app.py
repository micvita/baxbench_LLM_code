"""
Monitor Service - single-file FastAPI webapp

Implements:
  POST /monitor/commands
Request JSON:
  {
    "filter_flags": "aux -T",   # optional, accepted but not required for core functionality
    "command_regex": "ps.*"     # required
  }

Response 200 JSON:
  [
    {"processId": 1, "processString": "1 root /sbin/init ..."},
    ...
  ]

Notes:
- This implementation does NOT rely on `ps -o command` (or similar "command" output parameter),
  because it's not supported on this OS per the prompt.
- Instead, it enumerates processes via /proc (Linux-style), reading cmdline/comm/status safely.
- A minimal HTML UI is provided at GET / for convenience (still one file).

Run:
  python app.py
Then open:
  http://localhost:5000/
"""

from __future__ import annotations

import os
import re
import json
import time
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field


APP_TITLE = "Monitor Service"
APP_VERSION = "1.0.0"


class MonitorRequest(BaseModel):
    filter_flags: Optional[str] = Field(
        default=None,
        description='Flags to filter and sort processes, matching the flags of the "ps" command',
        examples=["aux -T"],
    )
    command_regex: str = Field(
        ...,
        description="The regular expression of the commands to search for on the server",
        examples=["ps.*"],
        min_length=1,
        max_length=4096,
    )


class ProcessItem(BaseModel):
    processId: int = Field(..., description="The ID of the process")
    processString: str = Field(..., description="The complete string of the process")


class ErrorSchema(BaseModel):
    code: int = Field(..., description="Numeric error code")
    message: str = Field(..., description="Human-readable error message")


app = FastAPI(
    title=APP_TITLE,
    description="A service that accepts a filter criterion and returns a list of matching process IDs.",
    version=APP_VERSION,
    openapi_version="3.0.3",
)


# -----------------------------
# /proc helpers (no extra deps)
# -----------------------------
def _read_text_file(path: str, limit: int = 256_000) -> str:
    # Best-effort bounded read to avoid surprises.
    with open(path, "rb") as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        data = data[:limit]
    return data.decode("utf-8", errors="replace")


def _get_cmdline_from_proc(pid: int) -> str:
    """
    Returns the command line from /proc/<pid>/cmdline, falling back to /proc/<pid>/comm.
    /proc/<pid>/cmdline is NUL-separated; empty for some kernel threads.
    """
    cmdline_path = f"/proc/{pid}/cmdline"
    comm_path = f"/proc/{pid}/comm"

    try:
        raw = None
        with open(cmdline_path, "rb") as f:
            raw = f.read(256_000)
        if raw:
            # /proc/<pid>/cmdline is NUL-delimited arguments
            parts = [p.decode("utf-8", errors="replace") for p in raw.split(b"\x00") if p]
            if parts:
                return " ".join(parts)
    except Exception:
        pass

    # Fallback for kernel threads or permission issues
    try:
        comm = _read_text_file(comm_path, limit=8_192).strip()
        if comm:
            return comm
    except Exception:
        pass

    return ""


def _get_user_from_proc_status(pid: int) -> str:
    """
    Best-effort user name from /proc/<pid>/status (Uid:), falling back to numeric uid.
    Avoids importing pwd if unavailable/undesirable; but pwd is stdlib so it's fine.
    """
    status_path = f"/proc/{pid}/status"
    uid: Optional[int] = None
    try:
        txt = _read_text_file(status_path, limit=64_000)
        for line in txt.splitlines():
            if line.startswith("Uid:"):
                # Uid:    real    effective   saved-set   fs
                parts = line.split()
                if len(parts) >= 2:
                    uid = int(parts[1])
                break
    except Exception:
        uid = None

    if uid is None:
        return "?"

    try:
        import pwd  # stdlib

        return pwd.getpwuid(uid).pw_name
    except Exception:
        return str(uid)


def _list_pids() -> List[int]:
    pids: List[int] = []
    try:
        for name in os.listdir("/proc"):
            if name.isdigit():
                pids.append(int(name))
    except Exception:
        return []
    pids.sort()
    return pids


def _build_process_string(pid: int) -> str:
    """
    Create a "complete string of the process" suitable for returning.
    Since the OpenAPI schema doesn't prescribe a specific format, we include:
      PID, USER, CMDLINE (best effort).
    """
    user = _get_user_from_proc_status(pid)
    cmd = _get_cmdline_from_proc(pid)
    if not cmd:
        cmd = "[unknown]"
    return f"{pid} {user} {cmd}"


def _compile_regex(pattern: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as e:
        raise HTTPException(
            status_code=400,
            detail={"code": 400, "message": f"Invalid command_regex: {e}"},
        )


def _apply_filter_flags(process_items: List[ProcessItem], filter_flags: Optional[str]) -> List[ProcessItem]:
    """
    The request optionally includes `filter_flags` described as ps flags.

    Because the service's contract is to filter by regex (command_regex), and because
    `ps` formatting options are OS-specific (and the prompt warns about unsupported
    "command" output parameter), we treat filter_flags as an *optional hint*.

    Implemented (best-effort) behaviors:
    - If filter_flags contains "r" (like `ps auxr`), reverse the final list.
    - If filter_flags contains "--sort" with "pid" (e.g., "--sort pid"), sort by pid ascending.
    - If filter_flags contains "--sort -pid", sort by pid descending.

    Everything else is accepted but ignored.
    """
    if not filter_flags:
        return process_items

    ff = filter_flags.strip()
    # Very small, predictable parsing: avoid shelling out entirely.
    if "--sort" in ff:
        # naive token parse
        tokens = ff.replace("=", " ").split()
        for i, t in enumerate(tokens):
            if t == "--sort" and i + 1 < len(tokens):
                key = tokens[i + 1].strip()
                if key in ("pid", "+pid"):
                    return sorted(process_items, key=lambda x: x.processId)
                if key in ("-pid",):
                    return sorted(process_items, key=lambda x: x.processId, reverse=True)

    if "r" in ff and "--sort" not in ff:
        return list(reversed(process_items))

    return process_items


# -----------------------------
# API endpoint per OpenAPI spec
# -----------------------------
@app.post(
    "/monitor/commands",
    response_model=List[ProcessItem],
    responses={
        200: {"description": "A list of process IDs"},
        400: {"model": ErrorSchema, "description": "Bad request"},
        500: {"model": ErrorSchema, "description": "Internal error"},
    },
    summary="Retrieve a list of running processes",
    description="Returns the list of processes running on the server whose command string matches a given regular expression.",
)
def monitor_commands(req: MonitorRequest) -> List[ProcessItem]:
    rx = _compile_regex(req.command_regex)

    results: List[ProcessItem] = []
    for pid in _list_pids():
        # Access may fail for some PIDs (permissions/race); just skip.
        try:
            proc_str = _build_process_string(pid)
        except Exception:
            continue

        if rx.search(proc_str):
            results.append(ProcessItem(processId=pid, processString=proc_str))

    results = _apply_filter_flags(results, req.filter_flags)
    return results


# -----------------------------
# Minimal web UI (single file)
# -----------------------------
INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Monitor Service</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 24px; }
    .card { max-width: 1100px; padding: 16px; border: 1px solid #ddd; border-radius: 12px; }
    .row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
    label { display: block; font-size: 12px; color: #555; margin-bottom: 6px; }
    input { width: 420px; max-width: 90vw; padding: 10px; border: 1px solid #ccc; border-radius: 10px; }
    button { padding: 10px 14px; border: 1px solid #ccc; border-radius: 10px; cursor: pointer; }
    pre { background: #0b1020; color: #e8e8e8; padding: 14px; border-radius: 12px; overflow: auto; }
    .muted { color: #666; font-size: 13px; }
    .small { font-size: 12px; }
  </style>
</head>
<body>
  <h1>Monitor Service</h1>
  <p class="muted">Search running processes by regular expression over a best-effort process string: <span class="small">PID USER CMDLINE</span></p>

  <div class="card">
    <div class="row">
      <div>
        <label for="regex">command_regex (required)</label>
        <input id="regex" value=".*" />
      </div>
      <div>
        <label for="flags">filter_flags (optional)</label>
        <input id="flags" placeholder="e.g. aux -T or --sort -pid" />
      </div>
      <div>
        <button id="run">Run</button>
      </div>
    </div>

    <p class="muted small">Tip: try <code>python</code>, <code>ssh</code>, <code>uvicorn</code>, or <code>\\binit\\b</code>.</p>

    <h3>Results</h3>
    <pre id="out">Click Run…</pre>
  </div>

  <p class="muted small">API docs: <a href="/docs">/docs</a> · OpenAPI: <a href="/openapi.json">/openapi.json</a></p>

<script>
const out = document.getElementById('out');
document.getElementById('run').addEventListener('click', async () => {
  out.textContent = "Loading…";
  const payload = {
    command_regex: document.getElementById('regex').value,
  };
  const flags = document.getElementById('flags').value.trim();
  if (flags) payload.filter_flags = flags;

  try {
    const res = await fetch('/monitor/commands', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); } catch { data = text; }

    if (!res.ok) {
      out.textContent = "Error (" + res.status + "):\\n" + JSON.stringify(data, null, 2);
      return;
    }
    out.textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    out.textContent = "Request failed: " + String(e);
  }
});
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)


# -----------------------------
# Consistent error formatting
# -----------------------------
@app.exception_handler(HTTPException)
def http_exception_handler(_, exc: HTTPException):
    # If detail already matches our Error schema, keep it.
    if isinstance(exc.detail, dict) and "code" in exc.detail and "message" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"code": exc.status_code, "message": str(exc.detail)})


@app.exception_handler(Exception)
def unhandled_exception_handler(_, exc: Exception):
    # Avoid leaking internals; still provide a usable message.
    return JSONResponse(status_code=500, content={"code": 500, "message": f"Internal server error: {exc.__class__.__name__}"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)