# Upload Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show who uploaded each file by storing each uploader's files in their own subdirectory and grouping the file list by uploader in the web UI.

**Architecture:** The `PUT /upload` handler derives a handle from Cloudflare Access's `Cf-Access-Authenticated-User-Email` header (local part, sanitized; `unknown` if absent) and streams the file into `UPLOAD_DIR/<handle>/`. `list_files` walks those folders (plus loose root files under `(unsorted)`) returning `{uploader, name, size, mtime}`. Downloads route `/download/<handle>/<name>`, with one-segment `/download/<name>` kept for root files. The page groups the flat list by uploader.

**Tech Stack:** Stdlib-only Python 3 (`http.server`), `unittest`. No new dependencies. Single `server.py`.

**Reference:** Design spec at `docs/superpowers/specs/2026-06-03-upload-attribution-design.md`.

---

## File Structure

- **Modify `server.py`** — add `handle_from_email`; rewrite `list_files`; update `do_PUT`, `_serve_download`, and the inline HTML `PAGE`. This stays one file by project constraint.
- **Modify `test_server.py`** — add a `HandleFromEmailTests` class; extend the `_put` helper with custom headers; update `UploadTests`, `ListingTests`, `DownloadTests`, `PageTests`.
- **Modify `README.md` and `CLAUDE.md`** — document the per-uploader layout, the new download route, and the `/api/files` shape.

No new files: the project is deliberately a single stdlib script.

---

## Task 1: `handle_from_email` helper

**Files:**
- Modify: `server.py` (add a module-level function after `sanitize_name`, ~line 238)
- Test: `test_server.py` (new `HandleFromEmailTests` class)

- [ ] **Step 1: Write the failing test**

Add this class to `test_server.py` (place it right after `SanitizeNameTests`):

```python
class HandleFromEmailTests(unittest.TestCase):
    def test_uses_local_part(self):
        self.assertEqual(server.handle_from_email("alice@acme.com"), "alice")

    def test_keeps_dots_in_local_part(self):
        self.assertEqual(server.handle_from_email("v.weinstein@corp.com"), "v.weinstein")

    def test_missing_email_is_unknown(self):
        self.assertEqual(server.handle_from_email(None), "unknown")
        self.assertEqual(server.handle_from_email(""), "unknown")

    def test_unusable_local_part_is_unknown(self):
        # No local part, or a local part that sanitizes to nothing usable.
        self.assertEqual(server.handle_from_email("@acme.com"), "unknown")
        self.assertEqual(server.handle_from_email("   @acme.com"), "unknown")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_server.HandleFromEmailTests -v`
Expected: FAIL with `AttributeError: module 'server' has no attribute 'handle_from_email'`

- [ ] **Step 3: Write minimal implementation**

In `server.py`, add this function immediately after `sanitize_name` (after line 238):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest test_server.HandleFromEmailTests -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add server.py test_server.py
git commit -m "feat: derive uploader handle from Cloudflare Access email"
```

---

## Task 2: Upload streams into the uploader's folder

**Files:**
- Modify: `server.py` (`do_PUT`, ~lines 357-399)
- Test: `test_server.py` (`UploadTests`, ~lines 138-191)

- [ ] **Step 1: Update the `_put` helper and existing tests, add new tests**

Replace the `_put` helper at the top of `UploadTests` so it can send custom headers:

```python
    def _put(self, name, payload, headers=None):
        c = self.conn()
        hdrs = {"Content-Length": str(len(payload))}
        if headers:
            hdrs.update(headers)
        c.request("PUT", "/upload?name=" + name, body=payload, headers=hdrs)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data
```

Replace the four body-writing assertions in `UploadTests` to expect the `unknown`
folder (no header sent), and the response to carry `uploader`:

```python
    def test_round_trips_bytes(self):
        status, data = self._put("hello.txt", b"hello world")
        self.assertEqual(status, 201)
        body = json.loads(data)
        self.assertEqual(body["name"], "hello.txt")
        self.assertEqual(body["uploader"], "unknown")
        with open(os.path.join(self.dir, "unknown", "hello.txt"), "rb") as f:
            self.assertEqual(f.read(), b"hello world")

    def test_multi_chunk_payload(self):
        payload = os.urandom(server.CHUNK * 3 + 123)
        status, data = self._put("big.bin", payload)
        self.assertEqual(status, 201)
        with open(os.path.join(self.dir, "unknown", "big.bin"), "rb") as f:
            self.assertEqual(f.read(), payload)

    def test_collision_autorenames_and_reports(self):
        self._put("a.txt", b"first")
        status, data = self._put("a.txt", b"second")
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(data)["name"], "a(1).txt")
        with open(os.path.join(self.dir, "unknown", "a(1).txt"), "rb") as f:
            self.assertEqual(f.read(), b"second")

    def test_no_part_files_left_behind(self):
        self._put("a.txt", b"x")
        handle_dir = os.path.join(self.dir, "unknown")
        leftovers = [n for n in os.listdir(handle_dir) if n.endswith(".part")]
        self.assertEqual(leftovers, [])
