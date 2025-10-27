# app.py
import os
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional
import httpx
from datetime import datetime

API_KEY = os.getenv("API_KEY", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "5"))
MENTION_ROLE_ID = os.getenv("MENTION_ROLE_ID", "")  # 선택: 특정 역할 멘션 원하면 넣기 (예: 123456789012345678)

if not API_KEY:
    raise RuntimeError("API_KEY env required")
if not DISCORD_WEBHOOK_URL:
    raise RuntimeError("DISCORD_WEBHOOK_URL env required")

app = FastAPI(title="MSG Alert Bot", version="1.0.0")

class BanPayload(BaseModel):
    ipAddress: str
    reason: str
    banType: str = Field(..., description="TEMPORARY | PERMANENT")
    bannedAt: str  # ISO8601
    expiresAt: Optional[str] = None
    bannedByAdminLoginId: Optional[str] = None
    durationMinutes: Optional[int] = None

def iso(v: Optional[str]) -> str:
    if not v:
        return "-"
    try:
        # normalize
        return datetime.fromisoformat(v.replace("Z","+00:00")).isoformat()
    except Exception:
        return v

@app.get("/healthz")
async def health():
    return {"ok": True}

@app.post("/alert/ban")
async def alert_ban(payload: BanPayload, x_api_key: str = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid api key")

    # 디스코드 임베드 구성
    color_red = 0xE11D48  # rose-600 느낌
    fields = [
        {"name": "IP", "value": f"`{payload.ipAddress}`", "inline": True},
        {"name": "타입", "value": payload.banType, "inline": True},
        {"name": "사유", "value": payload.reason or "-", "inline": False},
        {"name": "차단시각", "value": iso(payload.bannedAt), "inline": True},
        {"name": "만료시각", "value": iso(payload.expiresAt), "inline": True},
        {"name": "관리자", "value": payload.bannedByAdminLoginId or "AUTO_BAN_SYSTEM", "inline": True},
        {"name": "기간(분)", "value": str(payload.durationMinutes) if payload.durationMinutes else "-", "inline": True},
    ]

    content = ""
    if MENTION_ROLE_ID:
        content = f"<@&{MENTION_ROLE_ID}>"

    data = {
        "content": content,
        "embeds": [{
            "title": "🚫 IP Banned",
            "description": "자동/수동 차단 이벤트",
            "color": color_red,
            "fields": fields,
            "footer": {"text": "MSG CTF • IPBan"},
        }]
    }

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(DISCORD_WEBHOOK_URL, json=data)
        if r.status_code >= 300:
            raise HTTPException(status_code=502, detail=f"discord webhook failed: {r.status_code} {r.text}")

    return {"ok": True}
    
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT","8088")))
