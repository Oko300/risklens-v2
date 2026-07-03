from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os

app = FastAPI(title="RiskLens")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from api.routers import auth, conversations, messages, usage

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(conversations.router, prefix="/api", tags=["conversations"])
app.include_router(messages.router, prefix="/api", tags=["messages"])
app.include_router(usage.router, prefix="/api", tags=["usage"])

@app.get("/")
async def root():
    frontend_path = os.path.join(
        os.path.dirname(__file__), "../frontend/index.html"
    )
    if os.path.exists(frontend_path):
        return FileResponse(frontend_path)
    return {"status": "ok"}

@app.get("/health")
async def health():
    return {"status": "ok"}