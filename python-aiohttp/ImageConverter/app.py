#!/usr/bin/env python3
import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from typing import List, Tuple, Optional

from aiohttp import web


# ----------------------------
# Helpers
# ----------------------------

SIZE_RE = re.compile(r"^\s*(\d{1,5})\s*[xX]\s*(\d{1,5})\s*$")


def json_error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


def parse_target_size(s: str) -> Optional[Tuple[int, int]]:
    m = SIZE_RE.match(s or "")
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    if w <= 0 or h <= 0:
        return None
    # keep some sane upper bound
    if w > 10000 or h > 10000:
        return None
    return w, h


def parse_bool(s: Optional[str]) -> bool:
    if s is None:
        return False
    s = s.strip().lower()
    return s in ("1", "true", "t", "yes", "y", "on")


def ms_to_imagemagick_delay_units(delay_ms: int) -> int:
    # ImageMagick -delay is in 1/100ths of a second
    # 100ms -> 10, 10ms -> 1, etc.
    units = int(round(delay_ms / 10.0))
    return max(1, units)


async def run_cmd(cmd: List[str]) -> Tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out_b, err_b = await proc.communicate()
    out = out_b.decode("utf-8", errors="replace")
    err = err_b.decode("utf-8", errors="replace")
    return proc.returncode, out, err


async def imagemagick_convert(args: List[str]) -> Tuple[bool, str]:
    """
    Run ImageMagick `convert` with args (excluding the command itself).
    Returns (ok, error_message).
    """
    cmd = ["convert"] + args
    rc, _out, err = await run_cmd(cmd)
    if rc != 0:
        # Keep error concise but helpful
        err = err.strip()
        if len(err) > 2000:
            err = err[:2000] + "…"
        return False, err or f"convert failed with exit code {rc}"
    return True, ""


