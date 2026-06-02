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
