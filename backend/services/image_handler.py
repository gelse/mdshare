"""Image handling — URL rewriting, file persistence for uploaded / base64-encoded images."""

import base64
import os
import re
from typing import Any

from backend.config import config


def _images_dir(doc_id: str) -> str:
    """Return the filesystem path where images for *doc_id* are stored."""
    return os.path.join(config.data_dir, "images", doc_id)


def rewrite_image_urls(content: str, doc_id: str, filenames: set[str]) -> str:
    """Rewrite Markdown image references to point at the serving endpoint.

    Each filename in *filenames* is escaped via ``re.escape``, and filenames
    are sorted by length (longest first) to avoid partial-name collisions.
    """
    # Sort longest first so 'image_long.png' is matched before 'image.png'.
    if not filenames:
        return content
    sorted_names = sorted(filenames, key=len, reverse=True)
    for fname in sorted_names:
        content = re.sub(
            re.escape(fname),
            f"/v/{doc_id}/img/{fname}",
            content,
        )
    return content


def save_uploaded_image(
    image_storage: Any,
    doc_id: str,
    filename: str,
) -> str:
    """Persist an uploaded image (``werkzeug.datastructures.FileStorage``) to disk.

    Returns the target filesystem path.
    """
    dest_dir = _images_dir(doc_id)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, filename)
    image_storage.save(dest)
    return dest


def save_base64_image(
    base64_data: str,
    doc_id: str,
    image_name: str,
) -> str:
    """Decode a base64-encoded image and write it to disk.

    Supports both ``data:image/...;base64,...`` prefixed strings and
    raw base64 payloads.
    """
    if "," in base64_data:
        base64_data = base64_data.split(",", 1)[1]

    dest_dir = _images_dir(doc_id)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, image_name)

    decoded = base64.b64decode(base64_data)
    with open(dest, "wb") as f:
        f.write(decoded)
    return dest
