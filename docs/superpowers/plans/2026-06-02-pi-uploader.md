# Pi Uploader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single stdlib-only Python 3 web server that lets a user upload files to, and download files from, one fixed directory on a Raspberry Pi, fronted by a Cloudflare tunnel for auth.

**Architecture:** One `server.py` built on `http.server.ThreadingHTTPServer` + a custom `BaseHTTPRequestHandler`. Uploads are sent as the raw PUT body and streamed to a `.<uuid>.part` temp file in 64 KB windows bounded by `Content-Length`, then atomically renamed to a sanitized, collision-free final name. Downloads stream from disk in 64 KB chunks. A single inline HTML page (no external assets) provides the UI. Provisioning is handled by `setup.sh` + a systemd unit running as an unprivileged no-login `uploader` user.

**Tech Stack:** Python 3 standard library only (`http.server`, `socketserver`, `os`, `uuid`, `json`, `urllib.parse`, `html`). Tests use `unittest` + `http.client`. Deployment via systemd on Raspberry Pi OS.

---

## File Structure

- `server.py` — the entire application: config, filename helpers, request handler, HTML page, `main()`. Single file by design (zero-dependency, easy to scp/inspect on a Pi).
- `test_server.py` — `unittest` suite; starts the server against a temp dir on an ephemeral port and exercises it over HTTP.
- `uploader.service` — systemd unit template.
- `setup.sh` — idempotent provisioning script (run with `sudo` on the Pi).
- `README.md` — setup, Cloudflare tunnel hookup, and admin instructions.

The filename/config helpers are written as module-level functions so tests can call them directly without HTTP.

---

## Conventions used throughout

- Buffer size constant: `CHUNK = 64 * 1024`.
- Config read once at import: `UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/srv/uploader/files")`, `PORT = int(os.environ.get("PORT", "8000"))`, `HOST = "127.0.0.1"`.
- `sanitize_name(raw)` returns a safe basename or raises `ValueError`.
- `unique_path(directory, name)` returns an absolute path that does not yet exist, applying the `name(1).ext` rule.
- All commits use Conventional Commit prefixes (`feat:`, `test:`, `chore:`, `docs:`).

---

## Task 1: Filename sanitization

**Files:**
- Create: `server.py`
- Test: `test_server.py`

- [ ] **Step 1: Write the failing test**

Create `test_server.py`:

```python
import unittest
import server


class SanitizeNameTests(unittest.TestCase):
    def test_plain_name_passes_through(self):
        self.assertEqual(server.sanitize_name("report.zip"), "report.zip")

    def test_strips_directory_components(self):
        self.assertEqual(server.sanitize_name("/etc/passwd"), "passwd")
        self.assertEqual(server.sanitize_name("sub/dir/file.txt"), "file.txt")

    def test_rejects_traversal_and_empties(self):
        for bad in ["", ".", "..", "../x", "a/../b/", "   ", "/"]:
            with self.assertRaises(ValueError):
                server.sanitize_name(bad)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_server.SanitizeNameTests -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server'` (or `AttributeError: sanitize_name`).

- [ ] **Step 3: Write minimal implementation**

Create `server.py`:

```python
import os

CHUNK = 64 * 1024


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest test_server.SanitizeNameTests -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add server.py test_server.py
git commit -m "feat: add filename sanitization"
```

---

## Task 2: Collision-free unique path

**Files:**
- Modify: `server.py`
- Test: `test_server.py`

- [ ] **Step 1: Write the failing test**

Add to `test_server.py`:

```python
import os
import tempfile


class UniquePathTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_returns_name_when_free(self):
        p = server.unique_path(self.dir, "a.txt")
        self.assertEqual(os.path.basename(p), "a.txt")

    def test_suffixes_on_collision(self):
        open(os.path.join(self.dir, "a.txt"), "w").close()
        p = server.unique_path(self.dir, "a.txt")
        self.assertEqual(os.path.basename(p), "a(1).txt")

    def test_increments_until_free(self):
        open(os.path.join(self.dir, "a.txt"), "w").close()
        open(os.path.join(self.dir, "a(1).txt"), "w").close()
        p = server.unique_path(self.dir, "a.txt")
        self.assertEqual(os.path.basename(p), "a(2).txt")

    def test_name_without_extension(self):
        open(os.path.join(self.dir, "README"), "w").close()
        p = server.unique_path(self.dir, "README")
        self.assertEqual(os.path.basename(p), "README(1)")

    def test_raises_when_exhausted(self):
        # Mock so every candidate "exists" — exercises the bound without
        # creating 1000 files (this repo lives on an iCloud-synced path).
        from unittest import mock
        with mock.patch("os.path.exists", return_value=True):
            with self.assertRaises(RuntimeError):
                server.unique_path(self.dir, "a.txt")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_server.UniquePathTests -v`
