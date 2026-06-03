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
  .res .fail { color: #b00; }
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
  if (!res.ok) throw new Error('file list HTTP ' + res.status);
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

function addResult(name, ok, detail) {
  const row = document.createElement('div');
  row.className = 'res';
  const label = document.createElement('span');
  if (ok) {
    label.className = 'ok';
    label.textContent = '✓ ' + name;
    const url = document.createElement('input');
    url.className = 'url';
    url.type = 'text';
    url.readOnly = true;
    url.value = dlUrl(name);
    url.addEventListener('focus', () => url.select());
    row.append(label, url, copyButton(name));
  } else {
    label.className = 'fail';
    label.textContent = '✗ ' + name + ' — ' + detail;
    row.append(label);
  }
  result.append(row);
}

const UPLOAD_CHUNK_SIZE = 90 * 1024 * 1024; // 90 MB per chunk, safely under tunnel limit

function generateUploadId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint8Array(16)))
    .map(b => b.toString(16).padStart(2, '0')).join('');
}

function errorDetail(xhr) {
  if (xhr.status === 0) return 'connection dropped (network, timeout, or tunnel down)';
  const body = (xhr.responseText || '').trim();
  if (body && body.length <= 120) return body + ' (HTTP ' + xhr.status + ')';
  return 'HTTP ' + xhr.status;
}

function xhrPut(url, body, onProgress) {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', url);
    xhr.upload.onprogress = onProgress;
    xhr.onload = () => {
      if (xhr.status === 200 || xhr.status === 201) {
        let name = null;
        try { name = JSON.parse(xhr.responseText).name; } catch (_) {}
        resolve({ ok: true, status: xhr.status, name });
      } else {
        resolve({ ok: false, detail: errorDetail(xhr) });
      }
    };
    xhr.onerror = () => resolve({ ok: false, detail: 'network error' });
    xhr.send(body);
  });
}

async function uploadChunked(file) {
  const uploadId = generateUploadId();
  const numChunks = Math.ceil(file.size / UPLOAD_CHUNK_SIZE);
  let savedName = file.name;
  for (let i = 0; i < numChunks; i++) {
    const start = i * UPLOAD_CHUNK_SIZE;
    const blob = file.slice(start, Math.min(start + UPLOAD_CHUNK_SIZE, file.size));
    const url = '/upload?name=' + encodeURIComponent(file.name)
      + '&upload_id=' + encodeURIComponent(uploadId)
      + '&chunk=' + i
      + '&total=' + numChunks;
    const r = await xhrPut(url, blob, (e) => {
      if (e.lengthComputable)
        bar.value = ((i * UPLOAD_CHUNK_SIZE + e.loaded) / file.size) * 100;
    });
    if (!r.ok) return { ok: false, name: file.name, detail: r.detail };
    if (r.name) savedName = r.name;
  }
  return { ok: true, name: savedName };
}

function uploadOne(file) {
  if (file.size > UPLOAD_CHUNK_SIZE) return uploadChunked(file);
  // Resolves with a per-file result and never rejects, so one file's failure
  // does not abort the rest of the batch.
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', '/upload?name=' + encodeURIComponent(file.name));
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) bar.value = (e.loaded / e.total) * 100;
    };
    xhr.onload = () => {
      if (xhr.status === 201) {
        // Server returns the final saved name (post-collision, e.g. a(1).txt).
        let name = file.name;
        try { name = JSON.parse(xhr.responseText).name; } catch (e) {}
        resolve({ ok: true, name });
      } else {
        resolve({ ok: false, name: file.name, detail: errorDetail(xhr) });
      }
    };
    xhr.onerror = () => resolve({ ok: false, name: file.name, detail: 'network error' });
    xhr.send(file);
  });
}

async function uploadAll(files) {
  err.textContent = '';
  result.innerHTML = '';
  bar.style.display = 'block';
  let anyOk = false;
  try {
    for (const file of files) {
      bar.value = 0;
      const r = await uploadOne(file);
      // Report each file immediately, with its copy link taken straight from
      // the upload response — independent of the list refresh below.
      addResult(r.name, r.ok, r.detail);
      if (r.ok) anyOk = true;
    }
  } finally {
    bar.style.display = 'none';
  }
  if (anyOk) {
    // The file list is a nice-to-have; a refresh failure must NOT be reported
    // as an upload failure (each upload already reported its own status).
    try {
      await refresh();
    } catch (e) {
      err.textContent = 'Uploaded OK, but the file list could not refresh: ' + e.message;
    }
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

        upload_id = qs.get("upload_id", [None])[0]
        if upload_id is not None:
            chunk_str = qs.get("chunk", [None])[0]
            total_str = qs.get("total", [None])[0]
            self._handle_chunk(name, upload_id, chunk_str, total_str)
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

    def _handle_chunk(self, name, upload_id, chunk_str, total_str):
        if not upload_id or len(upload_id) > 64 or not all(
            c.isalnum() or c == "-" for c in upload_id
        ):
            self._send_text(400, "invalid upload_id")
            return

        try:
            chunk_index = int(chunk_str)
            total_chunks = int(total_str)
            if chunk_index < 0 or total_chunks < 1 or chunk_index >= total_chunks:
                raise ValueError
        except (TypeError, ValueError):
            self._send_text(400, "invalid chunk or total")
            return

        length_header = self.headers.get("Content-Length")
        try:
            remaining = int(length_header)
            if remaining < 0:
                raise ValueError
        except (TypeError, ValueError):
            self._send_text(400, "Content-Length required")
            return

        chunk_path = os.path.join(UPLOAD_DIR, f".{upload_id}.c{chunk_index}.part")
        try:
            with open(chunk_path, "wb") as f:
                stream_body(self.rfile, f, remaining)
        except IncompleteUpload:
            if os.path.exists(chunk_path):
                os.remove(chunk_path)
            self._send_text(400, "incomplete upload")
            return
        except OSError:
            if os.path.exists(chunk_path):
                os.remove(chunk_path)
            self._send_text(500, "upload failed")
            return

        if chunk_index < total_chunks - 1:
            self._send_json(200, {"chunk": chunk_index})
            return

        # Last chunk arrived — assemble.
        chunk_paths = [
            os.path.join(UPLOAD_DIR, f".{upload_id}.c{i}.part")
            for i in range(total_chunks)
        ]
        for cp in chunk_paths:
            if not os.path.exists(cp):
                for cp2 in chunk_paths:
                    if os.path.exists(cp2):
                        os.remove(cp2)
                self._send_text(500, "missing chunk")
                return

        tmp_path = os.path.join(UPLOAD_DIR, f".{uuid.uuid4().hex}.part")
        try:
            with open(tmp_path, "wb") as out:
                for cp in chunk_paths:
                    with open(cp, "rb") as inp:
                        while True:
                            data = inp.read(CHUNK)
                            if not data:
                                break
                            out.write(data)
            final_path = unique_path(UPLOAD_DIR, name)
            os.rename(tmp_path, final_path)
        except (OSError, RuntimeError):
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            for cp in chunk_paths:
                if os.path.exists(cp):
                    os.remove(cp)
            self._send_text(500, "assembly failed")
            return

        for cp in chunk_paths:
            if os.path.exists(cp):
                os.remove(cp)

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
