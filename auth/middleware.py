"""
auth/middleware.py — RiskLens v2
==================================
Authentication middleware — JWT auth DISABLED.

RiskLens v2 is deployed on MCPize which handles authentication
at the platform level. No per-request JWT verification is needed.

The middleware is kept as a pass-through so the server structure
remains clean and auth can be re-enabled per deployment if needed.
"""


class ContextAuthASGI:
    """Pass-through middleware — no auth enforced."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # All requests pass through directly — no JWT check
        await self.app(scope, receive, send)


class LifespanBridge:
    """
    Routes lifespan events to mcp_app and HTTP to auth_app.
    Ensures FastMCP task group initialises correctly.
    """
    def __init__(self, mcp_app, auth_app):
        self.mcp_app  = mcp_app
        self.auth_app = auth_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.mcp_app(scope, receive, send)
        else:
            await self.auth_app(scope, receive, send)
