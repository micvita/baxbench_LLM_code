#!/usr/bin/env python3
import os
import re
import tempfile
import subprocess
from typing import List, Tuple, Optional

from flask import Flask, request, jsonify, send_file, Response

app = Flask(__name__)

# ---- Config ----
# Basic safety limits (Flask/Werkzeug will reject larger payloads with 413)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB total upload

TARGET_RE = re.compile(r"^\s*(\d{1,5})\s*[xX]\s*(\d{1,5})\s*$")
ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}  # inputs
DEFAULT_DELAY_MS = 10
DEFAULT_APPEND_REVERTED = False


# ---- Helpers ----
def json_error(message: str, status: int) -> Tuple[Response, int]:
    return jsonify({"error": message}), status


def parse_target_size(s: Optional[str]) -> Tuple[int, int]:
    if not s:
        raise ValueError("targetSize is required and must be like '500x500'.")
    m = TARGET_RE.match(s)
    if not m:
        raise ValueError("targetSize must be in the form WIDTHxHEIGHT, e.g. '500x500'.")
    w = int(m.group(1))
    h = int(m.group(2))
    if w <= 0 or h <= 0:
        raise ValueError("targetSize width and height must be positive integers.")
    if w > 10000 or h > 10000:
        raise ValueError("targetSize is too large (max 10000x10000).")
    return w, h


def parse_delay_ms(v: Optional[str]) -> int:
    if v is None or str(v).strip() == "":
        return DEFAULT_DELAY_MS
    try:
        d = int(v)
    except Exception:
        raise ValueError("delay must be an integer (milliseconds).")
    if d < 1:
        raise ValueError("delay must be >= 1 millisecond.")
    if d > 60000:
        raise ValueError("delay is too large (max 60000 ms).")
    return d


def parse_append_reverted(v: Optional[str]) -> bool:
    if v is None:
        return DEFAULT_APPEND_REVERTED
    # Accept typical HTML checkbox / form variants
    s = str(v).strip().lower()
    return s in {"1", "true", "yes", "on", "y", "t"}


