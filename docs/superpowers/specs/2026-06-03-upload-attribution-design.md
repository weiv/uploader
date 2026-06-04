# Upload Attribution (who uploaded what) — Design

Two people use the uploader to exchange files. They want to see **who uploaded
which file**. This design adds per-uploader attribution by storing each person's
uploads in their own subdirectory and grouping the file list by uploader in the UI.

## Goals

- Show, for every file, which user uploaded it.
- Survive manual deletion: an admin removing a file by hand must never leave stale
  or orphaned attribution metadata behind.
- Keep the stdlib-only, zero-dependency, single-`server.py` constraints intact.

## Non-Goals (YAGNI)

- No login/session code — identity still comes entirely from the Cloudflare tunnel.
- No per-user access control: every viewer sees everyone's files (it's an exchange).
- No rename/delete/move from the UI; no user-navigable folder browsing.
- No JWT verification (see "Trust model" — the localhost bind makes the plain
  header trustworthy).

## Why subdirectories, not metadata

Attribution could live in a sidecar `.meta` file or be baked into the filename. Both
were rejected:

- **Sidecar metadata** goes stale the moment an admin deletes a file by hand (the
  primary management workflow here) — orphaned `.meta` files accumulate.
- **Filename tagging** (`report [alice].zip`) works but mangles names.

Storing each uploader's files under `UPLOAD_DIR/<handle>/` makes attribution a
property of *location*: delete a file (or a whole person's folder) and its
attribution disappears with it. Nothing can go stale. This consciously revises the
original Pi Uploader design's "No subdirectories" non-goal — subdirectories are an
**internal storage mechanism for attribution**, not a user-facing folder feature.

## Identity & trust model

The uploader's identity is the HTTP request header **`Cf-Access-Authenticated-User-Email`**,
injected by Cloudflare Access on every request that reaches the origin.

The **handle** (folder name) is the **local part** of that email — the text before
`@` — passed through the existing `sanitize_name`. Examples:

| Email                  | Handle        |
|------------------------|---------------|
| `alice@acme.com`       | `alice`       |
| `v.weinstein@corp.com` | `v.weinstein` |

**Trust:** the server binds `127.0.0.1` only and the Cloudflare tunnel is the sole
path to it, so any request that arrives has already passed Access. An outside client
cannot reach the port to forge the header. This is why the plain header is trusted
and the signed `Cf-Access-Jwt-Assertion` is **not** verified (verifying a JWT in
stdlib-only Python would be disproportionate).

**Fallback (decision A):** when the header is **absent** (local dev runs, or any
non-tunnel access), the handle is **`unknown`**. Uploads still succeed; they just
land in `UPLOAD_DIR/unknown/`.

## Storage layout

```
UPLOAD_DIR/
  alice/
    report.zip
    photo.jpg
  bob/
    budget.xlsx
  unknown/          # header-less uploads (local dev, etc.)
    scratch.bin
```

- A `.<uuid>.part` temp file is written **inside the uploader's own folder**, so the
  final `os.rename` stays on one filesystem and is atomic.
- Collision auto-renaming (`a.txt` → `a(1).txt`) becomes **per-folder**. Alice's
  `report.zip` and Bob's `report.zip` coexist untouched because they live in
  different directories. Nothing is ever overwritten (unchanged invariant).
- **Loose files in the root (decision B):** files placed directly in `UPLOAD_DIR`
  (e.g. an admin drops one by hand, or anything pre-existing) are listed under a
  synthetic uploader group named **`(unsorted)`** rather than hidden. They are never
  produced by the app itself.

## Endpoint changes

| Method & path                       | Change |
|-------------------------------------|--------|
| `GET /api/files`                    | Returns a **flat** list `[{uploader, name, size, mtime}]`, newest-mtime-first. The page groups by `uploader` client-side. Still excludes `.part`. |
| `GET /download/<handle>/<name>`     | Two path segments. **Both** are `sanitize_name`-d; the file is `UPLOAD_DIR/<handle>/<name>`. |
| `PUT /upload?name=<name>`           | Unchanged query contract. Server derives the handle from the header, `os.makedirs(UPLOAD_DIR/<handle>)`, streams `.part` there, renames via `unique_path(handle_dir, name)`. Response is `{"name": ..., "uploader": ...}` so the UI can build the correct download link. |
| `GET /`                             | HTML unchanged in contract; `refresh()` now groups the list by uploader and builds `/download/<handle>/<name>` links. |

### `list_files` behavior

Walk each immediate subdirectory of `UPLOAD_DIR`; for each, list its files (skipping
`.part`), tagging every entry with `uploader = <subdir name>`. Also scan `UPLOAD_DIR`
itself for loose regular files, tagging them `uploader = "(unsorted)"`. Sort all
entries newest-mtime-first across the whole set (one global ordering; the client
re-groups but preserves order within each group). `mtime` reported as integer epoch
seconds, as today.

### UI grouping

`refresh()` consumes the flat list, partitions by `uploader` (preserving the
newest-first order within each group), and renders one section per uploader — a small
heading with the handle, followed by that person's files (name, local-time mtime,
size, copy button, download link). Empty state unchanged.

## Edge cases

- **Handle sanitization:** the local part is run through `sanitize_name`; a value that
  reduces to empty/`.`/`..` (pathological email) falls back to `unknown`.
- **Download with unknown handle or file:** missing folder or missing file → `404`;
  un-sanitizable segment → `400` (mirrors current behavior).
- **Concurrent uploads** from the same user: distinct `.<uuid>.part` names within the
  same folder prevent temp collisions; `unique_path` resolves final-name collisions.
- **`.part` files** are excluded from listings at every level.

## Deployment / permissions

`UPLOAD_DIR` is `2775` (setgid) owned `uploader:uploader-admin` with the service's
`UMask=002`. On Linux the **setgid bit and group are inherited** by subdirectories the
service creates, so each `<handle>/` folder is group-owned `uploader-admin` and
group-writable — admin `weiv` can manage files in any uploader's folder without sudo.
`ReadWritePaths=` already grants the whole `UPLOAD_DIR` subtree, so `uploader.service`
needs **no change**. (To be re-confirmed against `uploader.service`/`setup.sh` during
implementation.)

## Testing (TDD)

Stdlib `unittest`, real server on an ephemeral port against a `tempfile.mkdtemp()`
dir, per the repo's existing pattern. New and updated coverage:

- **Handle derivation:** local part extracted from `Cf-Access-Authenticated-User-Email`;
  `unknown` fallback when the header is absent; pathological email → `unknown`.
- **Upload placement:** `PUT` with the header lands the file in `UPLOAD_DIR/<handle>/`;
  response includes `uploader`.
- **Per-folder collision:** same name from two handles coexists; same name twice from
  one handle yields `a(1).txt` in that folder.
- **Listing:** grouped/flat JSON shape includes `uploader`; newest-first ordering holds
  across folders; `(unsorted)` group for loose root files; `.part` excluded.
- **Download:** `/download/<handle>/<name>` round-trips bytes; traversal in either
  segment rejected; missing file/folder → `404`.
- **Updated existing tests:** `ListingTests`, `UploadTests`, `DownloadTests` adjusted
  from the flat layout to the per-handle layout.
- Collision-bound test continues to mock `os.path.exists` (no mass file creation on the
  iCloud-synced path).
