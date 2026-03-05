import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple

from flask import Flask, jsonify, request

app = Flask(__name__)


@dataclass
class ApiError(Exception):
    code: int
    message: str

    def to_dict(self):
        return {"code": int(self.code), "message": str(self.message)}


@app.errorhandler(ApiError)
def handle_api_error(err: ApiError):
    return jsonify(err.to_dict()), err.code


@app.errorhandler(Exception)
def handle_unexpected_error(err: Exception):
    # Avoid leaking internals; keep it predictable for clients.
    return jsonify({"code": 500, "message": "Internal server error"}), 500


def _run_ps(filter_flags: Optional[str]) -> str:
    """
    Runs `ps` with user-provided flags (split safely; no shell).
    Note: We do NOT use any unsupported `ps` "command" parameter.
    """
    base = ["ps"]
    if filter_flags and filter_flags.strip():
        # shlex.split keeps quoted segments together (still safe since shell=False)
        extra = shlex.split(filter_flags)
        cmd = base + extra
    else:
        # A reasonable default with wide availability
        cmd = base + ["aux"]

    try:
        # Use text mode for simpler parsing
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise ApiError(500, "`ps` command not found on this server")
    except Exception:
        raise ApiError(500, "Failed to execute `ps`")

    if proc.returncode != 0:
        # `ps` errors vary; return stderr as a 400 because flags are client-provided.
        msg = (proc.stderr or "").strip() or "Invalid ps flags"
        raise ApiError(400, f"`ps` failed: {msg}")

    return proc.stdout or ""


def _find_pid_index(header_line: str) -> Optional[int]:
    """
    Find the PID column index in the header line.
    Works for common ps formats (aux, -ef, etc).
    """
    tokens = header_line.strip().split()
    if not tokens:
        return None

    # Common header labels
    for label in ("PID", "Pid", "pid"):
        if label in tokens:
            return tokens.index(label)

    # Some ps variants may label it differently; heuristic: look for a token that equals "PID"
    # If nothing found, return None and we'll fall back to parsing by regex.
    return None


def _parse_ps_output(ps_text: str) -> Tuple[Optional[int], List[str]]:
    """
    Returns (pid_col_index, lines_without_header).
    If no header is present, pid_col_index may be None.
    """
    lines = [ln.rstrip("\n") for ln in (ps_text or "").splitlines() if ln.strip()]
    if not lines:
        return None, []

    header = lines[0]
    pid_idx = _find_pid_index(header)

    # If the first line looks like a header (contains PID), drop it.
    if pid_idx is not None:
        return pid_idx, lines[1:]

    # Some ps invocations might omit header; keep all lines.
    return None, lines


def _extract_pid(line: str, pid_idx: Optional[int]) -> Optional[int]:
    """
    Extract PID from a ps output line.
    - If pid_idx is known, split on whitespace and read that field.
    - Otherwise, use a conservative heuristic for common formats.
    """
    fields = line.split()
    if pid_idx is not None and pid_idx < len(fields):
        try:
            return int(fields[pid_idx])
        except Exception:
            return None

    # Heuristic fallback:
    # For `ps aux`, PID is typically the 2nd field (index 1): USER PID ...
    # For `ps -ef`, PID is typically the 2nd field (index 1): UID PID PPID ...
    if len(fields) >= 2:
        try:
            return int(fields[1])
        except Exception:
            pass

    # Last resort: find the first standalone integer token with reasonable length
    for tok in fields:
        if tok.isdigit():
            try:
                return int(tok)
            except Exception:
                return None
    return None


@app.get("/")
def index():
    return (
        "Monitor Service is running. Use POST /monitor/commands with JSON body "
        '{"filter_flags":"aux -T", "command_regex":"ps.*"}\n'
    )


@app.post("/monitor/commands")
def monitor_commands():
    if not request.is_json:
        raise ApiError(400, "Request body must be application/json")

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "JSON body must be an object")

    command_regex = data.get("command_regex", None)
    filter_flags = data.get("filter_flags", None)

    if command_regex is None or not isinstance(command_regex, str) or not command_regex.strip():
        raise ApiError(400, "`command_regex` is required and must be a non-empty string")
    if filter_flags is not None and not isinstance(filter_flags, str):
        raise ApiError(400, "`filter_flags` must be a string when provided")

    try:
        pattern = re.compile(command_regex)
    except re.error as e:
        raise ApiError(400, f"Invalid regular expression: {e}")

    ps_out = _run_ps(filter_flags)
    pid_idx, lines = _parse_ps_output(ps_out)

    results = []
    for line in lines:
        # Match against the complete process string (entire ps line),
        # as required by the schema.
        if pattern.search(line):
            pid = _extract_pid(line, pid_idx)
            if pid is None:
                # Skip lines where PID couldn't be parsed reliably
                continue
            results.append({"processId": pid, "processString": line})

    return jsonify(results), 200


if __name__ == "__main__":
    host = "localhost"
    port = 3000
    # Respect common env override but default to required port.
    port = int(os.environ.get("PORT", port))
    app.run(host=host, port=port)