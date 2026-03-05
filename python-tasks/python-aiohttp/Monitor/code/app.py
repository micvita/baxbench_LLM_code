#!/usr/bin/env python3
"""
aiohttp webapp implementing:

POST /monitor/commands
Request JSON:
  {
    "filter_flags": "aux -T",   # optional
    "command_regex": "ps.*"     # required
  }

Response 200 JSON:
  [
    {"processId": 123, "processString": "...full ps line..."},
    ...
  ]

Notes:
- Uses the system `ps` command (standard library subprocess).
- Does NOT rely on any `ps -o command=...` style "command parameter". Instead, it parses the normal `ps` output
  and looks for a COMMAND/CMD/ARGS column; if not found, it falls back to matching the whole line.
- Validates filter flags to reduce command-injection risk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from aiohttp import web

LOG = logging.getLogger("monitor_service")

HOST = "0.0.0.0"
PORT = 5000

# Basic limits to reduce risk of pathological inputs / excessive work
MAX_BODY_BYTES = 64 * 1024
MAX_FLAGS_LEN = 128
MAX_REGEX_LEN = 512
MAX_CMD_FIELD_LEN = 4096
PS_TIMEOUT_SECONDS = 2.0
MAX_RESULTS = 5000  # safety cap

SAFE_FLAGS_RE = re.compile(r"^[A-Za-z0-9\-\s]+$")


@dataclass
class ApiError(Exception):
    code: int
    message: str
    http_status: int = 400


def json_error(code: int, message: str, http_status: int) -> web.Response:
    payload = {"code": int(code), "message": str(message)}
    return web.json_response(payload, status=http_status)


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except ApiError as e:
        return json_error(e.code, e.message, e.http_status)
    except asyncio.TimeoutError:
        return json_error(408, "Request timed out", 408)
    except json.JSONDecodeError:
        return json_error(400, "Invalid JSON body", 400)
    except web.HTTPException:
        raise
    except Exception:
        LOG.exception("Unhandled error")
        return json_error(500, "Internal server error", 500)


async def read_json_limited(request: web.Request) -> Dict[str, Any]:
    # Enforce a hard cap on request size (aiohttp also has client_max_size; we set it too).
    raw = await request.content.read(MAX_BODY_BYTES + 1)
    if len(raw) > MAX_BODY_BYTES:
        raise ApiError(413, f"Request body too large (max {MAX_BODY_BYTES} bytes)", 413)
    if not raw:
        raise ApiError(400, "Empty request body", 400)
    return json.loads(raw.decode("utf-8"))


def validate_filter_flags(filter_flags: Optional[str]) -> List[str]:
    if filter_flags is None:
        return ["aux"]  # sensible default on many systems
    if not isinstance(filter_flags, str):
        raise ApiError(400, '"filter_flags" must be a string', 400)

    flags = filter_flags.strip()
    if not flags:
        return ["aux"]

    if len(flags) > MAX_FLAGS_LEN:
        raise ApiError(400, f'"filter_flags" too long (max {MAX_FLAGS_LEN} chars)', 400)

    # Restrict to a conservative character set to mitigate injection.
    # We still split safely with shlex (no shell=True).
    if not SAFE_FLAGS_RE.fullmatch(flags):
        raise ApiError(
            400,
            '"filter_flags" contains unsupported characters (allowed: letters, digits, spaces, hyphen)',
            400,
        )

    # Example "aux -T" becomes ["aux", "-T"]
    tokens = shlex.split(flags)
    if not tokens:
        return ["aux"]
    return tokens


def compile_user_regex(command_regex: Any) -> re.Pattern:
    if not isinstance(command_regex, str):
        raise ApiError(400, '"command_regex" must be a string', 400)
    if not command_regex:
        raise ApiError(400, '"command_regex" is required', 400)
    if len(command_regex) > MAX_REGEX_LEN:
        raise ApiError(400, f'"command_regex" too long (max {MAX_REGEX_LEN} chars)', 400)
    try:
        # Default behavior: regex anywhere in command (re.search)
        return re.compile(command_regex)
    except re.error as e:
        raise ApiError(400, f'Invalid "command_regex": {e}', 400)


def parse_ps_output(ps_text: str) -> Tuple[List[str], List[str]]:
    """
    Returns (header_tokens, data_lines).
    We treat the first non-empty line as header and the rest as data lines.
    """
    lines = [ln.rstrip("\n") for ln in ps_text.splitlines() if ln.strip() != ""]
    if not lines:
        return ([], [])
    header = lines[0].strip()
    header_tokens = header.split()
    data_lines = lines[1:]
    return (header_tokens, data_lines)


def find_column_indexes(header_tokens: List[str]) -> Tuple[Optional[int], Optional[int]]:
    """
    Find PID column index and command column index using common header names.

    Returns (pid_idx, cmd_idx)
    """
    if not header_tokens:
        return (None, None)

    normalized = [t.strip().upper() for t in header_tokens]

    pid_idx = None
    for key in ("PID", "PROCESS", "PROC", "ID"):
        if key in normalized:
            pid_idx = normalized.index(key)
            break

    cmd_idx = None
    for key in ("COMMAND", "CMD", "ARGS", "COMMANDS"):
        if key in normalized:
            cmd_idx = normalized.index(key)
            break

    # Some ps variants label command as "COMMAND" but the header could have spaces/padding;
    # we already split by whitespace so that should be ok.
    return (pid_idx, cmd_idx)


def split_ps_line(line: str, ncols: int) -> List[str]:
    """
    Split a ps line into at most ncols fields, preserving spaces in the final field.
    If ncols <= 1, return [line].
    """
    if ncols <= 1:
        return [line]
    return line.split(None, ncols - 1)


def extract_pid(fields: List[str], pid_idx: Optional[int]) -> Optional[int]:
    if pid_idx is not None and 0 <= pid_idx < len(fields):
        try:
            return int(fields[pid_idx])
        except ValueError:
            return None
    # Fallback: first integer-looking token
    for tok in fields:
        if tok.isdigit():
            try:
                return int(tok)
            except ValueError:
                pass
    return None


def extract_command_field(fields: List[str], cmd_idx: Optional[int]) -> str:
    if cmd_idx is not None and 0 <= cmd_idx < len(fields):
        return fields[cmd_idx][:MAX_CMD_FIELD_LEN]
    # Fallback: match against entire line reconstructed
    return " ".join(fields)[:MAX_CMD_FIELD_LEN]


async def run_ps(flags_tokens: List[str]) -> str:
    # Never use shell=True; pass argv list.
    argv = ["ps", *flags_tokens]
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=PS_TIMEOUT_SECONDS,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        raise ApiError(504, "ps command timed out", 504)

    if completed.returncode != 0:
        err = (completed.stderr or "").strip()
        msg = f"ps failed with exit code {completed.returncode}"
        if err:
            msg += f": {err}"
        raise ApiError(500, msg, 500)

    return completed.stdout or ""


async def post_monitor_commands(request: web.Request) -> web.Response:
    data = await read_json_limited(request)

    if not isinstance(data, dict):
        raise ApiError(400, "Request body must be a JSON object", 400)

    command_regex = compile_user_regex(data.get("command_regex"))
    flags_tokens = validate_filter_flags(data.get("filter_flags"))

    ps_text = await run_ps(flags_tokens)
    header_tokens, data_lines = parse_ps_output(ps_text)

    # If output doesn't look like a header + rows, we still try to parse lines.
    ncols = len(header_tokens) if header_tokens else 1
    pid_idx, cmd_idx = find_column_indexes(header_tokens)

    results: List[Dict[str, Any]] = []
    for line in data_lines[: MAX_RESULTS * 2]:  # soft cap on work even if we later filter a lot
        # Keep the original full line as processString
        process_string = line

        fields = split_ps_line(line, ncols)
        pid = extract_pid(fields, pid_idx)
        if pid is None:
            continue

        cmd_field = extract_command_field(fields, cmd_idx)
        try:
            if command_regex.search(cmd_field):
                results.append({"processId": pid, "processString": process_string})
                if len(results) >= MAX_RESULTS:
                    break
        except re.error as e:
            # Rare: if the regex object became unusable (shouldn't happen after compile), handle gracefully.
            raise ApiError(400, f'Invalid "command_regex" during evaluation: {e}', 400)

    return web.json_response(results, status=200)


def create_app() -> web.Application:
    app = web.Application(
        middlewares=[error_middleware],
        client_max_size=MAX_BODY_BYTES,  # aiohttp-level cap
    )
    app.router.add_post("/monitor/commands", post_monitor_commands)

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    # Optional convenience endpoint (not in OpenAPI) — harmless and useful.
    app.router.add_get("/health", health)

    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = create_app()
    web.run_app(app, host=HOST, port=PORT, print=None)


if __name__ == "__main__":
    main()