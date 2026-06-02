# Design: file-list timestamps + copyable upload links

Date: 2026-06-02

## Problem

Two small gaps in the uploader UI:

1. The file list shows name + size but no indication of *when* a file landed.
2. After an upload there is no easy way to grab a shareable link to paste into a
   chat — you'd have to hunt for the file in the list and copy its `href` by hand.

## Goals

- Show each file's modification time in the list, in the viewer's local timezone.
- After an upload (and on every list row), offer a one-click **Copy** of the file's
  full, public download URL — ready to paste into chat.

## Non-goals (YAGNI)

- Relative "2 minutes ago" timestamps with refresh timers.
- Per-file delete/rename buttons, QR codes, short links, or previews.

## Design

### Server (`server.py`)

`list_files` already `stat`s each file and computes `_mtime` purely to sort
newest-first, then deletes it. The only server change is to **keep** it as a
public field:

```
GET /api/files → [{ "name": str, "size": int, "mtime": int }, ...]   # newest mtime first
```

`mtime` is integer epoch seconds (`int(st.st_mtime)`). The browser converts to
local time; the server stays timezone-agnostic. No new endpoint.

`PUT /upload` is unchanged: it already returns `{"name": <final saved name>}` —
the post-collision name (e.g. `a(1).txt`) — which is exactly what the link needs.

### Client (inline HTML/JS in `PAGE`)

- `fmtTime(s)` → `new Date(s * 1000).toLocaleString()`, rendered per row as a
  middle column: **name (link) · timestamp · size**.
- `dlUrl(name)` → `location.origin + '/download/' + encodeURIComponent(name)`.
  Using `location.origin` makes the copied link the **public tunnel URL**
  (`https://files.example.com/...`) when reached through Cloudflare, and
  `http://127.0.0.1:57194/...` locally — correct automatically, with no need for
  the server to know its public hostname.
- `copy(text, btn)` → `navigator.clipboard.writeText(text)` with a
  `document.execCommand('copy')` fallback for non-secure contexts; briefly flips
  the button label to "Copied".
- Each list row gains a small **Copy** button next to the size.
- `uploadOne` resolves with the parsed `{name}` (final name) instead of nothing.
  When `uploadAll` finishes, a confirmation area lists each uploaded file as
  `✓ <name>` + its full URL (selectable text) + a **Copy** button.

Because `/download/<name>` sends `Content-Disposition: attachment`, a pasted link
downloads the file on click — the desired chat-sharing behavior.

## Testing

Server-side contract is unit-tested (stdlib `unittest`, no JS harness in this
stdlib-only project):

- `/api/files` entries include an integer `mtime`.
- `mtime` is consistent with the existing newest-first ordering.
- Existing listing/upload tests stay green (they access `name`/`size` and the
  upload `name`, and don't pin the entry key set).

The clipboard/link/timestamp rendering is client-side JS and is **not** covered
by the Python suite; this is called out rather than silently skipped.

## Risks

- `navigator.clipboard` requires a secure context (HTTPS or localhost). Through
  the Cloudflare tunnel the origin is HTTPS, and local dev is `127.0.0.1`, so both
  real paths qualify; the `execCommand` fallback covers the rest.
