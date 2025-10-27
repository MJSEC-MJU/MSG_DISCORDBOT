# app.py
import os, json, gzip, base64
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

app = FastAPI(title="MSG Alert Bot", version="1.4.0")

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
            return v
    if isinstance(v, (int, float)):
        try:
            ts = float(v)
            if ts > 1e12: ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except Exception:
            return str(v)
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        try:
            y, mo, d, hh, mm, ss = [int(x or 0) for x in list(v)[:6]]
            return datetime(y, mo, d, hh, mm, ss, tzinfo=timezone.utc).isoformat()
        except Exception:
            return str(v)
    if isinstance(v, dict):
        try:
            y, mo, d = v.get("year"), v.get("month"), v.get("day")
            hh, mm, ss = v.get("hour",0), v.get("minute",0), v.get("second",0)
            if y and mo and d:
                return datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss), tzinfo=timezone.utc).isoformat()
        except Exception:
            pass
        return json.dumps(v, ensure_ascii=False)
    return str(v)

def pick(d: Dict[str, Any], *names, default=None):
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default

async def read_raw(request: Request) -> bytes:
    raw = await request.body()
    enc = request.headers.get("content-encoding","").lower()
    if enc == "gzip" and raw:
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    return raw

def parse_body(raw: bytes, ctype: str) -> Dict[str, Any]:
    if not raw:
        return {}
    # JSON 먼저
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        pass
    # x-www-form-urlencoded
    try:
        form = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
        return {k: (v[0] if isinstance(v, list) and v else v) for k, v in form.items()}
    except Exception:
        pass
    return {}

@app.get("/healthz")
async def health():
    return {"ok": True}

@app.post("/alert/ban")
async def alert_ban(
    request: Request,
    x_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None)
):
    key = extract_api_key(x_api_key, authorization)
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid api key")

    # ── 로깅 ─────────────────────────────────────
    headers_for_log = {k.lower(): v for k, v in request.headers.items()}
    raw = await read_raw(request)
    ctype = headers_for_log.get("content-type","")
    clen  = headers_for_log.get("content-length","")
    if DEBUG_LOGS:
        print(f"[ALERT] method=POST path=/alert/ban ctype='{ctype}' clen='{clen}'")
        head_sample = {k: headers_for_log[k] for k in ["content-type","content-encoding","expect","connection","user-agent"] if k in headers_for_log}
        print("[ALERT] hdrs:", head_sample)
        # raw 미리보기(UTF-8) + 바이너리 안전(베이스64) 둘 다
        try:
            print("[ALERT] raw(utf8):", raw.decode("utf-8"))
        except Exception:
            print("[ALERT] raw(b64):", base64.b64encode(raw[:1024]).decode())

    # ── 파싱 ─────────────────────────────────────
    data = parse_body(raw, ctype)
    # 쿼리스트링 폴백(혹시 프록시가 바디를 떨궜을 때)
    if not data:
        qs = dict(request.query_params)
        if qs:
            data = qs

    # 키 매핑(여러 포맷 대응)
    ip = pick(data, "ipAddress","ip","ip_address")
    ban_type = pick(data, "banType","ban_type", default="TEMPORARY")
    reason = pick(data, "reason", default="-")
    banned_at = pick(data, "bannedAt","banned_at","time")
    expires_at = pick(data, "expiresAt","expires_at")
    by = pick(data, "bannedByAdminLoginId","by","admin", default="AUTO_BAN_SYSTEM")
    duration = pick(data, "durationMinutes","duration_minutes")

    fields = [
        {"name": "IP", "value": f"`{ip or '-'}`", "inline": True},
        {"name": "타입", "value": str(ban_type), "inline": True},
        {"name": "사유", "value": str(reason or "-"), "inline": False},
        {"name": "차단시각", "value": iso(banned_at), "inline": True},
        {"name": "만료시각", "value": iso(expires_at), "inline": True},
        {"name": "관리자", "value": str(by or "AUTO_BAN_SYSTEM"), "inline": True},
        {"name": "기간(분)", "value": str(duration) if duration is not None else "-", "inline": True},
    ]

    # 파싱 실패시 원문 RAW까지 디코에 첨부(디버깅용)
    if not ip:
        preview = ""
        try:
            preview = raw.decode("utf-8")
        except Exception:
            preview = base64.b64encode(raw[:2048]).decode()
        fields.append({"name": "RAW(payload)", "value": f"```{preview[:900]}```", "inline": False})
        if DEBUG_LOGS:
            print("[ALERT] parsed empty; attached RAW to embed.")

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