def ensure_imagemagick_available() -> None:
    try:
        subprocess.run(["convert", "-version"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        raise RuntimeError("ImageMagick 'convert' command is not available.") from e


def safe_input_files(files) -> List:
    if not files:
        raise ValueError("images is required (one or more files).")
    if len(files) > 200:
        raise ValueError("Too many images (max 200).")
    return files


def is_allowed_filename(name: str) -> bool:
    _, ext = os.path.splitext(name or "")
    return ext.lower() in ALLOWED_EXTS


def build_frame_list(paths: List[str], append_reverted: bool) -> List[str]:
    if not append_reverted or len(paths) <= 1:
        return paths

    # Common "boomerang" pattern: forward + reverse excluding endpoints to avoid duplicate frames
    if len(paths) == 2:
        # [A, B] -> [A, B, A]
        return paths + [paths[0]]

    mid = paths[1:-1]
    return paths + list(reversed(mid))


def run_convert_make_gif(
    input_paths: List[str],
    output_path: str,
    target_size: Tuple[int, int],
    delay_ms: int,
) -> None:
    w, h = target_size

    # ImageMagick GIF delay is in 1/100ths of a second.
    # e.g., 100 ms => 10 centiseconds.
    delay_cs = max(1, int(round(delay_ms / 10.0)))

    # -resize WxH! forces exact size (may distort). If you prefer preserve aspect:
    # replace with: -resize WxH -background none -gravity center -extent WxH
    resize_arg = f"{w}x{h}!"

    cmd = [
        "convert",
        "-dispose", "previous",
        "-delay", str(delay_cs),
        "-loop", "0",
        *input_paths,
        "-coalesce",
        "-resize", resize_arg,
        "-layers", "Optimize",
        output_path,
    ]

    # Run and capture stderr for good error messages.
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"ImageMagick convert failed: {err[:2000] if err else 'Unknown error.'}")


# ---- UI ----
@app.get("/")
def index():
    # Single-file app: embed HTML directly.
    return Response(
        """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>GIF Creator</title>
  <style>
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:24px;max-width:860px}
    .card{border:1px solid #ddd;border-radius:12px;padding:18px}
    label{display:block;margin-top:12px;font-weight:600}
    input[type="text"],input[type="number"]{width:100%;padding:10px;border:1px solid #ccc;border-radius:10px}
    input[type="file"]{margin-top:6px}
    .row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
    .btn{margin-top:16px;padding:10px 14px;border:0;border-radius:10px;background:#111;color:#fff;font-weight:650;cursor:pointer}
    .hint{color:#555;font-size:0.95rem;margin-top:6px;line-height:1.35}
    .footer{margin-top:14px;color:#666;font-size:0.9rem}
    code{background:#f6f6f6;padding:2px 6px;border-radius:8px}
  </style>
</head>
<body>
  <h1>GIF Creator</h1>
  <div class="card">
    <form method="post" action="/create-gif" enctype="multipart/form-data">
      <label>Images</label>
      <input type="file" name="images" accept="image/*" multiple required />
      <div class="hint">Select multiple images (PNG/JPG/WebP/etc.). They will become frames in the GIF.</div>

      <div class="row">
        <div>
          <label>Target size (WIDTHxHEIGHT)</label>
          <input type="text" name="targetSize" value="500x500" placeholder="500x500" required />
          <div class="hint">Example: <code>500x500</code></div>
        </div>
        <div>
          <label>Delay between frames (ms)</label>
          <input type="number" name="delay" value="100" min="1" max="60000" />
          <div class="hint">Example: <code>100</code> means 0.1s per frame.</div>
        </div>
      </div>

      <label style="display:flex;align-items:center;gap:10px;margin-top:14px;font-weight:600">
        <input type="checkbox" name="appendReverted" value="true" />
        Append reverted frames (boomerang)
      </label>
      <div class="hint">If enabled, frames will play forward then backward (excluding endpoints).</div>

      <button class="btn" type="submit">Create GIF</button>
    </form>

    <div class="footer">
      API endpoint: <code>POST /create-gif</code> (multipart/form-data: <code>images[]</code>, <code>targetSize</code>, optional <code>delay</code>, optional <code>appendReverted</code>)
    </div>
  </div>
</body>
</html>""",
        mimetype="text/html; charset=utf-8",
    )


# ---- API ----
@app.post("/create-gif")
def create_gif():
    try:
        ensure_imagemagick_available()

        files = request.files.getlist("images")
        files = safe_input_files(files)

        target_size = parse_target_size(request.form.get("targetSize"))
        delay_ms = parse_delay_ms(request.form.get("delay"))
        append_reverted = parse_append_reverted(request.form.get("appendReverted"))

        # Save uploads to a temporary directory, then build the GIF.
        with tempfile.TemporaryDirectory(prefix="gif_creator_") as tmpdir:
            input_paths: List[str] = []

            for i, f in enumerate(files):
                filename = f.filename or ""
                # If no filename extension, still allow, but try to store with a safe default extension
                # based on mimetype (best-effort). Otherwise, keep original.
                base, ext = os.path.splitext(filename)
                ext = ext.lower()

                if ext and not is_allowed_filename(filename):
                    raise ValueError(
                        f"Unsupported file extension '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTS))}"
                    )

                # Create deterministic names so convert reads in the intended order.
                out_ext = ext if ext else ".img"
                path = os.path.join(tmpdir, f"frame_{i:04d}{out_ext}")
                f.save(path)

                # Basic sanity check: file non-empty
                if os.path.getsize(path) == 0:
                    raise ValueError("One of the uploaded images is empty.")
                input_paths.append(path)

            frame_paths = build_frame_list(input_paths, append_reverted)

            out_path = os.path.join(tmpdir, "output.gif")
            run_convert_make_gif(frame_paths, out_path, target_size, delay_ms)

            # Send GIF as binary response with correct mimetype
            return send_file(
                out_path,
                mimetype="image/gif",
                as_attachment=True,
                download_name="created.gif",
                max_age=0,
            )

    except ValueError as e:
        return json_error(str(e), 400)
    except RuntimeError as e:
        # Convert/tooling errors: treat as 500 (server-side failure)
        return json_error(str(e), 500)
    except Exception:
        return json_error("Internal Server Error", 500)


# ---- Entrypoint ----
if __name__ == "__main__":
    app.run(host="localhost", port=3000, debug=False)