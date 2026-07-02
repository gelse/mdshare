"""MCP Streamable HTTP server for mdshare.

Thin MCP tools that delegate all business logic to the service layer.
Accessible via the ``/api/mcp`` endpoint.
"""

from __future__ import annotations

import base64
import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from backend.config import config
from backend.services.auth import extract_bearer_token, verify_master_password
from backend.services.image_handler import save_base64_image, rewrite_image_urls
from backend.services.share_service import share_service as service

# ---------------------------------------------------------------------------
# FastMCP application
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "mdshare",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
    streamable_http_path="/api/mcp",
    stateless_http=True,
    json_response=True,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _share_url(share_id: str) -> str:
    """Build absolute share URL from base URL (configurable or fallback)."""
    base = config.base_url or "http://localhost:5000"
    return f"{base}/v/{share_id}"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def create_share(
    content: str,
    protected: bool = False,
    images: list[str] | None = None,
    ttl_hours: int | None = None,
) -> dict[str, Any]:
    """Create new share from tool arguments. Auth via Bearer token."""

    doc_id = service.generate_id()
    filenames: set[str] = set()

    decoded: list[tuple[str, str, bytes]] = []

    if images:
        for i, img_data in enumerate(images):
            if img_data.startswith("data:"):
                header, _, b64_data = img_data[5:].partition(",")
                params = header.split(";")
                safe_name: str | None = None
                for param in params:
                    if param.startswith("filename="):
                        safe_name = os.path.basename(param[len("filename=") :])
                        break
                if not safe_name:
                    safe_name = f"image_{i}.png"
            else:
                safe_name = f"image_{i}.png"
                b64_data = img_data

            try:
                raw = base64.b64decode(b64_data)
            except Exception:
                return {"error": f"invalid base64 data for image '{safe_name}'"}
            decoded.append((safe_name, img_data, raw))

        # Validate total image data size before saving
        total_img_size = sum(len(raw) for _, _, raw in decoded)
        if total_img_size > config.max_size:
            return {
                "error": f"total image data exceeds max size of {config.max_size} bytes"
            }

        # Collect filenames for URL rewriting (no saving yet)
        for safe_name, _, _ in decoded:
            filenames.add(safe_name)

        content = rewrite_image_urls(content, doc_id, filenames)

    try:
        result = service.create_share(
            content=content,
            protected=protected,
            ttl_hours=ttl_hours,
            filenames=filenames,
            doc_id=doc_id,
        )
    except ValueError as e:
        return {"error": str(e)}

    # Save images AFTER creating the DB record (matches Flask pattern)
    if decoded:
        for safe_name, img_data, _ in decoded:
            save_base64_image(img_data, doc_id, safe_name)

    # Build MCP response — MUST omit password key for public shares (test line 60)
    response: dict[str, Any] = {
        "id": result["id"],
        "url": _share_url(result["id"]),
        "valid_until": result["valid_until"],
    }
    if protected and result.get("password"):
        response["password"] = result["password"]

    return response


@mcp.tool()
async def get_share(
    share_id: str,
    password: str = "",
) -> dict[str, Any]:
    """Get share content by ID."""
    if not share_id or not share_id.strip():
        return {"error": "Share ID is required"}

    result = service.get_share(share_id, view_password=password or None)

    if result is None:
        return {"error": "Share not found"}

    if "error" in result:
        # Translate lowercase service errors to MCP format (capitalised)
        error_map = {
            "password_required": "Password required",
            "incorrect_password": "Incorrect password",
        }
        return {"error": error_map.get(result["error"], result["error"])}

    return {
        "content": result["content"],
        "protected": result.get("password") is not None,
        "created_at": result["created_at"],
    }


@mcp.tool()
async def get_share_info(share_id: str) -> dict[str, Any]:
    """Get metadata about a share without returning content."""
    if not share_id or not share_id.strip():
        return {"error": "Share ID is required"}

    result = service.get_share_info(share_id)
    result["url"] = _share_url(share_id)
    return result


@mcp.tool()
async def health_check() -> dict[str, Any]:
    """Verify that service is operational."""
    result = service.health_check()

    storage_map = {
        "ok": "sqlite",
        "degraded": "unreachable",
    }
    storage_status = storage_map.get(result["storage"], "unreachable")

    return {
        "status": "ok" if result["storage"] == "ok" else "degraded",
        "storage": storage_status,
        "data_dir": result["data_dir"],
    }


    @mcp.tool()
    async def get_version() -> dict[str, Any]:
        """Return deployed mdshare version (git hash or release tag)."""
        return {"version": config.version}


    @mcp.tool()
    async def list_shares() -> dict[str, Any]:
        """List all active shares. Auth via Bearer token."""

        shares = service.list_shares()

        share_list: list[dict[str, Any]] = []
        for share in shares:
            share_list.append(
                {
                    "id": share["id"],
                    "url": _share_url(share["id"]),
                    "created_at": share["created_at"],
                    "valid_until": share["valid_until"],
                    "protected": share.get("protected", False),
                }
            )

        return {"shares": share_list, "count": len(share_list)}


# ---------------------------------------------------------------------------
# ASGI Bearer-auth middleware
# ---------------------------------------------------------------------------


async def _send_401(send: Any) -> None:
    """Send a 401 Unauthorized JSON response."""
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [(b"content-type", b"application/json")],
    })
    await send({
        "type": "http.response.body",
        "body": b'{"error":"unauthorized"}',
    })


def _bearer_auth_middleware(inner_app: Any) -> Any:
    """Wrap an ASGI app with Bearer token authentication.

    Uses the shared ``extract_bearer_token`` + ``verify_master_password``
    from ``backend.services.auth`` — the same logic used by the Flask API
    routes.
    """
    async def middleware(scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await inner_app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode(errors="replace")
        token = extract_bearer_token(auth_header)
        if not verify_master_password(token):
            await _send_401(send)
            return
        await inner_app(scope, receive, send)

    return middleware


# ---------------------------------------------------------------------------
# Export ASGI app
# ---------------------------------------------------------------------------

mcp_app = _bearer_auth_middleware(mcp.streamable_http_app())