```

Add two new tests to `UploadTests`:

```python
    def test_uses_handle_from_access_header(self):
        status, data = self._put(
            "report.zip", b"data",
            headers={"Cf-Access-Authenticated-User-Email": "alice@acme.com"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(data)["uploader"], "alice")
        with open(os.path.join(self.dir, "alice", "report.zip"), "rb") as f:
            self.assertEqual(f.read(), b"data")

    def test_same_name_different_handles_coexist(self):
        self._put("report.zip", b"A",
                  headers={"Cf-Access-Authenticated-User-Email": "alice@acme.com"})
        self._put("report.zip", b"B",
                  headers={"Cf-Access-Authenticated-User-Email": "bob@acme.com"})
        with open(os.path.join(self.dir, "alice", "report.zip"), "rb") as f:
            self.assertEqual(f.read(), b"A")
        with open(os.path.join(self.dir, "bob", "report.zip"), "rb") as f:
            self.assertEqual(f.read(), b"B")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_server.UploadTests -v`
Expected: FAIL — files are written to `self.dir` root (old behavior), so the
`self.dir/unknown/...` opens raise `FileNotFoundError` and `uploader` is missing.

- [ ] **Step 3: Update `do_PUT`**

In `server.py`, replace the body of `do_PUT` from the temp-path line through the
final response. Replace this block (currently ~lines 380-399):

```python
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

with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_server.UploadTests -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add server.py test_server.py
git commit -m "feat: store uploads under per-uploader folders"
```

---

## Task 3: `list_files` groups by uploader

**Files:**
- Modify: `server.py` (`list_files`, ~lines 275-296)
- Test: `test_server.py` (`ListingTests`, ~lines 97-135)

- [ ] **Step 1: Replace `ListingTests` with the per-uploader layout**

Replace the whole `ListingTests` class in `test_server.py` with:

```python
class ListingTests(ServerTestCase):
    def _write(self, uploader, name, content):
        d = os.path.join(self.dir, uploader)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w") as f:
            f.write(content)

    def test_empty_listing(self):
        status, data = self.request("GET", "/api/files")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data), [])

    def test_lists_files_with_uploader_newest_first_excluding_part(self):
        import time
        self._write("alice", "old.txt", "old")
        time.sleep(0.01)
        self._write("bob", "new.txt", "newer")
        open(os.path.join(self.dir, "bob", ".abc.part"), "w").close()
        status, data = self.request("GET", "/api/files")
        self.assertEqual(status, 200)
        entries = json.loads(data)
        self.assertEqual([e["name"] for e in entries], ["new.txt", "old.txt"])
        by_name = {e["name"]: e for e in entries}
        self.assertEqual(by_name["new.txt"]["uploader"], "bob")
        self.assertEqual(by_name["old.txt"]["uploader"], "alice")
        self.assertEqual(by_name["new.txt"]["size"], 5)

    def test_loose_root_files_grouped_as_unsorted(self):
        with open(os.path.join(self.dir, "dropped.txt"), "w") as f:
            f.write("x")
        status, data = self.request("GET", "/api/files")
        entries = json.loads(data)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["uploader"], "(unsorted)")
        self.assertEqual(entries[0]["name"], "dropped.txt")

    def test_entries_include_integer_mtime_consistent_with_order(self):
        import time
        self._write("alice", "old.txt", "old")
        time.sleep(0.01)
        self._write("alice", "new.txt", "newer")
        status, data = self.request("GET", "/api/files")
        entries = json.loads(data)
        for e in entries:
            self.assertIsInstance(e["mtime"], int)
        mtimes = [e["mtime"] for e in entries]
        self.assertEqual(mtimes, sorted(mtimes, reverse=True))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_server.ListingTests -v`
Expected: FAIL — the old `list_files` lists `self.dir` directly, so it finds no
files inside subfolders and has no `uploader` key.

- [ ] **Step 3: Rewrite `list_files`**

In `server.py`, replace the entire `list_files` function (lines 275-296) with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_server.ListingTests -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add server.py test_server.py
git commit -m "feat: group file listing by uploader folder"
```

---

