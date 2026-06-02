import json
import os
import uuid
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

CHUNK = 64 * 1024

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/srv/uploader/files")
HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8000"))

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Uploader</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 40rem; margin: 2rem auto; padding: 0 1rem; }
  #drop { border: 2px dashed #999; border-radius: 8px; padding: 2rem; text-align: center; color: #555; }
  #drop.over { border-color: #333; background: #f5f5f5; }
  progress { width: 100%; display: none; margin-top: 1rem; }
  ul { list-style: none; padding: 0; }
  li { display: flex; justify-content: space-between; align-items: center; gap: 1rem; padding: .4rem 0; border-bottom: 1px solid #eee; }
  li a { overflow-wrap: anywhere; }
  .meta { display: flex; align-items: center; gap: .8rem; white-space: nowrap; }
  .time, .size { color: #888; font-variant-numeric: tabular-nums; }
  button.copy, .res button { font: inherit; padding: .1rem .5rem; cursor: pointer; }
  #err { color: #b00; min-height: 1.2em; }
  #result { margin: 1rem 0; }
  .res { display: flex; align-items: center; gap: .5rem; padding: .3rem 0; }
  .res .ok { color: #2a7; white-space: nowrap; }
  .res .url { flex: 1; min-width: 0; font-family: ui-monospace, monospace; font-size: .85em; padding: .2rem .4rem; }
</style>
</head>
<body>
<h1>Uploader</h1>
<div id="drop">Ovde baci datoteku ili <input type="file" id="file" multiple></div>
<progress id="bar" max="100" value="0"></progress>
<div id="err"></div>
<div id="result"></div>
<h2>Files</h2>
<ul id="list"></ul>
<script>
const drop = document.getElementById('drop');
const fileInput = document.getElementById('file');
const bar = document.getElementById('bar');
const err = document.getElementById('err');
const result = document.getElementById('result');
const list = document.getElementById('list');

function human(n) {
  const u = ['B','KB','MB','GB','TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(1)) + ' ' + u[i];
}

function fmtTime(s) {
  // mtime is epoch seconds; render in the viewer's local timezone.
  return new Date(s * 1000).toLocaleString();
}

function dlUrl(name) {
  // location.origin -> the public tunnel URL when reached through Cloudflare,
  // 127.0.0.1 locally; so the copied link is always correct for the viewer.
  return location.origin + '/download/' + encodeURIComponent(name);
}

async function copy(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    // Fallback for non-secure contexts where the Clipboard API is unavailable.
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (_) {}
    document.body.removeChild(ta);
  }
  const old = btn.textContent;
  btn.textContent = 'Copied';
  setTimeout(() => { btn.textContent = old; }, 1200);
}

function copyButton(name, cls) {
  const btn = document.createElement('button');
  btn.type = 'button';
  if (cls) btn.className = cls;
  btn.textContent = 'Copy';
  btn.addEventListener('click', () => copy(dlUrl(name), btn));
  return btn;
}

async function refresh() {
  const res = await fetch('/api/files');
  const files = await res.json();
  list.innerHTML = '';
  for (const f of files) {
    const li = document.createElement('li');
    const a = document.createElement('a');
    a.href = '/download/' + encodeURIComponent(f.name);
    a.textContent = f.name;
    const meta = document.createElement('span');
    meta.className = 'meta';
    const time = document.createElement('span');
    time.className = 'time';
    time.textContent = fmtTime(f.mtime);
    const size = document.createElement('span');
    size.className = 'size';
    size.textContent = human(f.size);
    meta.append(time, size, copyButton(f.name, 'copy'));
    li.append(a, meta);
    list.append(li);
  }
}

function showResults(names) {
  result.innerHTML = '';
  for (const name of names) {
    const row = document.createElement('div');
    row.className = 'res';
    const ok = document.createElement('span');
    ok.className = 'ok';
    ok.textContent = '✓ ' + name;
    const url = document.createElement('input');
    url.className = 'url';
    url.type = 'text';
    url.readOnly = true;
    url.value = dlUrl(name);
    url.addEventListener('focus', () => url.select());
    row.append(ok, url, copyButton(name));
    result.append(row);
  }
}

function uploadOne(file) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', '/upload?name=' + encodeURIComponent(file.name));
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) bar.value = (e.loaded / e.total) * 100;
    };
    xhr.onload = () => {
      if (xhr.status !== 201) {
        reject(new Error(xhr.responseText || xhr.status));
        return;
      }
      // Server returns the final saved name (post-collision, e.g. a(1).txt).
      try { resolve(JSON.parse(xhr.responseText).name); }
      catch (e) { resolve(file.name); }
    };
    xhr.onerror = () => reject(new Error('network error'));
    xhr.send(file);
  });
}

