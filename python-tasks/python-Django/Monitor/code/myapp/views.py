import json
import re
import shlex
import subprocess
from typing import List, Dict, Optional, Tuple

from django.http import JsonResponse, HttpRequest, HttpResponseNotAllowed
from django.views.decorators.csrf import csrf_exempt


def _error(status: int, message: str) -> JsonResponse:
    return JsonResponse({"code": status, "message": message}, status=status)


def _run_ps(filter_flags: Optional[str]) -> Tuple[int, str, str]:
    """
    Run `ps` with optional flags.

    We do NOT rely on `ps -o command=` or similar "command" parameters.
    We use whatever default output the given flags produce and parse PID
    from the header's PID column.

    Returns: (returncode, stdout, stderr)
    """
    args = ["ps"]

    if filter_flags:
        # Split like a shell would, but without shell=True (prevents injection).
        try:
            extra = shlex.split(filter_flags)
        except ValueError as e:
            return 2, "", f"Invalid filter_flags: {e}"
        args.extend(extra)
    else:
        # Reasonable default with command column present on most systems.
        args.extend(["aux"])

    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", "ps command not found on this system"
    except Exception as e:
        return 1, "", f"Failed to execute ps: {e}"


def _parse_ps_output(output: str) -> Tuple[Optional[int], List[str]]:
    """
    Parse `ps` output to locate the PID column index and return data lines.

    Returns: (pid_col_index or None, list_of_data_lines)
    """
    lines = [ln.rstrip("\n") for ln in output.splitlines() if ln.strip()]
    if not lines:
        return None, []

    header = lines[0]
    cols = header.split()
    pid_idx = None
    for i, c in enumerate(cols):
        if c.upper() == "PID":
            pid_idx = i
            break

    # Some ps variants might not include a header (rare with some flags).
    # If no PID header is found, we still return data lines and let extraction try a fallback.
    return pid_idx, lines[1:]


def _extract_pid_from_line(line: str, pid_idx: Optional[int], header_cols_len: Optional[int]) -> Optional[int]:
    """
    Extract PID from a ps output line. Primary approach uses pid_idx.
    Fallback tries to find a standalone integer token that looks like a PID.
    """
    parts = line.split()

    if pid_idx is not None and pid_idx < len(parts):
        token = parts[pid_idx]
        if token.isdigit():
            try:
                return int(token)
            except ValueError:
                return None

    # Fallback heuristic:
    # - Prefer the first integer token that is within a plausible PID range.
    for tok in parts:
        if tok.isdigit():
            try:
                val = int(tok)
                if 1 <= val <= 10_000_000:
                    return val
            except ValueError:
                continue

    return None


@csrf_exempt
def monitor_commands(request: HttpRequest):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    # Parse JSON body
    try:
        raw = request.body.decode("utf-8") if request.body else ""
        payload = json.loads(raw) if raw else {}
    except Exception:
        return _error(400, "Invalid JSON body")

    command_regex = payload.get("command_regex")
    filter_flags = payload.get("filter_flags")

    if not isinstance(command_regex, str) or not command_regex:
        return _error(400, "Missing or invalid required field: command_regex")
    if filter_flags is not None and not isinstance(filter_flags, str):
        return _error(400, "Field filter_flags must be a string if provided")

    try:
        pattern = re.compile(command_regex)
    except re.error as e:
        return _error(400, f"Invalid command_regex: {e}")

    rc, out, err = _run_ps(filter_flags)
    if rc != 0:
        # Provide stderr to help debugging but keep it shortish
        msg = err.strip() or f"ps exited with code {rc}"
        return _error(500, msg)

    pid_idx, data_lines = _parse_ps_output(out)

    results: List[Dict[str, object]] = []
    # we don't actually need header_cols_len; kept for potential future extension
    header_cols_len = None

    for line in data_lines:
        # Match regex against the full process line (processString)
        if not pattern.search(line):
            continue

        pid = _extract_pid_from_line(line, pid_idx, header_cols_len)
        if pid is None:
            # If we can't parse a PID, skip (schema requires integer)
            continue

        results.append({
            "processId": pid,
            "processString": line,
        })

    return JsonResponse(results, safe=False, status=200)