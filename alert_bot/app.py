# app.py
import os, json, math
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from typing import Optional, Any, Dict
import httpx
from datetime import datetime, timezone

API_KEY = os.getenv("API_KEY", "").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "5"))
MENTION_ROLE_ID = os.getenv("MENTION_ROLE_ID", "").strip()
DEBUG_LOGS = os.getenv("DEBUG_LOGS", "false").lower() in ("1", "true", "yes", "y")

if not API_KEY:
    raise RuntimeError("API_KEY env required")
if not DISCORD_WEBHOOK_URL:
    raise RuntimeError("DISCORD_WEBHOOK_URL env required")

app = FastAPI(title="MSG Alert Bot", version="1.2.0")

# ──────────────────────────────────────────────────────────────────────────────
# Models (관대한 입력 → 이후에 우리가 정규화)
# ──────────────────────────────────────────────────────────────────────────────
class BanPayload(BaseModel):
    ipAddress: str
    reason: str
    banType: str = Field(..., description="TEMPORARY | PERMANENT")
    bannedAt: Any                      # ← 어떤 타입이 와도 받음
    expiresAt: Optional[Any] = None    # ← 어떤 타입이 와도 받음
    bannedByAdminLoginId: Optional[str] = None
    durationMinutes: Optional[int] = None

class LegacyBanPayload(BaseModel):
    kind: Optional[str] = None
    environment: Optional[str] = None
    ip: str
    reason: Optional[str] = None
    banType: Optional[str] = None
    bannedAt: Optional[Any] = None
    expiresAt: Optional[Any] = None
    by: Optional[str] = None
    durationMinutes: Optional[int] = None

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def iso(v: Any) -> str:
    """여러 형태의 날짜 입력을 ISO8601 문자열로 정규화"""
    if v is None or v == "":
        return "-"
    # 이미 문자열
    if isinstance(v, str):
        s = v.strip().replace(" ", "T")
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()
        except Exception:
            return v  # 그래도 보여주기

    # 숫자: epoch(초/밀리초) 추정
    if isinstance(v, (int, float)):
        try:
            ts = float(v)
            if ts > 1e12:   # ms 기준으로 보임
                ts /= 1000.0
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.isoformat()
        except Exception:
            return str(v)

    # 배열: [Y,M,D,h,m,s,(ns)]
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        try:
            parts = list(v) + [0] * (7 - len(v))
            y, mo, d, hh, mm, ss, ns = parts[:7]
            # ns(나노초) → 마이크로초
            us = int(ns) // 1000 if isinstance(ns, (int, float)) else 0
            dt = datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0), us, tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            return str(v)

    # 객체: {year,month,day,hour,minute,second,nano}
    if isinstance(v, dict):
        try:
            y = v.get("year"); mo = v.get("month"); d = v.get("day")
            hh = v.get("hour", 0); mm = v.get("minute", 0); ss = v.get("second", 0)
            ns = v.get("nano", 0)
            if y and mo and d:
                us = int(ns) // 1000 if isinstance(ns, (int, float)) else 0
                dt = datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0), us, tzinfo=timezone.utc)
                return dt.isoformat()
        except Exception:
            pass
        return json.dumps(v, ensure_ascii=False)

    return str(v)

def extract_api_key(x_api_key: Optional[str], authorization: Optional[str]) -> str:
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    return ""

def coerce_to_ban_payload(d: Dict[str, Any]) -> BanPayload:
    # 1) 신규 스키마 시도
    try:
        return BanPayload(**d)
    except ValidationError:
        pass
    # 2) 레거시 스키마 → 신규 매핑
    try:
        legacy = LegacyBanPayload(**d)
        mapped = {
            "ipAddress": legacy.ip,
            "reason": legacy.reason or "-",
            "banType": legacy.banType or "TEMPORARY",
            "bannedAt": legacy.bannedAt or datetime.utcnow().isoformat(),
            "expiresAt": legacy.expiresAt,
            "bannedByAdminLoginId": legacy.by or "AUTO_BAN_SYSTEM",
            "durationMinutes": legacy.durationMinutes,
        }
        return BanPayload(**mapped)
    except ValidationError as e:
        # 마지막 방어: ip → ipAddress 만 강제 매핑 후 남은 값은 best-effort
        if "ip" in d and "ipAddress" not in d:
            d2 = d.copy()
            d2["ipAddress"] = d["ip"]
            return BanPayload(**d2)
        raise e

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
    key = extract_api_key(x_api_key, authorization)
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid api key")

    raw = await request.body()
    ctype = request.headers.get("content-type", "")
    if DEBUG_LOGS:
        print(f"[ALERT HEADERS] content-type={ctype} len={len(raw)}")
        try:
            print("[ALERT RAW JSON]", raw.decode("utf-8"))
        except Exception:
            print("[ALERT RAW BYTES]", raw[:128], "...")

    # JSON 파싱
    try:
        body_dict = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=422, detail="invalid JSON body")

    # 스키마 정규화
    try:
        p = coerce_to_ban_payload(body_dict)
    except ValidationError as e:
        if DEBUG_LOGS:
            print("[SCHEMA ERROR]", str(e))
        raise HTTPException(status_code=422, detail=f"schema error: {e}")

    # 디스코드 임베드 구성
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
            raise HTTPException(status_code=502, detail=f"discord webhook failed: {r.status_code} {r.text}")

    return {"ok": True}

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8088")))