async function uploadAll(files) {
  err.textContent = '';
  result.innerHTML = '';
  bar.style.display = 'block';
  const names = [];
  try {
    for (const file of files) {
      bar.value = 0;
      names.push(await uploadOne(file));
    }
    await refresh();
    showResults(names);
  } catch (e) {
    err.textContent = 'Upload failed: ' + e.message;
  } finally {
    bar.style.display = 'none';
  }
}

fileInput.addEventListener('change', () => uploadAll(fileInput.files));
drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', (e) => {
  e.preventDefault();
  drop.classList.remove('over');
  uploadAll(e.dataTransfer.files);
});

refresh();
</script>
</body>
</html>
"""


def sanitize_name(raw):
    """Return a safe basename inside the upload dir, or raise ValueError.

    Strips any directory components and rejects names that, after stripping,
    are empty or refer to the current/parent directory.
    """
    if raw is None:
        raise ValueError("missing name")
    # strip() is intentional normalization: a name that is only whitespace
    # (or whitespace around separators) must end up empty and be rejected.
    name = os.path.basename(raw.strip())
    if not name or name in (".", ".."):
        raise ValueError("invalid name")
    # basename already removed separators; guard against any residual.
    if "/" in name or "\\" in name or os.sep in name:
        raise ValueError("invalid name")
    return name


def unique_path(directory, name):
    """Return an absolute path in `directory` for `name` that does not exist.

    On collision, inserts ` (n)` before the extension: a.txt -> a(1).txt.
    Bounded at 999 to avoid an unbounded loop; raises RuntimeError if exhausted.
    """
    candidate = os.path.join(directory, name)
    if not os.path.exists(candidate):
        return candidate
    stem, ext = os.path.splitext(name)
    for n in range(1, 1000):
        candidate = os.path.join(directory, f"{stem}({n}){ext}")
        if not os.path.exists(candidate):
            return candidate
    raise RuntimeError("too many name collisions")


class IncompleteUpload(Exception):
    """Client sent fewer bytes than Content-Length promised (disconnected)."""


def stream_body(reader, dst, remaining):
    """Copy exactly `remaining` bytes from `reader` to `dst` in CHUNK windows.

    Raises IncompleteUpload if the source ends before `remaining` bytes are read.
    """
    while remaining > 0:
        chunk = reader.read(min(CHUNK, remaining))
        if not chunk:
            raise IncompleteUpload()
        dst.write(chunk)
        remaining -= len(chunk)


def list_files(directory):
    """Return file entries (name, size, mtime), newest mtime first, excluding .part.

    `mtime` is integer epoch seconds; the client renders it in local time.
    """
    entries = []
    for name in os.listdir(directory):
        if name.endswith(".part"):
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        st = os.stat(path)
        # Sort on full-precision mtime so sub-second-apart files order correctly;
        # report it truncated to whole epoch seconds for the client.
        entries.append(
            {"name": name, "size": st.st_size, "mtime": st.st_mtime}
        )
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    for e in entries:
        e["mtime"] = int(e["mtime"])
    return entries


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status, text):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/api/files":
            self._send_json(200, list_files(UPLOAD_DIR))
        elif parsed.path.startswith("/download/"):
            self._serve_download(parsed.path[len("/download/"):])
        else:
            self._send_text(404, "not found")

    def _serve_download(self, raw_name):
        try:
            name = sanitize_name(unquote(raw_name))
        except ValueError:
            self._send_text(400, "invalid name")
            return
        path = os.path.join(UPLOAD_DIR, name)
        if not os.path.isfile(path):
            self._send_text(404, "not found")
            return
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header(
            "Content-Disposition", f'attachment; filename="{name}"'
        )
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def do_PUT(self):
        parsed = urlparse(self.path)
        if parsed.path != "/upload":
            self._send_text(404, "not found")
            return

        qs = parse_qs(parsed.query)
        raw_name = qs.get("name", [None])[0]
        try:
            name = sanitize_name(raw_name)
        except ValueError:
            self._send_text(400, "invalid name")
            return

        length_header = self.headers.get("Content-Length")
        try:
            remaining = int(length_header)
            if remaining < 0:
                raise ValueError
        except (TypeError, ValueError):
            self._send_text(400, "Content-Length required")
            return

        tmp_path = os.path.join(UPLOAD_DIR, f".{uuid.uuid4().hex}.part")
        try:
            with open(tmp_path, "wb") as f:
                stream_body(self.rfile, f, remaining)
            final_path = unique_path(UPLOAD_DIR, name)
            os.rename(tmp_path, final_path)
        except IncompleteUpload:
            # Client fault: fewer bytes than promised.
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            self._send_text(400, "incomplete upload")
            return
        except (OSError, RuntimeError):
            # Server fault: disk error, or collision space exhausted.
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            self._send_text(500, "upload failed")
            return

        self._send_json(201, {"name": os.path.basename(final_path)})


def main():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Serving {UPLOAD_DIR} on http://{HOST}:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
