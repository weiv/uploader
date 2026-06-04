# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Pi Uploader** — a minimal personal "dropbox" for a Raspberry Pi: a single web page to
upload files to, and download files from, one fixed directory. Fronted by a Cloudflare
tunnel that handles authentication, so the app itself has **no login/session code**.

Hard constraints that shape every decision:
- **Stdlib-only Python 3**, zero runtime dependencies. The entire app is one `server.py`.
- **Streams all transfers** — the Pi must never buffer a whole (hundreds-of-MB) file in RAM.
- Runs as an unprivileged, no-login system user; administered by `weiv`.

## Current state

Implementation has **not started**. The repo currently contains only the design spec and
the task-by-task implementation plan:

- `docs/superpowers/specs/2026-06-02-pi-uploader-design.md` — the design (source of truth).
- `docs/superpowers/plans/2026-06-02-pi-uploader.md` — TDD, task-by-task build plan with
  full code for each file.

**Read both before writing code.** The plan is meant to be executed with
`superpowers:subagent-driven-development` or `superpowers:executing-plans`, one task at a
time (each task: write failing test → verify it fails → implement → verify it passes →
commit). The files it produces: `server.py`, `test_server.py`, `uploader.service`,
`setup.sh`, `README.md`.

## Commands

```bash
# Run the full test suite (stdlib unittest, no deps)
python3 -m unittest test_server -v

# Run one test class / one test
python3 -m unittest test_server.UploadTests -v
python3 -m unittest test_server.UploadTests.test_round_trips_bytes -v

# Run the server locally
UPLOAD_DIR=/tmp/uptest PORT=8000 python3 server.py    # then open http://127.0.0.1:8000

# Provision on the Pi (idempotent, safe to re-run)
sudo ./setup.sh

# Validate the systemd unit / lint the setup script (no-ops gracefully off-Pi)
systemd-analyze verify ./uploader.service
bash -n setup.sh && shellcheck setup.sh
```

There is no build step, linter config, or package manager — it's a single stdlib script.

## Architecture

One `server.py` on `http.server.ThreadingHTTPServer` + a custom `BaseHTTPRequestHandler`,
bound to `127.0.0.1` (the tunnel connects locally). Four endpoints:

| Method & path             | Purpose |
|---------------------------|---------|
| `GET /`                   | One inline HTML page (no external assets): drag-drop upload + file list. |
| `GET /api/files`          | JSON `[{uploader, name, size, mtime}]`, newest-mtime-first, excluding `.part`; one entry per file across all uploader folders. |
| `GET /download/<handle>/<name>` | Streams the file in 64 KB chunks with `Content-Disposition: attachment`. One-segment `/download/<name>` serves loose root files. |
| `PUT /upload?name=<name>` | Streams the raw body into `UPLOAD_DIR/<handle>/`, where `<handle>` comes from the `Cf-Access-Authenticated-User-Email` header. |

The filename/config helpers (`sanitize_name`, `unique_path`, `stream_body`) are
**module-level functions** so tests exercise them directly without HTTP.

### Non-obvious design points (get these right)

- **Raw-body upload, not multipart.** The browser sends the file as the raw PUT body
  (`fetch('/upload?name=...', {method:'PUT', body:file})`). This deliberately avoids stdlib
  multipart parsing — `cgi` is deprecated and **removed in Python 3.13**.
- **The read loop is exact.** `Content-Length` is **required** (absent or non-integer → 400).
  Reading past `Content-Length` on `http.server`'s `rfile` blocks forever, so `stream_body`
  reads *exactly* that many bytes, tracking remaining. A short read (client dropped) raises
  `IncompleteUpload` → clean up the `.part` file → 400. Never write a truncated file.
- **Atomic, collision-free writes.** Stream to `.<uuid>.part`, then `os.rename` to the final
  sanitized name. The uuid prevents temp-file collisions between concurrent/retried uploads.
  On an existing name, `unique_path` auto-renames `report.zip` → `report(1).zip` …, bounded
  1..999 (exhausted → 500). **Nothing is ever overwritten**; `.part` files are excluded from
  listings and cleaned up on error.
- **Filename safety.** `sanitize_name` = `os.path.basename` of the stripped input, rejecting
  empty / `.` / `..` / anything still containing a separator → keeps all transfers inside
  `UPLOAD_DIR`. Applied to **both** upload and download.
- **Per-uploader folders.** Each upload is filed under `UPLOAD_DIR/<handle>/`, where
  `<handle>` is the sanitized local part of the `Cf-Access-Authenticated-User-Email`
  header injected by the Cloudflare tunnel (`unknown` when absent, e.g. local dev).
  This attributes files by *location*, so manual deletion never leaves stale
  metadata. The header is trusted because the server binds `127.0.0.1` only — nothing
  reaches it without passing Cloudflare Access. `.part` temps and collision
  auto-renaming are per-folder. Files dropped directly in `UPLOAD_DIR` list under
  `(unsorted)`.

### Deployment coupling

The service runs under systemd hardening: `ProtectSystem=strict` makes the whole filesystem
read-only **except** paths in `ReadWritePaths=`. So `UPLOAD_DIR` and `ReadWritePaths=` in
`uploader.service` must stay in sync — if you point `UPLOAD_DIR` elsewhere, add it to
`ReadWritePaths` or writes silently fail. The dir is `2775` (setgid) owned
`uploader:uploader-admin` with the service's `UMask=002`, so human admin `weiv` (in the
`uploader-admin` group) can manage files without sudo.

## Conventions

- `CHUNK = 64 * 1024` everywhere a buffer size is needed.
- Config read **once at import**: `UPLOAD_DIR` (env, default `/srv/uploader/files`),
  `PORT` (env, default `8000`), `HOST = "127.0.0.1"` (always — never bind publicly).
- Conventional Commit prefixes: `feat:`, `test:`, `chore:`, `docs:`.
- Tests start the real server against a `tempfile.mkdtemp()` dir on an **ephemeral port**
  (`("127.0.0.1", 0)`) and swap `server.UPLOAD_DIR` in `setUp`/restore in `tearDown`.
- **This repo lives on an iCloud-synced path.** Don't have tests create hundreds/thousands
  of files (e.g. to exercise the collision bound) — mock `os.path.exists` instead.