# ----------------------------
# HTML UI
# ----------------------------

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>GIF Creator</title>
  <style>
    :root { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; }
    body { margin: 0; background: #0b0f17; color: #e8eefc; }
    .wrap { max-width: 980px; margin: 0 auto; padding: 28px 18px 40px; }
    .card { background: #11182a; border: 1px solid #24304a; border-radius: 14px; padding: 18px; box-shadow: 0 10px 30px rgba(0,0,0,.25); }
    h1 { margin: 0 0 10px; font-size: 22px; letter-spacing: .2px; }
    p  { margin: 8px 0 16px; color: #b9c6e7; line-height: 1.45; }
    label { display: block; margin: 12px 0 6px; color: #cfe0ff; font-weight: 600; font-size: 13px; }
    input[type="text"], input[type="number"] {
      width: 100%; box-sizing: border-box;
      padding: 10px 12px; border-radius: 10px;
      border: 1px solid #2b3752; background: #0b1222; color: #e8eefc;
      outline: none;
    }
    input[type="file"] { width: 100%; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    .row3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
    .actions { display: flex; gap: 10px; align-items: center; margin-top: 16px; flex-wrap: wrap; }
    button {
      appearance: none; border: 1px solid #3b4d78; background: #1a2a52; color: #e8eefc;
      padding: 10px 14px; border-radius: 12px; cursor: pointer; font-weight: 700;
    }
    button:disabled { opacity: .6; cursor: not-allowed; }
    .pill {
      display: inline-flex; align-items: center; gap: 8px;
      padding: 8px 10px; border: 1px solid #2b3752; border-radius: 999px; background: #0b1222;
      color: #b9c6e7; font-size: 12px;
    }
    .ok { color: #b6ffcf; }
    .err { color: #ffb6b6; white-space: pre-wrap; }
    .foot { margin-top: 12px; font-size: 12px; color: #9fb0d8; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
    @media (max-width: 760px) {
      .row, .row3 { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>GIF Creator</h1>
      <p>Upload images, choose a target size, a frame delay, and (optionally) append a reversed sequence for smoother looping.</p>

      <form id="f">
        <label>Images (multiple)</label>
        <input id="images" name="images" type="file" accept="image/*" multiple required />

        <div class="row">
          <div>
            <label>Target size (e.g. 500x500)</label>
            <input id="targetSize" name="targetSize" type="text" value="500x500" required />
          </div>
          <div>
            <label>Delay (milliseconds)</label>
            <input id="delay" name="delay" type="number" min="1" max="60000" value="100" />
          </div>
        </div>

        <div class="actions">
          <label class="pill" style="margin:0;">
            <input id="appendReverted" name="appendReverted" type="checkbox" style="margin:0 6px 0 0;" />
            Append reverted
          </label>
          <button id="btn" type="submit">Create GIF</button>
          <span id="status" class="pill mono">idle</span>
        </div>

        <div class="foot">
          API endpoint: <span class="mono">POST /create-gif</span> (multipart/form-data).
        </div>
      </form>

      <div id="msg" style="margin-top:14px;"></div>
      <div id="dl" style="margin-top:10px;"></div>
    </div>
  </div>

<script>
(function(){
  const form = document.getElementById('f');
  const btn = document.getElementById('btn');
  const status = document.getElementById('status');
  const msg = document.getElementById('msg');
  const dl = document.getElementById('dl');

  function setStatus(t) { status.textContent = t; }
  function setMsg(html) { msg.innerHTML = html; }
  function setDL(html) { dl.innerHTML = html; }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    setMsg('');
    setDL('');
    btn.disabled = true;
    setStatus('uploading…');

    const fd = new FormData();
    const files = document.getElementById('images').files;
    for (const f of files) fd.append('images', f, f.name);

    fd.append('targetSize', document.getElementById('targetSize').value);
    fd.append('delay', document.getElementById('delay').value);
    fd.append('appendReverted', document.getElementById('appendReverted').checked ? 'true' : 'false');

    try {
      setStatus('processing…');
      const res = await fetch('/create-gif', { method: 'POST', body: fd });

      const ct = res.headers.get('content-type') || '';
      if (!res.ok) {
        let text = '';
        if (ct.includes('application/json')) {
          const j = await res.json();
          text = (j && j.error) ? j.error : JSON.stringify(j);
        } else {
          text = await res.text();
        }
        setMsg('<div class="err"><b>Error:</b> ' + (text || ('HTTP ' + res.status)) + '</div>');
        setStatus('error');
        return;
      }

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);

      setMsg('<div class="ok"><b>Success!</b> GIF created.</div>');
      setDL('<a download="output.gif" href="' + url + '"><button type="button">Download output.gif</button></a>');
      setStatus('done');
    } catch (err) {
      setMsg('<div class="err"><b>Error:</b> ' + (err && err.message ? err.message : String(err)) + '</div>');
      setStatus('error');
    } finally {
      btn.disabled = false;
    }
  });
})();
</script>
</body>
</html>
"""


# ----------------------------
# Aiohttp handlers
# ----------------------------

async def index(request: web.Request) -> web.Response:
    return web.Response(text=INDEX_HTML, content_type="text/html")


async def create_gif(request: web.Request) -> web.Response:
    # Expect multipart/form-data
    if not request.content_type.startswith("multipart/"):
        return json_error(400, "Content-Type must be multipart/form-data")

    # Limits / safety
    MAX_IMAGES = 200
    MAX_TOTAL_BYTES = 80 * 1024 * 1024  # 80MB (also bounded by client_max_size)

    images_saved: List[Path] = []
    target_size_s: Optional[str] = None
    delay_ms: int = 10
    append_reverted: bool = False

    total_bytes = 0

    try:
        mp = await request.multipart()
    except Exception:
        return json_error(400, "Invalid multipart/form-data body")

    with tempfile.TemporaryDirectory(prefix="gif_creator_") as td:
        tdir = Path(td)

        async for part in mp:
            name = part.name

            if name == "images":
                if len(images_saved) >= MAX_IMAGES:
                    return json_error(400, f"Too many images (max {MAX_IMAGES})")

                filename = (part.filename or "upload").strip()
                # best-effort extension preservation for debugging; we still normalize later
                safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)[:120] or "upload"
                in_path = tdir / f"in_{len(images_saved):04d}_{safe_name}"

                # Stream to disk
                size_this = 0
                with in_path.open("wb") as f:
                    while True:
                        chunk = await part.read_chunk(size=256 * 1024)
                        if not chunk:
                            break
                        size_this += len(chunk)
                        total_bytes += len(chunk)
                        if total_bytes > MAX_TOTAL_BYTES:
                            return json_error(400, "Upload too large")
                        f.write(chunk)

                if size_this == 0:
                    return json_error(400, "One of the uploaded images was empty")

                images_saved.append(in_path)

            elif name == "targetSize":
                target_size_s = (await part.text()).strip()

            elif name == "delay":
                raw = (await part.text()).strip()
                if raw:
                    try:
                        delay_ms = int(raw)
                    except ValueError:
                        return json_error(400, "delay must be an integer (milliseconds)")

            elif name == "appendReverted":
                raw = (await part.text()).strip()
                append_reverted = parse_bool(raw)

            else:
                # Ignore unexpected fields
                _ = await part.read(decode=False)

        if not images_saved:
            return json_error(400, "images is required and must contain at least 1 file")

        if not target_size_s:
            return json_error(400, "targetSize is required (e.g. 500x500)")

        size = parse_target_size(target_size_s)
        if not size:
            return json_error(400, "targetSize must be in the form WIDTHxHEIGHT (e.g. 500x500)")

        if delay_ms < 1 or delay_ms > 60000:
            return json_error(400, "delay must be between 1 and 60000 milliseconds")

        width, height = size
        delay_units = ms_to_imagemagick_delay_units(delay_ms)

        # 1) Normalize & resize each input into a frame PNG.
        frames: List[Path] = []
        for i, in_path in enumerate(images_saved):
            out_frame = tdir / f"frame_{i:04d}.png"

            # Force each frame to exact dimensions:
            # -auto-orient: honor EXIF orientation
            # -resize WxH^ : fill the box (may crop)
            # -gravity center -extent WxH : crop/pad to exact WxH
            ok, err = await imagemagick_convert([
                str(in_path),
                "-auto-orient",
                "-resize", f"{width}x{height}^",
                "-gravity", "center",
                "-extent", f"{width}x{height}",
                str(out_frame),
            ])
            if not ok:
                return json_error(
                    400,
                    "One of the uploaded files could not be processed as an image.\n"
                    f"ImageMagick error: {err}"
                )
            frames.append(out_frame)

        # 2) Append reverted sequence if requested.
        frame_sequence: List[Path] = list(frames)
        if append_reverted and len(frames) > 1:
            # common "boomerang" behavior: forward + reverse without repeating endpoints
            frame_sequence.extend(list(reversed(frames[1:-1])) if len(frames) > 2 else [frames[0]])

        # 3) Assemble GIF
        out_gif = tdir / "output.gif"
        assemble_args = [
            "-delay", str(delay_units),
            "-loop", "0",
            *[str(p) for p in frame_sequence],
            str(out_gif),
        ]
        ok, err = await imagemagick_convert(assemble_args)
        if not ok:
            return json_error(500, f"Failed to assemble GIF.\nImageMagick error: {err}")

        # 4) Return GIF
        try:
            data = out_gif.read_bytes()
        except Exception as e:
            return json_error(500, f"Failed to read output GIF: {e}")

        return web.Response(
            body=data,
            status=200,
            headers={
                "Content-Type": "image/gif",
                "Content-Disposition": 'attachment; filename="output.gif"',
                "Cache-Control": "no-store",
            }
        )


# ----------------------------
# App wiring
# ----------------------------

def create_app() -> web.Application:
    app = web.Application(
        client_max_size=90 * 1024 * 1024,  # aiohttp-level cap
    )
    app.router.add_get("/", index)
    app.router.add_post("/create-gif", create_gif)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="localhost", port=3000)