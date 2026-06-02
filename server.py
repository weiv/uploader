import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

CHUNK = 64 * 1024

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/srv/uploader/files")


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