Expected: FAIL — `AttributeError: module 'server' has no attribute 'unique_path'`.

- [ ] **Step 3: Write minimal implementation**

Add to `server.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest test_server.UniquePathTests -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add server.py test_server.py
git commit -m "feat: add collision-free unique path resolver"
```

---

## Task 3: HTTP handler skeleton + `/api/files` listing

**Files:**
- Modify: `server.py`
- Test: `test_server.py`

- [ ] **Step 1: Write the failing test**

Add a test helper and listing test to `test_server.py`:

```python
import json
import threading
import http.client
from http.server import ThreadingHTTPServer


class ServerTestCase(unittest.TestCase):
    """Base: starts server.Handler against a temp dir on an ephemeral port."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._orig_dir = server.UPLOAD_DIR
        server.UPLOAD_DIR = self.dir
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        server.UPLOAD_DIR = self._orig_dir

    def conn(self):
        return http.client.HTTPConnection("127.0.0.1", self.port)

    def request(self, method, path, body=None, headers=None):
        c = self.conn()
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data


class ListingTests(ServerTestCase):
    def test_empty_listing(self):
        status, data = self.request("GET", "/api/files")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data), [])

    def test_lists_files_newest_first_excluding_part(self):
        # Create two real files plus a .part temp that must be hidden.
        import time
        with open(os.path.join(self.dir, "old.txt"), "w") as f:
            f.write("old")
        time.sleep(0.01)
        with open(os.path.join(self.dir, "new.txt"), "w") as f:
            f.write("newer")
        open(os.path.join(self.dir, ".abc.part"), "w").close()
        status, data = self.request("GET", "/api/files")
        self.assertEqual(status, 200)
        names = [e["name"] for e in json.loads(data)]
        self.assertEqual(names, ["new.txt", "old.txt"])
        sizes = {e["name"]: e["size"] for e in json.loads(data)}
        self.assertEqual(sizes["new.txt"], 5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_server.ListingTests -v`
Expected: FAIL — `AttributeError: module 'server' has no attribute 'Handler'`.

- [ ] **Step 3: Write minimal implementation**

Add to `server.py` (imports at top, then the handler):

```python
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote


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
        if parsed.path == "/api/files":
            self._send_json(200, list_files(UPLOAD_DIR))
        else:
            self._send_text(404, "not found")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest test_server.ListingTests -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add server.py test_server.py
git commit -m "feat: add HTTP handler with /api/files listing"
```

---

## Task 4: Streaming upload (`PUT /upload`)

**Files:**
- Modify: `server.py`
- Test: `test_server.py`

- [ ] **Step 1: Write the failing test**

Add to `test_server.py`:

```python
class UploadTests(ServerTestCase):
    def _put(self, name, payload):
        c = self.conn()
        headers = {"Content-Length": str(len(payload))}
        c.request("PUT", "/upload?name=" + name, body=payload, headers=headers)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_round_trips_bytes(self):
        status, data = self._put("hello.txt", b"hello world")
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(data)["name"], "hello.txt")
        with open(os.path.join(self.dir, "hello.txt"), "rb") as f:
            self.assertEqual(f.read(), b"hello world")

    def test_multi_chunk_payload(self):
        payload = os.urandom(server.CHUNK * 3 + 123)
        status, data = self._put("big.bin", payload)
        self.assertEqual(status, 201)
        with open(os.path.join(self.dir, "big.bin"), "rb") as f:
            self.assertEqual(f.read(), payload)

    def test_collision_autorenames_and_reports(self):
        self._put("a.txt", b"first")
        status, data = self._put("a.txt", b"second")
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(data)["name"], "a(1).txt")
        with open(os.path.join(self.dir, "a(1).txt"), "rb") as f:
            self.assertEqual(f.read(), b"second")

    def test_no_part_files_left_behind(self):
        self._put("a.txt", b"x")
        leftovers = [n for n in os.listdir(self.dir) if n.endswith(".part")]
        self.assertEqual(leftovers, [])

    def test_rejects_bad_name(self):
        status, _ = self._put("..", b"x")
        self.assertEqual(status, 400)

    def test_rejects_non_integer_content_length(self):
        # Send an explicit non-integer Content-Length. This deterministically
        # hits the 400 branch (int() raises) regardless of http.client version
        # quirks around auto-injecting Content-Length for bodyless requests.
        c = self.conn()
        c.putrequest("PUT", "/upload?name=x.txt")
        c.putheader("Content-Length", "notanumber")
        c.endheaders()
        r = c.getresponse()
        status = r.status
        r.read()
        c.close()
        self.assertEqual(status, 400)


import io


class StreamBodyTests(unittest.TestCase):
    def test_copies_exact_bytes(self):
        dst = io.BytesIO()
        server.stream_body(io.BytesIO(b"abcdef"), dst, 6)
        self.assertEqual(dst.getvalue(), b"abcdef")

    def test_short_read_raises_incomplete(self):
        # Source ends early (client disconnected): must raise, not silently
        # write a truncated file.
        dst = io.BytesIO()
        with self.assertRaises(server.IncompleteUpload):
            server.stream_body(io.BytesIO(b"abc"), dst, 6)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_server.UploadTests test_server.StreamBodyTests -v`
