from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os

from api.routers import auth, conversations, messages, usage

app = FastAPI(
    title="RiskLens API",
    description="API for RiskLens SaaS application",
    version="2.0.0",
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for now
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(auth.router, prefix="/api/auth")
app.include_router(conversations.router, prefix="/api/conversations")
app.include_router(messages.router, prefix="/api/messages")
app.include_router(usage.router, prefix="/api/usage")

# Health check
@app.get("/")
async def root():
    frontend_path = os.path.join(
        os.path.dirname(__file__), "../frontend/index.html"
    )
    if os.path.exists(frontend_path):
        return FileResponse(frontend_path)
    return {"status": "ok", "app": "RiskLens"}

@app.get("/health")
async def health():
    return {"status": "ok"}
