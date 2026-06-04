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
  ul { list-style: none; padding: 0; margin: 0 0 1rem; }
  h3.uploader { margin: 1rem 0 .3rem; font-size: 1rem; color: #333; border-bottom: 2px solid #ddd; padding-bottom: .2rem; }
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
<div id="list"></div>
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

function dlUrl(uploader, name) {
  // location.origin -> the public tunnel URL through Cloudflare, 127.0.0.1
  // locally, so the copied link is always correct for the viewer. Unsorted
  // (root) files have no handle segment.
  const base = location.origin + '/download/';
  return uploader === '(unsorted)'
    ? base + encodeURIComponent(name)
    : base + encodeURIComponent(uploader) + '/' + encodeURIComponent(name);
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

function copyButton(uploader, name, cls) {
  const btn = document.createElement('button');
  btn.type = 'button';
  if (cls) btn.className = cls;
  btn.textContent = 'Copy';
  btn.addEventListener('click', () => copy(dlUrl(uploader, name), btn));
  return btn;
}

async function refresh() {
  const res = await fetch('/api/files');
  if (!res.ok) throw new Error('file list HTTP ' + res.status);
  const files = await res.json();
  list.innerHTML = '';
  // Partition by uploader, preserving the newest-first order within each group.
  const groups = new Map();
  for (const f of files) {
    if (!groups.has(f.uploader)) groups.set(f.uploader, []);
    groups.get(f.uploader).push(f);
  }
  for (const [uploader, items] of groups) {
    const head = document.createElement('h3');
    head.className = 'uploader';
    head.textContent = uploader;
    list.append(head);
    const ul = document.createElement('ul');
    for (const f of items) {
      const li = document.createElement('li');
      const a = document.createElement('a');
      a.href = dlUrl(f.uploader, f.name);
      a.textContent = f.name;
      const meta = document.createElement('span');
      meta.className = 'meta';
      const time = document.createElement('span');
      time.className = 'time';
      time.textContent = fmtTime(f.mtime);
      const size = document.createElement('span');
      size.className = 'size';
      size.textContent = human(f.size);
      meta.append(time, size, copyButton(f.uploader, f.name, 'copy'));
      li.append(a, meta);
      ul.append(li);
    }
    list.append(ul);
  }
}

function addResult(name, uploader, ok, detail) {
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
    url.value = dlUrl(uploader, name);
    url.addEventListener('focus', () => url.select());
    row.append(label, url, copyButton(uploader, name));
  } else {
    label.className = 'fail';
    label.textContent = '✗ ' + name + ' — ' + detail;
    row.append(label);
  }
  result.append(row);
}

function errorDetail(xhr) {
  if (xhr.status === 413) return 'too large for the tunnel (uploads are capped near 100 MB)';
  if (xhr.status === 0) return 'connection dropped (network, timeout, or tunnel down)';
  const body = (xhr.responseText || '').trim();
  if (body && body.length <= 120) return body + ' (HTTP ' + xhr.status + ')';
  return 'HTTP ' + xhr.status;
}

function uploadOne(file) {
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
        // Server returns the final saved name (post-collision) and the handle
        // the file was filed under, so the result link is built correctly.
        let name = file.name, uploader = '';
        try {
          const r = JSON.parse(xhr.responseText);
          name = r.name; uploader = r.uploader;
        } catch (e) {}
        resolve({ ok: true, name, uploader });
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
      addResult(r.name, r.uploader, r.ok, r.detail);
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


def handle_from_email(email):
    """Derive a safe per-uploader folder handle from a Cloudflare Access email.

    Uses the local part (text before '@'), run through sanitize_name. Falls back
    to 'unknown' for a missing, blank, or unusable value so uploads always land
    somewhere (e.g. local dev runs with no Cloudflare header).
    """
    if not email:
        return "unknown"
    local = email.split("@", 1)[0]
    try:
        return sanitize_name(local)
    except ValueError:
        return "unknown"


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


UNSORTED = "(unsorted)"


def list_files(directory):
    """Return file entries across uploader folders, newest mtime first.

    Each entry is {uploader, name, size, mtime}. A file lives in
    `directory/<uploader>/<name>`; files placed directly in `directory` (e.g.
    dropped in by an admin) are grouped under '(unsorted)'. `mtime` is integer
    epoch seconds; the client renders it in local time. Excludes .part temps.
    """
    entries = []
    for entry in sorted(os.listdir(directory)):
        path = os.path.join(directory, entry)
        if os.path.isdir(path):
            for name in os.listdir(path):
                if name.endswith(".part"):
                    continue
                fpath = os.path.join(path, name)
                if not os.path.isfile(fpath):
                    continue
                st = os.stat(fpath)
                entries.append({"uploader": entry, "name": name,
                                "size": st.st_size, "mtime": st.st_mtime})
        elif os.path.isfile(path) and not entry.endswith(".part"):
            st = os.stat(path)
            entries.append({"uploader": UNSORTED, "name": entry,
                            "size": st.st_size, "mtime": st.st_mtime})
    # Sort on full-precision mtime so sub-second-apart files order correctly;
    # report it truncated to whole epoch seconds for the client.
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

    def _serve_download(self, raw):
        # `raw` is "<name>" (a loose root file) or "<handle>/<name>" (an
        # uploader folder), each segment URL-encoded. Both segments are
        # sanitized, so neither can escape UPLOAD_DIR.
        parts = raw.split("/")
        try:
            if len(parts) == 1:
                segments = [sanitize_name(unquote(parts[0]))]
            elif len(parts) == 2:
                segments = [sanitize_name(unquote(parts[0])),
                            sanitize_name(unquote(parts[1]))]
            else:
                raise ValueError
        except ValueError:
            self._send_text(400, "invalid name")
            return
        name = segments[-1]
        path = os.path.join(UPLOAD_DIR, *segments)
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

        handle = handle_from_email(
            self.headers.get("Cf-Access-Authenticated-User-Email")
        )
        handle_dir = os.path.join(UPLOAD_DIR, handle)
        os.makedirs(handle_dir, exist_ok=True)

        tmp_path = os.path.join(handle_dir, f".{uuid.uuid4().hex}.part")
        try:
            with open(tmp_path, "wb") as f:
                stream_body(self.rfile, f, remaining)
            final_path = unique_path(handle_dir, name)
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

        self._send_json(201, {"name": os.path.basename(final_path), "uploader": handle})


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
