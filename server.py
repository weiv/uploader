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