Expected: FAIL — `StreamBodyTests` fails with `AttributeError` (no `stream_body`/`IncompleteUpload`), and `UploadTests` hit the 404 branch (no `do_PUT`), so assertions on 201 fail.

- [ ] **Step 3: Write minimal implementation**

Add `import uuid` to the top of `server.py`. Then add the read-loop helper and
its exception at module level (near `unique_path`, above the `Handler` class) so
they can be unit-tested directly:

```python
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
```

Then add this method to `Handler`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest test_server.UploadTests test_server.StreamBodyTests -v`
Expected: PASS (6 upload tests + 2 stream_body tests).

- [ ] **Step 5: Commit**

```bash
git add server.py test_server.py
git commit -m "feat: add streaming PUT upload with atomic rename"
```

---

## Task 5: Streaming download (`GET /download/<name>`)

**Files:**
- Modify: `server.py`
- Test: `test_server.py`

- [ ] **Step 1: Write the failing test**

Add to `test_server.py`:

```python
from urllib.parse import quote


class DownloadTests(ServerTestCase):
    def test_downloads_exact_content(self):
        payload = os.urandom(server.CHUNK * 2 + 7)
        with open(os.path.join(self.dir, "f.bin"), "wb") as f:
            f.write(payload)
        status, data = self.request("GET", "/download/f.bin")
        self.assertEqual(status, 200)
        self.assertEqual(data, payload)

    def test_missing_file_404(self):
        status, _ = self.request("GET", "/download/nope.txt")
        self.assertEqual(status, 404)

    def test_rejects_traversal(self):
        status, _ = self.request("GET", "/download/" + quote("../server.py"))
        self.assertIn(status, (400, 404))

    def test_sets_attachment_header(self):
        with open(os.path.join(self.dir, "f.txt"), "w") as f:
            f.write("hi")
        c = self.conn()
        c.request("GET", "/download/f.txt")
        r = c.getresponse()
        disp = r.getheader("Content-Disposition")
        r.read()
        c.close()
        self.assertIn("attachment", disp)
        self.assertIn("f.txt", disp)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_server.DownloadTests -v`
Expected: FAIL — `/download/...` currently returns 404 for the success cases.

- [ ] **Step 3: Write minimal implementation**

In `server.py`, update `do_GET` to route downloads. Replace the existing `do_GET` with:

```python
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/files":
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest test_server.DownloadTests -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add server.py test_server.py
git commit -m "feat: add streaming download endpoint"
```

---

## Task 6: HTML page (`GET /`) and `main()`

**Files:**
- Modify: `server.py`
- Test: `test_server.py`

- [ ] **Step 1: Write the failing test**

Add to `test_server.py`:

```python
class PageTests(ServerTestCase):
    def test_root_serves_html(self):
        c = self.conn()
        c.request("GET", "/")
        r = c.getresponse()
        ctype = r.getheader("Content-Type")
        body = r.read().decode("utf-8")
        c.close()
        self.assertEqual(r.status, 200)
        self.assertIn("text/html", ctype)
        # Sanity: the page references the upload + listing endpoints.
        self.assertIn("/upload", body)
        self.assertIn("/api/files", body)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_server.PageTests -v`
Expected: FAIL — `/` returns 404.

- [ ] **Step 3: Write minimal implementation**

Add the page constant near the top of `server.py` (after imports):

```python
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
<div id="drop">Drop files here or <input type="file" id="file" multiple></div>
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
```

Update `do_GET` so the root path serves the page. Replace the `do_GET` method body with:

```python
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
```

Add `main()` and the entrypoint at the bottom of `server.py`:

```python
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
```

Also ensure `HOST` and `PORT` are defined with the other config near the top of `server.py`:

```python
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/srv/uploader/files")
HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8000"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest test_server.PageTests -v`
Expected: PASS (1 test).

