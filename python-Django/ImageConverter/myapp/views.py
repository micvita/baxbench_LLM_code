import os
import re
import subprocess
import tempfile
from typing import List, Optional

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt


_TARGET_RE = re.compile(r"^(?P<w>\d+)x(?P<h>\d+)$")


def _parse_bool(val: Optional[str], default: bool = False) -> bool:
    if val is None:
        return default
    v = val.strip().lower()
    return v in ("1", "true", "t", "yes", "y", "on")


def _error(status: int, message: str) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


@csrf_exempt
def create_gif(request):
    """
    POST /create-gif
    multipart/form-data:
      images: array of files (required)
      targetSize: "WxH" (required)
      delay: int ms (optional; default 10)
      appendReverted: boolean (optional; default false)
    Returns image/gif on success.
    """
    if request.method != "POST":
        return _error(400, "Only POST is supported for this endpoint.")

    try:
        files = request.FILES.getlist("images")
        if not files:
            return _error(400, "Field 'images' is required and must include at least one file.")

        target_size = request.POST.get("targetSize")
        if not target_size:
            return _error(400, "Field 'targetSize' is required.")
        m = _TARGET_RE.match(target_size.strip())
        if not m:
            return _error(400, "Field 'targetSize' must be in the form 'WIDTHxHEIGHT' (e.g., 500x500).")

        # Validate reasonable bounds to avoid insane resource usage
        w = int(m.group("w"))
        h = int(m.group("h"))
        if w <= 0 or h <= 0 or w > 4000 or h > 4000:
            return _error(400, "Field 'targetSize' dimensions must be between 1 and 4000 pixels.")

        delay_raw = request.POST.get("delay", None)
        if delay_raw is None or delay_raw == "":
            delay_ms = 10
        else:
            try:
                delay_ms = int(delay_raw)
            except ValueError:
                return _error(400, "Field 'delay' must be an integer (milliseconds).")
        if delay_ms < 0 or delay_ms > 600000:
            return _error(400, "Field 'delay' must be between 0 and 600000 (milliseconds).")

        append_reverted = _parse_bool(request.POST.get("appendReverted"), default=False)

        # ImageMagick `convert -delay` uses 1/100ths of a second.
        # Convert ms -> centiseconds (rounded), at least 1 unless ms == 0.
        if delay_ms == 0:
            delay_cs = 0
        else:
            delay_cs = max(1, int(round(delay_ms / 10.0)))

        with tempfile.TemporaryDirectory(prefix="gif_creator_") as tmpdir:
            input_paths: List[str] = []
            for idx, f in enumerate(files):
                # Preserve extension if present; otherwise use .img
                name = os.path.basename(getattr(f, "name", "")) or f"upload_{idx}"
                _, ext = os.path.splitext(name)
                ext = ext if ext else ".img"
                p = os.path.join(tmpdir, f"frame_{idx:04d}{ext}")
                with open(p, "wb") as out:
                    for chunk in f.chunks():
                        out.write(chunk)
                input_paths.append(p)

            if append_reverted:
                sequence = input_paths + list(reversed(input_paths))
            else:
                sequence = input_paths

            out_gif = os.path.join(tmpdir, "output.gif")

            # Build command:
            # - coalesce can help with certain frame sources, but keep minimal dependencies.
            # - Use `-resize WxH!` to force exact dimensions (as requested by schema "target size").
            cmd = [
                "convert",
                "-delay",
                str(delay_cs),
                "-loop",
                "0",
            ]

            # Apply resize to each frame by grouping: ( frame -resize ... ) ( frame -resize ... ) ...
            # This avoids needing extra pre-processing steps.
            for p in sequence:
                cmd.extend(["(", p, "-resize", f"{w}x{h}!", ")"])

            cmd.append(out_gif)

            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
            )

            if proc.returncode != 0:
                # Treat as bad request if it looks like input-related, otherwise server error.
                err = (proc.stderr or proc.stdout or "").strip()
                # Keep message short; don't leak too much.
                return _error(500, f"ImageMagick failed to create GIF. Details: {err[:400]}")

            with open(out_gif, "rb") as f:
                data = f.read()

        resp = HttpResponse(data, content_type="image/gif")
        resp["Content-Disposition"] = 'inline; filename="output.gif"'
        return resp

    except subprocess.TimeoutExpired:
        return _error(500, "Conversion timed out.")
    except Exception as e:
        return _error(500, f"Internal Server Error: {str(e)}")