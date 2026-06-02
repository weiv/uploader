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
  li { display: flex; justify-content: space-between; padding: .4rem 0; border-bottom: 1px solid #eee; }
  .size { color: #888; font-variant-numeric: tabular-nums; }
  #err { color: #b00; min-height: 1.2em; }
</style>
</head>
<body>
<h1>Uploader</h1>
<div id="drop">Ovde baci datoteku ili <input type="file" id="file" multiple></div>
<progress id="bar" max="100" value="0"></progress>
<div id="err"></div>
<h2>Files</h2>
<ul id="list"></ul>
<script>
const drop = document.getElementById('drop');
const fileInput = document.getElementById('file');
const bar = document.getElementById('bar');
const err = document.getElementById('err');
const list = document.getElementById('list');

function human(n) {
  const u = ['B','KB','MB','GB','TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(1)) + ' ' + u[i];
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
    const span = document.createElement('span');
    span.className = 'size';
    span.textContent = human(f.size);
    li.append(a, span);
    list.append(li);
  }
}

function uploadOne(file) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', '/upload?name=' + encodeURIComponent(file.name));
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) bar.value = (e.loaded / e.total) * 100;
    };
    xhr.onload = () => (xhr.status === 201 ? resolve() : reject(new Error(xhr.responseText || xhr.status)));
    xhr.onerror = () => reject(new Error('network error'));
    xhr.send(file);
  });
}

async function uploadAll(files) {
  err.textContent = '';
  bar.style.display = 'block';
  try {
    for (const file of files) {
      bar.value = 0;
      await uploadOne(file);
    }
    await refresh();
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
    """Return file entries (name, size), newest mtime first, excluding .part."""
    entries = []
    for name in os.listdir(directory):
        if name.endswith(".part"):
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        st = os.stat(path)
        entries.append({"name": name, "size": st.st_size, "_mtime": st.st_mtime})
    entries.sort(key=lambda e: e["_mtime"], reverse=True)
    for e in entries:
        del e["_mtime"]
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
