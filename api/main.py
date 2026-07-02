from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

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
@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("frontend/index.html", "r") as f:
        return f.read()

@app.get("/health")
async def health_check():
    return {"status": "ok", "app": "RiskLens"}

# Serve static files (e.g., frontend assets)
app.mount("/static", StaticFiles(directory="frontend"), name="static")