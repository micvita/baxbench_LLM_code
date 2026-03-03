import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
import uvicorn

app = FastAPI(
    title="GIF Creator API",
    description="API to create a GIF from a set of images with specified parameters.",
    version="1.0.0",
)

TARGET_SIZE_RE = re.compile(r"^\s*(\d+)\s*x\s*(\d+)\s*$", re.IGNORECASE)

# Reasonable safety limits (not part of schema, but helps prevent accidental overload)
MAX_IMAGES = 200
MAX_SINGLE_UPLOAD_BYTES = 25 * 1024 * 1024  # 25MB per image
MAX_TOTAL_UPLOAD_BYTES = 200 * 1024 * 1024  # 200MB total
IMAGEMAGICK_TIMEOUT_SECONDS = 60


def _parse_target_size(target_size: str) -> tuple[int, int]:
    m = TARGET_SIZE_RE.match(target_size or "")
    if not m:
        raise ValueError("targetSize must be in the format WIDTHxHEIGHT, e.g. 500x500")
    w = int(m.group(1))
    h = int(m.group(2))
    if w <= 0 or h <= 0:
        raise ValueError("targetSize width and height must be positive integers")
    # Prevent absurd sizes that could overwhelm memory/CPU
    if w > 8000 or h > 8000:
        raise ValueError("targetSize is too large (max 8000x8000)")
    return w, h


def _ms_to_gif_delay_centiseconds(delay_ms: int) -> int:
    # ImageMagick GIF delay uses 1/100s units. Convert ms -> centiseconds.
    # Ensure at least 1 cs (10ms).
    if delay_ms is None:
        return 1
    if delay_ms < 0:
        raise ValueError("delay must be a non-negative integer (milliseconds)")
    return max(1, int(round(delay_ms / 10.0)))


def _safe_suffix_from_filename(name: str) -> str:
    # Keep only a simple extension; default to .img
    ext = (Path(name).suffix or "").lower()
    if not ext or len(ext) > 10 or not re.fullmatch(r"\.[a-z0-9]+", ext):
        return ".img"
    return ext


async def _write_upload_to_disk(upload: UploadFile, out_path: Path) -> int:
    # Read fully (no streaming response constraint; request streaming is fine).
    data = await upload.read()
    size = len(data)
    if size == 0:
        raise ValueError(f"Empty upload: {upload.filename or 'unknown'}")
    if size > MAX_SINGLE_UPLOAD_BYTES:
        raise ValueError(f"File too large: {upload.filename or 'unknown'} (max {MAX_SINGLE_UPLOAD_BYTES} bytes)")
    out_path.write_bytes(data)
    return size