- [ ] **Step 5: Run the full suite**

Run: `python3 -m unittest test_server -v`
Expected: PASS (all tests across all classes).

- [ ] **Step 6: Manual smoke test**

```bash
UPLOAD_DIR=/tmp/uptest PORT=8000 python3 server.py
```
Open `http://127.0.0.1:8000`, drag a file in, confirm it appears in the list and downloads correctly. Ctrl-C to stop.

- [ ] **Step 7: Commit**

```bash
git add server.py test_server.py
git commit -m "feat: add HTML upload page and server entrypoint"
```

---

## Task 7: systemd unit

**Files:**
- Create: `uploader.service`

- [ ] **Step 1: Write the unit file**

Create `uploader.service`:

```ini
[Unit]
Description=Pi Uploader
After=network.target

[Service]
User=uploader
Group=uploader-admin
UMask=002
WorkingDirectory=/srv/uploader
ExecStart=/usr/bin/python3 /srv/uploader/server.py
Restart=on-failure

# Hardening
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/srv/uploader/files

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Validate syntax locally (if systemd present)**

Run: `systemd-analyze verify ./uploader.service 2>&1 || echo "skipped (no systemd, e.g. on macOS)"`
Expected: no errors, or the skip message on a non-systemd host.

- [ ] **Step 3: Commit**

```bash
git add uploader.service
git commit -m "chore: add systemd unit"
```

---

## Task 8: setup.sh provisioning script

**Files:**
- Create: `setup.sh`

- [ ] **Step 1: Write the script**

Create `setup.sh`:

```bash
#!/usr/bin/env bash
# Idempotent provisioning for the Pi uploader. Run with sudo on the Pi.
#   sudo ./setup.sh
set -euo pipefail

ADMIN_USER="${ADMIN_USER:-weiv}"     # your interactive Pi user, added to the admin group
SERVICE_USER="uploader"
ADMIN_GROUP="uploader-admin"
BASE="/srv/uploader"
FILES="$BASE/files"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo." >&2
  exit 1
fi

# Admin group (contains both the service user and the human admin).
getent group "$ADMIN_GROUP" >/dev/null || groupadd "$ADMIN_GROUP"

# Service user: system account, no login, home at $BASE.
id -u "$SERVICE_USER" >/dev/null 2>&1 || \
  useradd --system --home "$BASE" --shell /usr/sbin/nologin "$SERVICE_USER"

# Memberships.
usermod -aG "$ADMIN_GROUP" "$SERVICE_USER"
if id -u "$ADMIN_USER" >/dev/null 2>&1; then
  usermod -aG "$ADMIN_GROUP" "$ADMIN_USER"
else
  echo "Warning: admin user '$ADMIN_USER' not found; skipping group add." >&2
fi

# Directories. setgid so new files inherit the admin group; group-writable.
install -d -o "$SERVICE_USER" -g "$ADMIN_GROUP" -m 0755 "$BASE"
install -d -o "$SERVICE_USER" -g "$ADMIN_GROUP" -m 2775 "$FILES"

# Application code, group-writable so the admin can edit it.
install -o "$SERVICE_USER" -g "$ADMIN_GROUP" -m 0664 "$SRC_DIR/server.py" "$BASE/server.py"

# systemd unit.
install -o root -g root -m 0644 "$SRC_DIR/uploader.service" /etc/systemd/system/uploader.service
systemctl daemon-reload
systemctl enable uploader.service
# restart (not just `enable --now`) so re-runs pick up an edited server.py.
systemctl restart uploader.service

