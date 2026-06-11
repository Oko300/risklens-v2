"""
auth/middleware.py — RiskLens v2
==================================
Raw ASGI auth middleware with Context Protocol JWT verification.

ContextAuthASGI
---------------
  GET /mcp  — returns 200 SSE keepalive so Context Refresh Skills passes.
              Context checks for a live SSE channel before tools/list discovery.
              FastMCP requires a session for GET so it returns 400;
              our keepalive satisfies the check — actual tool calls use POST.
  POST /mcp — reads auth header from ASGI scope (populated before body parsing),
              buffers body to inspect MCP method, verifies JWT for protected
              methods via ctxprotocol.verify_context_request.
  Others    — passed through untouched.

LifespanBridge
--------------
  Routes lifespan events directly to mcp_app so FastMCP's
  StreamableHTTPSessionManager task group initialises correctly.
  Routes all HTTP to auth_app (ContextAuthASGI wrapping mcp_app).
  No Starlette routing — full path is preserved, no leading-slash stripping.

reconstructed_receive
---------------------
  After buffering the POST body for JWT inspection, we must serve the same
  bytes to FastMCP. The reconstructed receive callable serves the buffered
  body on the first call then delegates to the original receive() for real
  disconnect detection, preventing "ASGI callable returned without completing
  response" errors on long-running streaming tool responses.
"""

import json
from ctxprotocol import verify_context_request, is_protected_mcp_method, ContextError


class ContextAuthASGI:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Pass through non-HTTP scopes (websocket, lifespan, etc.)
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        http_method = scope.get("method", b"")
        if isinstance(http_method, bytes):
            http_method = http_method.decode()
        http_method = http_method.upper()

        # ── GET /mcp ─────────────────────────────────────────────────────────
        # Return a 200 SSE keepalive so Context Refresh Skills discovery passes.
        if http_method == "GET" and scope.get("path", "") == "/mcp":
            await send({
                "type":    "http.response.start",
                "status":  200,
                "headers": [
                    (b"content-type", b"text/event-stream"),
                    (b"cache-control", b"no-cache"),
                    (b"connection",    b"keep-alive"),
                ],
            })
            await send({
                "type":      "http.response.body",
                "body":      b": keepalive\n\n",
                "more_body": True,
            })
            # Hold open until client disconnects
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    break
            await send({
                "type": "http.response.body", "body": b"", "more_body": False,
            })
            return

        # ── Non-POST: pass through ────────────────────────────────────────────
        if http_method != "POST":
            await self.app(scope, receive, send)
            return

        # ── POST: extract auth header from ASGI scope ─────────────────────────
        auth_header = ""
        for name, value in scope.get("headers", []):
            if name.lower() == b"authorization":
                auth_header = value.decode("utf-8", errors="replace")
                break

        # Buffer request body to inspect MCP method before forwarding
        body_parts: list[bytes] = []
        more_body = True
        while more_body:
            message = await receive()
            body_parts.append(message.get("body", b""))
            more_body = message.get("more_body", False)
        body = b"".join(body_parts)

        # JWT verification for protected MCP methods
        request_id = None
        try:
            body_json  = json.loads(body)
            mcp_method = body_json.get("method", "")
            request_id = body_json.get("id")

            if is_protected_mcp_method(mcp_method):
                try:
                    await verify_context_request(authorization_header=auth_header)
                except ContextError as e:
                    error_body = json.dumps({
                        "jsonrpc": "2.0",
                        "error":   {"code": -32001, "message": f"Unauthorized: {e.message}"},
                        "id":      request_id,
                    }).encode()
                    await send({
                        "type":    "http.response.start",
                        "status":  401,
                        "headers": [
                            (b"content-type",   b"application/json"),
                            (b"content-length", str(len(error_body)).encode()),
                        ],
                    })
                    await send({
                        "type": "http.response.body",
                        "body": error_body, "more_body": False,
                    })
                    return
        except (json.JSONDecodeError, Exception):
            pass   # Malformed body — let FastMCP handle it

        # Reconstruct receive: serve buffered body first, then delegate to
        # original receive() for real disconnect detection.
        body_served = False

        async def reconstructed_receive():
            nonlocal body_served
            if not body_served:
                body_served = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, reconstructed_receive, send)


class LifespanBridge:
    """
    Routes lifespan events to mcp_app and HTTP events to auth_app.

    This ensures FastMCP's StreamableHTTPSessionManager task group
    initialises correctly without path stripping side effects.
    """
    def __init__(self, mcp_app, auth_app):
        self.mcp_app  = mcp_app
        self.auth_app = auth_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.mcp_app(scope, receive, send)
        else:
            await self.auth_app(scope, receive, send)