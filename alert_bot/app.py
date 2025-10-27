# app.py
import os
import json
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from typing import Optional, Any, Dict
import httpx
from datetime import datetime

API_KEY = os.getenv("API_KEY", "").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "5"))
MENTION_ROLE_ID = os.getenv("MENTION_ROLE_ID", "").strip()  # 선택
DEBUG_LOGS = os.getenv("DEBUG_LOGS", "false").lower() in ("1", "true", "yes", "y")

if not API_KEY:
    raise RuntimeError("API_KEY env required")
if not DISCORD_WEBHOOK_URL:
    raise RuntimeError("DISCORD_WEBHOOK_URL env required")

app = FastAPI(title="MSG Alert Bot", version="1.1.0")


# ──────────────────────────────────────────────────────────────────────────────
# Models
# ──────────────────────────────────────────────────────────────────────────────
class BanPayload(BaseModel):
    ipAddress: str
    reason: str
    banType: str = Field(..., description="TEMPORARY | PERMANENT")
    bannedAt: str  # ISO8601
    expiresAt: Optional[str] = None
    bannedByAdminLoginId: Optional[str] = None
    durationMinutes: Optional[int] = None


# 백엔드가 보내던 레거시 포맷도 허용
class LegacyBanPayload(BaseModel):
    kind: Optional[str] = None
    environment: Optional[str] = None
    ip: str
    reason: Optional[str] = None
    banType: Optional[str] = None
    bannedAt: Optional[str] = None
    expiresAt: Optional[str] = None
    by: Optional[str] = None
    durationMinutes: Optional[int] = None


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def iso(v: Optional[Any]) -> str:
    if v is None or v == "":
        return "-"
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).isoformat()
        except Exception:
            return v
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def coerce_to_ban_payload(d: Dict[str, Any]) -> BanPayload:
    # 1) 신규 스키마 시도
    try:
        return BanPayload(**d)
    except ValidationError:
        pass

    # 2) 레거시 → 신규 매핑
    mapped = {
        "ipAddress": d.get("ip"),
        "reason": d.get("reason") or "-",
        "banType": d.get("banType") or "TEMPORARY",
        "bannedAt": d.get("bannedAt") or datetime.utcnow().isoformat(),
        "expiresAt": d.get("expiresAt"),
        "bannedByAdminLoginId": d.get("by") or "AUTO_BAN_SYSTEM",
        "durationMinutes": d.get("durationMinutes"),
    }
    return BanPayload(**mapped)


def extract_api_key(x_api_key: Optional[str], authorization: Optional[str]) -> str:
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    return ""


# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/healthz")
async def health():
    return {"ok": True}


@app.post("/alert/ban")
async def alert_ban(
    request: Request,
    x_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    # 인증: x-api-key 또는 Authorization: Bearer 모두 허용
    key = extract_api_key(x_api_key, authorization)
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid api key")

    # 원문 바디 파싱 (둘 다 지원하기 위해 raw dict로 받아 정규화)
    try:
        raw = await request.body()
        body_dict = json.loads(raw.decode("utf-8"))
        if DEBUG_LOGS:
            print("[ALERT RAW JSON]", raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=422, detail="invalid JSON body")

    # 스키마 정규화
    try:
        p = coerce_to_ban_payload(body_dict)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"schema error: {e}")

    # 디스코드 임베드
    color_red = 0xE11D48
    fields = [
        {"name": "IP", "value": f"`{p.ipAddress}`", "inline": True},
        {"name": "타입", "value": p.banType, "inline": True},
        {"name": "사유", "value": p.reason or "-", "inline": False},
        {"name": "차단시각", "value": iso(p.bannedAt), "inline": True},
        {"name": "만료시각", "value": iso(p.expiresAt), "inline": True},
        {"name": "관리자", "value": p.bannedByAdminLoginId or "AUTO_BAN_SYSTEM", "inline": True},
        {"name": "기간(분)", "value": str(p.durationMinutes) if p.durationMinutes else "-", "inline": True},
    ]

    content = f"<@&{MENTION_ROLE_ID}>" if MENTION_ROLE_ID else ""

    data = {
        "content": content,
        "embeds": [{
            "title": "🚫 IP Banned",
            "description": "자동/수동 차단 이벤트",
            "color": color_red,
            "fields": fields,
            "footer": {"text": "MSG CTF • IPBan"},
        }],
    }

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(DISCORD_WEBHOOK_URL, json=data)
        if r.status_code >= 300:
            # 디스코드 에러는 5xx로 래핑해서 백엔드가 재시도/로깅하기 좋게
            raise HTTPException(status_code=502, detail=f"discord webhook failed: {r.status_code} {r.text}")

    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8088")))