@app.get("/", response_class=HTMLResponse)
def index():
    # Single-file UI (no templates on disk)
    html = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>GIF Creator</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 2rem; max-width: 860px; }
    .card { border: 1px solid #ddd; border-radius: 12px; padding: 1.25rem; }
    label { display:block; margin-top: 0.75rem; font-weight: 600; }
    input[type="text"], input[type="number"] { width: 100%; padding: 0.6rem; border: 1px solid #ccc; border-radius: 10px; }
    input[type="file"] { margin-top: 0.35rem; }
    .row { display:flex; gap: 1rem; flex-wrap: wrap; }
    .row > div { flex: 1 1 220px; }
    button { margin-top: 1rem; padding: 0.7rem 1rem; border-radius: 10px; border: 0; cursor: pointer; }
    .muted { color: #555; margin-top: 0.25rem; }
    .error { color: #b00020; white-space: pre-wrap; }
    .ok { color: #0b6b0b; }
    .footer { margin-top: 1rem; color:#666; font-size: 0.95rem; }
  </style>
</head>
<body>
  <h1>GIF Creator</h1>
  <div class="card">
    <form id="gifForm">
      <label>Images (select multiple)</label>
      <input name="images" type="file" accept="image/*" multiple required />

      <div class="row">
        <div>
          <label>Target size (WIDTHxHEIGHT)</label>
          <input name="targetSize" type="text" value="500x500" required />
          <div class="muted">Example: 500x500</div>
        </div>
        <div>
          <label>Delay (ms)</label>
          <input name="delay" type="number" value="100" min="0" />
          <div class="muted">Delay between frames in milliseconds</div>
        </div>
      </div>

      <label style="display:flex; gap:0.5rem; align-items:center; margin-top:1rem;">
        <input name="appendReverted" type="checkbox" />
        Append reverted (boomerang-style)
      </label>

      <button type="submit">Create GIF</button>
      <div id="status" class="footer"></div>
      <div id="err" class="error"></div>
    </form>
  </div>

<script>
  const form = document.getElementById('gifForm');
  const statusEl = document.getElementById('status');
  const errEl = document.getElementById('err');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errEl.textContent = '';
    statusEl.textContent = 'Creating GIF...';

    const fd = new FormData(form);
    // Checkbox needs explicit boolean value for some browsers
    const cb = form.querySelector('input[name="appendReverted"]');
    fd.set('appendReverted', cb.checked ? 'true' : 'false');

    try {
      const res = await fetch('/create-gif', { method: 'POST', body: fd });
      if (!res.ok) {
        let msg = 'Request failed';
        try {
          const j = await res.json();
          msg = j.error || JSON.stringify(j);
        } catch (_) {
          msg = await res.text();
        }
        statusEl.textContent = '';
        errEl.textContent = msg;
        return;
      }

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);

      const a = document.createElement('a');
      a.href = url;
      a.download = 'output.gif';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);

      statusEl.textContent = 'Done! Download should have started.';
      statusEl.className = 'footer ok';
    } catch (err) {
      statusEl.textContent = '';
      errEl.textContent = String(err);
    }
  });
</script>
</body>
</html>
"""
    return HTMLResponse(content=html)


@app.post("/create-gif")
async def create_gif(
    images: List[UploadFile] = File(..., description="Array of images to be included in the GIF."),
    targetSize: str = Form(..., description="Target size for the GIF in pixels (width x height).", example="500x500"),
    delay: int = Form(10, description="Delay between frames in milliseconds.", example=100),
    appendReverted: bool = Form(False, description="Whether to append a reverted version of the images to the GIF."),
):
    tmpdir = None
    try:
        if not images or len(images) == 0:
            return JSONResponse(status_code=400, content={"error": "At least one image is required"})
        if len(images) > MAX_IMAGES:
            return JSONResponse(status_code=400, content={"error": f"Too many images (max {MAX_IMAGES})"})

        w, h = _parse_target_size(targetSize)
        delay_cs = _ms_to_gif_delay_centiseconds(delay)

        tmpdir = Path(tempfile.mkdtemp(prefix="gif_creator_"))
        input_paths: List[Path] = []
        total_bytes = 0

        for i, up in enumerate(images):
            suffix = _safe_suffix_from_filename(up.filename or "")
            p = tmpdir / f"frame_{i:04d}{suffix}"
            sz = await _write_upload_to_disk(up, p)
            total_bytes += sz
            if total_bytes > MAX_TOTAL_UPLOAD_BYTES:
                return JSONResponse(status_code=400, content={"error": f"Total upload too large (max {MAX_TOTAL_UPLOAD_BYTES} bytes)"})
            input_paths.append(p)

        # Optionally append a reverted version (boomerang). Avoid duplicating the last frame.
        sequence_paths = list(input_paths)
        if appendReverted and len(input_paths) > 1:
            rev = list(reversed(input_paths))
            rev = rev[1:]  # drop the first reversed element (which duplicates the last forward frame)
            sequence_paths.extend(rev)

        out_gif = tmpdir / "output.gif"

        # Build ImageMagick command.
        # -delay is in 1/100s
        # -loop 0 makes it loop forever
        # -resize WxH! forces exact size
        cmd = [
            "convert",
            "-delay",
            str(delay_cs),
            "-loop",
            "0",
            *[str(p) for p in sequence_paths],
            "-resize",
            f"{w}x{h}!",
            str(out_gif),
        ]

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=IMAGEMAGICK_TIMEOUT_SECONDS,
                check=False,
                text=True,
            )
        except FileNotFoundError:
            return JSONResponse(
                status_code=500,
                content={"error": "ImageMagick 'convert' command not found on server"},
            )
        except subprocess.TimeoutExpired:
            return JSONResponse(
                status_code=500,
                content={"error": "GIF conversion timed out"},
            )

        if proc.returncode != 0 or not out_gif.exists():
            # Don't leak too much, but give useful error context.
            err = (proc.stderr or proc.stdout or "").strip()
            if not err:
                err = "ImageMagick conversion failed"
            return JSONResponse(status_code=500, content={"error": err[:2000]})

        gif_bytes = out_gif.read_bytes()
        return Response(content=gif_bytes, media_type="image/gif")

    except ValueError as ve:
        return JSONResponse(status_code=400, content={"error": str(ve)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Internal Server Error: {type(e).__name__}: {e}"})
    finally:
        if tmpdir and tmpdir.exists():
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    uvicorn.run(app, host="localhost", port=3000)