# Pi Uploader — Design

A minimal personal "dropbox" for a Raspberry Pi: a single web page to upload files
to, and download files from, one fixed directory. Exposed via a Cloudflare tunnel,
which also handles authentication — so the app itself contains no login/session code.

## Goals

- Upload files (hundreds of MB+) and download them from one fixed directory.
- Zero runtime dependencies: stdlib-only Python 3, a single `server.py`.
- Run as an unprivileged, no-login system user (`uploader`); administered by `weiv`.
- Stream all transfers so the Pi never buffers a whole large file in RAM.

## Non-Goals (YAGNI)

- No authentication / sessions (Cloudflare handles it).
- No delete, rename, multi-file, or overwrite from the UI — upload + download only.
- No subdirectories, no resumable/chunked uploads.

## Architecture

One file, `server.py`, using `http.server` + `socketserver` from the stdlib. It serves
on a configurable port, bound to `127.0.0.1` (the Cloudflare tunnel connects locally).
A module-level `UPLOAD_DIR` constant, overridable by the `UPLOAD_DIR` env var, names the
fixed directory (default `/srv/uploader/files`). Port overridable by `PORT` env var
(default `8000`).

### Endpoints

| Method & path            | Purpose |
|--------------------------|---------|
| `GET /`                  | Single inline HTML page: file picker / drag-and-drop, upload progress bar, and a list of files (name, size, download link). No external assets. |
| `GET /api/files`         | JSON array of files: `[{"name": ..., "size": ...}, ...]`. |
| `GET /download/<name>`   | Streams the file in chunks with `Content-Disposition: attachment`. |
| `PUT /upload?name=<name>`| Streams the raw request body to disk in chunks. |

### Upload mechanism (raw body, not multipart)

The browser sends the file as the **raw request body**:
`fetch('/upload?name=' + encodeURIComponent(file.name), {method: 'PUT', body: file})`.
The server reads `rfile` in 64 KB chunks (using `Content-Length`) and writes to disk.
This avoids stdlib multipart parsing entirely (`cgi` is deprecated and removed in
Python 3.13) and streams cleanly regardless of file size.

## Data Flow

- **Upload:** JS picks file → `PUT /upload?name=...` raw body → server writes chunks to a
  temp file `<final>.part` in `UPLOAD_DIR` → on success, atomically `os.rename` to the
  sanitized, de-duplicated final name → returns final name as JSON → JS refreshes the list.
- **Download:** `GET /download/<name>` → sanitize name → open file → write to socket in
  64 KB chunks with correct `Content-Length` and attachment header.

## Safety

- **Filename sanitization:** take `os.path.basename` of the requested name, reject empty,
  `.`/`..`, or anything still containing a path separator → 400. Guarantees transfers stay
  inside `UPLOAD_DIR`.
- **Collision handling:** if the sanitized name exists, auto-rename `report.zip` →
  `report(1).zip`, `report(2).zip`, … Nothing is ever overwritten.
- **Atomic writes:** stream to `<name>.part`, then rename. A half-finished or aborted
  upload never appears as a real file; `.part` files are cleaned up on error and excluded
  from listings.

## Error Handling

- Missing/empty/invalid `name` on upload → `400`.
- Disk write error during upload → `500`, delete the partial `.part` file.
- Download of a missing/invalid name → `404`.
- Client shows a simple inline error message; progress bar resets.

## Users & Permissions (on the Pi)

- **System user `uploader`**, no login:
  `useradd --system --home /srv/uploader --shell /usr/sbin/nologin uploader`.
  The server process runs as this user — a compromise gets no shell and nothing outside
  its directory.
- **Shared group `uploader-admin`** containing both `uploader` and `weiv`.
- **Upload dir** `/srv/uploader/files` owned `uploader:uploader-admin`, mode **`2775`**
  (setgid → new files inherit the `uploader-admin` group).
- Server runs with **`UMask=002`** → uploaded files are `664` (group-writable). Net effect:
  **`weiv` can read, move, rename, and delete uploaded files directly**, no sudo needed.
- Code at `/srv/uploader/server.py`, owned `uploader:uploader-admin`, group-writable so
  `weiv` can edit it.

## Service Management (systemd)

`uploader.service` with:

- `User=uploader`, `Group=uploader-admin`, `UMask=002`
- `WorkingDirectory=/srv/uploader`, `ExecStart=/usr/bin/python3 /srv/uploader/server.py`
- Bound to `127.0.0.1` (in code).
- Hardening: `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ProtectHome=yes`,
  `PrivateTmp=yes`, `ReadWritePaths=/srv/uploader/files`.
- `Restart=on-failure`.

`weiv` administers via `sudo systemctl start/stop/restart/status uploader` and
`journalctl -u uploader` (weiv has sudo, the Pi default).

## Setup Delivery

Both:

- **`setup.sh`** — idempotent `sudo ./setup.sh` that creates the user, group, directory
  (correct owner/mode), copies `server.py` into place, installs `uploader.service`, and
  enables + starts it. Safe to re-run.
- **`README.md`** — explains what `setup.sh` does, the manual copy-paste equivalent of each
  step, the Cloudflare tunnel hookup (point the tunnel at `http://127.0.0.1:8000`), and how
  `weiv` administers files and the service.

## Testing

A stdlib `unittest` script (`test_server.py`) that starts the server against a temp
directory on an ephemeral port and verifies:

1. Upload round-trips bytes exactly (incl. a multi-chunk file larger than the 64 KB buffer).
2. Collision on an existing name auto-renames (`x.txt` → `x(1).txt`).
3. Path-traversal names (`../x`, `a/b`, empty, `..`) are rejected with 400.
4. Download returns exact content; missing file → 404.
5. `.part` files are not shown in `/api/files`.

## File Layout

```
server.py          # the app
test_server.py     # stdlib unittest tests
setup.sh           # idempotent provisioning script (run with sudo on the Pi)
uploader.service   # systemd unit (installed by setup.sh)
README.md          # setup, Cloudflare tunnel, and admin instructions
```
