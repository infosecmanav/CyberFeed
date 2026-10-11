import os, threading
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from .engine import build, ROOT
app = FastAPI()
lock = threading.Lock()
class BuildRequest(BaseModel):
    period: str = "daily"
    api_key: str = Field(default="", max_length=300)
    model: str = Field(default="gpt-4.1-mini", max_length=100)
    demo: bool = True
@app.get("/")
def index(): return FileResponse(ROOT / "web/index.html")
@app.post("/api/briefing")
def briefing(body: BuildRequest, request: Request):
    if request.headers.get("sec-fetch-site") == "cross-site": raise HTTPException(403, "Cross-site request blocked")
    if body.period not in ("daily", "weekly"): raise HTTPException(400, "Unknown period")
    if not lock.acquire(blocking=False): raise HTTPException(429, "A briefing is already building")
    try: return build(body.period, body.demo, body.api_key or os.getenv("OPENAI_API_KEY"), body.model)
    finally: lock.release()
