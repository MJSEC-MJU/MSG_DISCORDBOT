# app.py
import os, json
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from typing import Optional, Any, Dict
import httpx
from datetime import datetime, timezone
from urllib.parse import parse_qs

API_KEY = os.getenv("API_KEY", "").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "5"))
MENTION_ROLE_ID = os.getenv("MENTION_ROLE_ID", "").strip()
DEBUG_LOGS = os.getenv("DEBUG_LOGS", "false").lower() in ("1","true","yes","y")

if not API_KEY:
    raise RuntimeError("API_KEY env required")
if not DISCORD_WEBHOOK_URL:
    raise RuntimeError("DISCORD_WEBHOOK_URL env required")

app = FastAPI(title="MSG Alert Bot", version="1.3.0")

# ─────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────
def extract_api_key(x_api_key: Optional[str], authorization: Optional[str]) -> str:
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    if authorization:
        parts = authorization.split(" ", 2)
        if len(parts) >= 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    return ""

def iso(v: Any) -> str:
    if v is None or v == "":
        return "-"
    if isinstance(v, str):
        s = v.strip().replace(" ", "T")
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()
        except Exception:
            return v  # 원문 그대로 노출
    if isinstance(v, (int, float)):
        try:
            ts = float(v)
            if ts > 1e12:  # ms로 보이면 s로 환산
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except Exception:
            return str(v)
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        try:
            parts = list(v) + [0]*(7 - len(v))
            y, mo, d, hh, mm, ss, ns = parts[:7]
            us = int(ns) // 1000 if isinstance(ns, (int, float)) else 0
            return datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0), us, tzinfo=timezone.utc).isoformat()
        except Exception:
            return str(v)
    if isinstance(v, dict):
        try:
            y = v.get("year"); mo = v.get("month"); d = v.get("day")
            hh = v.get("hour", 0); mm = v.get("minute", 0); ss = v.get("second", 0)
            ns = v.get("nano", 0)
            if y and mo and d:
                us = int(ns) // 1000 if isinstance(ns, (int, float)) else 0
                return datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0), us, tzinfo=timezone.utc).isoformat()
        except Exception:
            pass
        return json.dumps(v, ensure_ascii=False)
    return str(v)

def pick(d: Dict[str, Any], *names, default=None):
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default

def parse_body(raw: bytes, ctype: str) -> Dict[str, Any]:
    # 1) JSON 시도
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        pass
    # 2) x-www-form-urlencoded 시도
    try:
        form = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
        return {k: (v[0] if isinstance(v, list) and v else v) for k, v in form.items()}
    except Exception:
        pass
    # 3) 모르겠으면 빈 객체
    return {}

# ─────────────────────────────────────────────
# routes
# ─────────────────────────────────────────────
@app.get("/healthz")
async def health():
    return {"ok": True}

@app.post("/alert/ban")
async def alert_ban(request: Request,
                    x_api_key: Optional[str] = Header(None),
                    authorization: Optional[str] = Header(None)):
    key = extract_api_key(x_api_key, authorization)
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid api key")

    raw = await request.body()
    ctype = request.headers.get("content-type","")
    if DEBUG_LOGS:
        print(f"[ALERT] ctype={ctype} len={len(raw)}")
        try:
            print("[ALERT] raw:", raw.decode("utf-8"))
        except Exception:
            print("[ALERT] raw-bytes:", raw[:128], "...")

    data = parse_body(raw, ctype)

    # 신규/레거시/다양한 키 대응
    ip = pick(data, "ipAddress", "ip", "ip_address")
    ban_type = pick(data, "banType", "ban_type", default="TEMPORARY")
    reason = pick(data, "reason", default="-")
    banned_at = pick(data, "bannedAt", "banned_at", "time")
    expires_at = pick(data, "expiresAt", "expires_at")
    by = pick(data, "bannedByAdminLoginId", "by", "admin", default="AUTO_BAN_SYSTEM")
    duration = pick(data, "durationMinutes", "duration_minutes")

    # 최소 정보 채우기: IP 없으면 원문을 같이 보여주도록 함
    fields = [
        {"name": "IP", "value": f"`{ip or '-'}`", "inline": True},
        {"name": "타입", "value": str(ban_type), "inline": True},
        {"name": "사유", "value": str(reason or "-"), "inline": False},
        {"name": "차단시각", "value": iso(banned_at), "inline": True},
        {"name": "만료시각", "value": iso(expires_at), "inline": True},
        {"name": "관리자", "value": str(by or "AUTO_BAN_SYSTEM"), "inline": True},
        {"name": "기간(분)", "value": str(duration) if duration is not None else "-", "inline": True},
    ]

    # ip가 비어있으면 원문 전체를 추가로 붙여서 디버깅에 도움
    if not ip:
        try:
            pretty = json.dumps(data, ensure_ascii=False, separators=(",",":"))
        except Exception:
            pretty = str(data)
        fields.append({"name": "원문(payload)", "value": f"`{pretty[:1000]}`", "inline": False})

    content = f"<@&{MENTION_ROLE_ID}>" if MENTION_ROLE_ID else ""
    embed = {
        "title": "🚫 IP Banned",
        "description": "자동/수동 차단 이벤트",
        "color": 0xE11D48,
        "fields": fields,
        "footer": {"text": "MSG CTF • IPBan"},
    }

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(DISCORD_WEBHOOK_URL, json={"content": content, "embeds": [embed]})
        if r.status_code >= 300:
            raise HTTPException(status_code=502, detail=f"discord webhook failed: {r.status_code} {r.text}")

    return {"ok": True}

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT","8088")))
