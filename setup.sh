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