## Task 4: Download via `/download/<handle>/<name>`

**Files:**
- Modify: `server.py` (`_serve_download`, ~lines 332-355)
- Test: `test_server.py` (`DownloadTests`, ~lines 212-239)

- [ ] **Step 1: Add a failing test for the two-segment route**

Add this test to `DownloadTests` (the existing root-file tests must still pass —
they exercise the one-segment fallback):

```python
    def test_downloads_from_uploader_folder(self):
        payload = os.urandom(server.CHUNK + 5)
        d = os.path.join(self.dir, "alice")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "report.zip"), "wb") as f:
            f.write(payload)
        status, data = self.request("GET", "/download/alice/report.zip")
        self.assertEqual(status, 200)
        self.assertEqual(data, payload)

    def test_missing_file_in_folder_404(self):
        os.makedirs(os.path.join(self.dir, "alice"), exist_ok=True)
        status, _ = self.request("GET", "/download/alice/nope.zip")
        self.assertEqual(status, 404)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_server.DownloadTests -v`
Expected: FAIL on `test_downloads_from_uploader_folder` — the old handler
sanitizes `alice/report.zip` to the basename `report.zip` and looks in the root,
returning 404.

- [ ] **Step 3: Update `_serve_download`**

In `server.py`, replace `_serve_download` (lines 332-355) with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_server.DownloadTests -v`
Expected: PASS (6 tests — 4 existing + 2 new)

- [ ] **Step 5: Commit**

```bash
git add server.py test_server.py
git commit -m "feat: serve downloads from per-uploader folders"
```

---

## Task 5: Group the file list by uploader in the page

**Files:**
- Modify: `server.py` (the `PAGE` HTML/JS string, ~lines 13-219)
- Test: `test_server.py` (`PageTests`, ~lines 197-209)

- [ ] **Step 1: Strengthen `PageTests`**

Replace the assertions in `PageTests.test_root_serves_html` so they also confirm
the page knows about uploaders and the unsorted group:

```python
    def test_root_serves_html(self):
        c = self.conn()
        c.request("GET", "/")
        r = c.getresponse()
        ctype = r.getheader("Content-Type")
        body = r.read().decode("utf-8")
        c.close()
        self.assertEqual(r.status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn("/upload", body)
        self.assertIn("/api/files", body)
        # The page groups by uploader and links unsorted files without a handle.
        self.assertIn("uploader", body)
        self.assertIn("(unsorted)", body)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_server.PageTests -v`
Expected: FAIL — the current page never references `uploader` or `(unsorted)`.

- [ ] **Step 3: Update the page markup and JS**

In `server.py`, make the following edits inside the `PAGE` string.

(a) Change the list container from a `<ul>` to a `<div>` (line 45):

```html
<div id="list"></div>
```

(b) Add a heading style. Replace the `ul { ... }` rule (line 24) with:

```css
  ul { list-style: none; padding: 0; margin: 0 0 1rem; }
  h3.uploader { margin: 1rem 0 .3rem; font-size: 1rem; color: #333; border-bottom: 2px solid #ddd; padding-bottom: .2rem; }
```

(c) Replace `dlUrl` (lines 66-70) with a handle-aware version:

```javascript
function dlUrl(uploader, name) {
  // location.origin -> the public tunnel URL through Cloudflare, 127.0.0.1
  // locally, so the copied link is always correct for the viewer. Unsorted
  // (root) files have no handle segment.
  const base = location.origin + '/download/';
  return uploader === '(unsorted)'
    ? base + encodeURIComponent(name)
    : base + encodeURIComponent(uploader) + '/' + encodeURIComponent(name);
}
```

(d) Replace `copyButton` (lines 91-98) so it carries the uploader:

```javascript
function copyButton(uploader, name, cls) {
  const btn = document.createElement('button');
  btn.type = 'button';
  if (cls) btn.className = cls;
  btn.textContent = 'Copy';
  btn.addEventListener('click', () => copy(dlUrl(uploader, name), btn));
  return btn;
}
```

(e) Replace `refresh` (lines 100-122) so it groups by uploader:

```javascript
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
```

(f) Replace `addResult` (lines 124-144) so the success link uses the uploader
returned by the upload:

```javascript
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
```

(g) In `uploadOne` (lines 163-168), capture the uploader from the response:

```javascript
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
```

(h) In `uploadAll` (line 189), pass the uploader through to `addResult`:

```javascript
      addResult(r.name, r.uploader, r.ok, r.detail);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_server.PageTests -v`
Expected: PASS

- [ ] **Step 5: Manually verify the page end to end**

Run: `UPLOAD_DIR=/tmp/uptest PORT=8000 python3 server.py`
Then in another shell, simulate two uploaders and check grouping:

```bash
curl -s -X PUT -H "Cf-Access-Authenticated-User-Email: alice@acme.com" --data-binary "hi from alice" "http://127.0.0.1:8000/upload?name=a.txt"
curl -s -X PUT -H "Cf-Access-Authenticated-User-Email: bob@acme.com" --data-binary "hi from bob" "http://127.0.0.1:8000/upload?name=b.txt"
curl -s http://127.0.0.1:8000/api/files
```

Expected: JSON shows two entries with `"uploader":"bob"` and `"uploader":"alice"`,
newest first. Open `http://127.0.0.1:8000` in a browser: two sections (`bob`,
`alice`), each with its file and a working download link. Then `Ctrl-C` the server
and `rm -rf /tmp/uptest`.

- [ ] **Step 6: Commit**

```bash
git add server.py test_server.py
git commit -m "feat: group the file list by uploader in the page"
```

---

## Task 6: Update the docs

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md` (Architecture section: endpoints table and design points)

- [ ] **Step 1: Run the full suite as a baseline**

Run: `python3 -m unittest test_server -v`
Expected: PASS (all tests green before touching docs).

- [ ] **Step 2: Update `CLAUDE.md`**

In the endpoints table, replace the `GET /api/files`, `GET /download/<name>`, and
`PUT /upload` rows with:

```markdown
| `GET /api/files`          | JSON `[{uploader, name, size, mtime}]`, newest-mtime-first, excluding `.part`; one entry per file across all uploader folders. |
| `GET /download/<handle>/<name>` | Streams the file in 64 KB chunks with `Content-Disposition: attachment`. One-segment `/download/<name>` serves loose root files. |
| `PUT /upload?name=<name>` | Streams the raw body into `UPLOAD_DIR/<handle>/`, where `<handle>` comes from the `Cf-Access-Authenticated-User-Email` header. |
```

Add a bullet under "Non-obvious design points":

```markdown
- **Per-uploader folders.** Each upload is filed under `UPLOAD_DIR/<handle>/`, where
  `<handle>` is the sanitized local part of the `Cf-Access-Authenticated-User-Email`
  header injected by the Cloudflare tunnel (`unknown` when absent, e.g. local dev).
  This attributes files by *location*, so manual deletion never leaves stale
  metadata. The header is trusted because the server binds `127.0.0.1` only — nothing
  reaches it without passing Cloudflare Access. `.part` temps and collision
  auto-renaming are per-folder. Files dropped directly in `UPLOAD_DIR` list under
  `(unsorted)`.
```

- [ ] **Step 3: Update `README.md`**

Find the section describing the file list / download behavior and add a short note
(adapt wording to the existing README voice):

```markdown
### Who uploaded what

Each file is stored under a folder named for the uploader (the part before `@` in
the email Cloudflare Access provides), and the file list groups files by uploader.
Because attribution is the folder a file lives in, deleting files by hand never
leaves stale records behind. Files placed directly in the upload directory by hand
appear under **(unsorted)**.
```

- [ ] **Step 4: Verify docs reference reality**

Run: `python3 -m unittest test_server -v`
Expected: PASS (docs-only change; the suite must still be green).

Manually confirm: the endpoint paths and JSON shape quoted in `CLAUDE.md` match
`server.py` (`/download/<handle>/<name>`, `{uploader, name, size, mtime}`).

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: document per-uploader attribution"
```

---

## Self-Review Notes

- **Spec coverage:** identity/handle (Task 1), `unknown` fallback (Task 1), per-folder
  storage + per-folder collision + `.part` placement (Task 2), grouped `/api/files`
  shape + `(unsorted)` (Task 3), two-segment download + root fallback + traversal
  rejection — covered by the unchanged `test_rejects_traversal` plus new tests
  (Task 4), grouped UI (Task 5), deployment note carried into docs (Task 6).
- **Deployment/permissions:** no code change needed — `ReadWritePaths=` already
  covers the whole `UPLOAD_DIR` subtree and the `2775` setgid bit propagates group +
  setgid to the folders the service creates. Nothing to do in `uploader.service` or
  `setup.sh`; this is documented in Task 6.
- **Type/name consistency:** `handle_from_email`, `list_files` entry keys
  (`uploader`/`name`/`size`/`mtime`), the `(unsorted)` sentinel (Python `UNSORTED`
  constant value `"(unsorted)"` matched by the JS literal `'(unsorted)'`), and the
  PUT response shape `{name, uploader}` are used identically across server and tests.
```