echo "Done. Status:"
systemctl --no-pager status uploader.service || true
echo
echo "Point your Cloudflare tunnel at http://127.0.0.1:8000"
echo "Note: group changes take effect on the admin user's next login."
```

- [ ] **Step 2: Make it executable and lint**

```bash
chmod +x setup.sh
bash -n setup.sh && echo "syntax OK"
```
Expected: `syntax OK`. If `shellcheck` is installed, also run `shellcheck setup.sh` and address warnings.

- [ ] **Step 3: Commit**

```bash
git add setup.sh
git commit -m "chore: add idempotent setup script"
```

---

## Task 9: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write the README**

Create `README.md`:

```markdown
# Pi Uploader

A tiny, dependency-free web "dropbox" for a Raspberry Pi: upload files to and
download them from one fixed directory. Authentication is handled by the
Cloudflare tunnel in front of it, so the app has no login of its own.

## What it is

- `server.py` — the whole app (Python 3 standard library only).
- `test_server.py` — test suite (`python3 -m unittest test_server -v`).
- `uploader.service` — systemd unit.
- `setup.sh` — one-shot provisioning.

## Quick local run

    UPLOAD_DIR=/tmp/uptest PORT=8000 python3 server.py

Then open http://127.0.0.1:8000.

## Install on the Pi

Copy this directory to the Pi, then:

    sudo ./setup.sh

This is idempotent (safe to re-run, e.g. after editing `server.py`). It:

1. Creates the `uploader-admin` group.
2. Creates the `uploader` system user (no login, home `/srv/uploader`).
3. Adds both `uploader` and your admin user (`weiv` by default; override with
   `ADMIN_USER=...`) to `uploader-admin`.
4. Creates `/srv/uploader/files` owned `uploader:uploader-admin`, mode `2775`
   (setgid, group-writable) so uploads inherit the group.
5. Installs `server.py` to `/srv/uploader/server.py` (group-writable).
6. Installs, enables, and starts `uploader.service`.

After first run, log out and back in as the admin user so the new group
membership takes effect.

### Manual equivalent

    sudo groupadd uploader-admin
    sudo useradd --system --home /srv/uploader --shell /usr/sbin/nologin uploader
    sudo usermod -aG uploader-admin uploader
    sudo usermod -aG uploader-admin weiv
    sudo install -d -o uploader -g uploader-admin -m 0755 /srv/uploader
    sudo install -d -o uploader -g uploader-admin -m 2775 /srv/uploader/files
    sudo install -o uploader -g uploader-admin -m 0664 server.py /srv/uploader/server.py
    sudo install -m 0644 uploader.service /etc/systemd/system/uploader.service
    sudo systemctl daemon-reload && sudo systemctl enable --now uploader.service

## Cloudflare tunnel

Point a tunnel at the local server and let Cloudflare Access enforce auth:

    cloudflared tunnel --url http://127.0.0.1:8000

Or, for a named tunnel, add an ingress rule in `~/.cloudflared/config.yml`:

    ingress:
      - hostname: files.example.com
        service: http://127.0.0.1:8000
      - service: http_status:404

Then put a Cloudflare Access policy in front of `files.example.com` to require
authentication. The app trusts that anyone who reaches it is already
authenticated, so do **not** expose port 8000 directly.

## Administering it (as `weiv`)

- Files live in `/srv/uploader/files`. Because you're in `uploader-admin` and the
  dir is setgid + group-writable, you can read, move, rename, and delete uploaded
  files directly — no sudo.
- Service control: `sudo systemctl restart|stop|status uploader`.
- Logs: `journalctl -u uploader -f`.
- Edit the app: change `/srv/uploader/server.py` (group-writable), then
  `sudo systemctl restart uploader`.

## Behavior notes

- Uploads stream to disk (handles large files without buffering in RAM).
- Filenames are sanitized to a basename; path traversal is rejected.
- Name collisions auto-rename (`report.zip` → `report(1).zip`); nothing is
  overwritten.
- Config via env vars: `UPLOAD_DIR` (default `/srv/uploader/files`), `PORT`
  (default `8000`). The server always binds `127.0.0.1`.
- If you point `UPLOAD_DIR` at a directory outside `/srv/uploader/files`, you must
  also update `ReadWritePaths=` in `uploader.service` — `ProtectSystem=strict`
  makes everything else read-only, so writes will fail otherwise — and give the new
  directory the same `uploader:uploader-admin` owner and `2775` mode.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README"
```

---

## Final verification

- [ ] **Run the full test suite**

Run: `python3 -m unittest test_server -v`
Expected: all tests PASS.

- [ ] **Confirm clean tree**

Run: `git status`
Expected: nothing to commit, working tree clean.
