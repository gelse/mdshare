"""mdshare — combined ASGI application (Flask + MCP)."""

from asgiref.wsgi import WsgiToAsgi
from backend.app import app as flask_app
from backend.mcp_server import mcp_app

flask_asgi = WsgiToAsgi(flask_app)


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        # Forward lifespan events to the MCP app so its session manager
        # can initialize the task group (required for Streamable HTTP).
        await mcp_app(scope, receive, send)
    elif scope["type"] == "http" and scope["path"].startswith("/api/mcp"):
        await mcp_app(scope, receive, send)
    else:
        await flask_asgi(scope, receive, send)
