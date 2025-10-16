# TiketBot.py — contest-time API + leaderboard SSE(Top3) + 자동 리스케줄 + 시작/종료 1회 공지(중복 방지)
import os
import json
import asyncio
import datetime
import pytz
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from aiohttp_sse_client import client as sse_client
import aiohttp

load_dotenv()

# ====== 환경변수 ======
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN") or ""
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID") or 0)                # 알림 채널
DISCORD_SERVER_ID = int(os.getenv("DISCORD_SERVER_ID") or 0)
CHALANGE_DISCORD_CHANNEL_ID = int(os.getenv("CHALANGE_DISCORD_CHANNEL_ID") or 0)  # 대회 채널(개폐)
API_URL = os.getenv("API_URL") or "https://msgctf.kr/api/leaderboard/stream"      # 팀 랭킹 SSE
CONTEST_TIME_URL = os.getenv("CONTEST_TIME_URL") or "https://msgctf.kr/api/contest-time"
DEFAULT_ROUND = int(os.getenv("CONTEST_ROUND") or 0)  # 회차 기본값(선택)

# ====== Discord 기본 ======
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='/', intents=intents)

KST = pytz.timezone("Asia/Seoul")

# ====== 우승자 저장 ======
WINNER_FILE = "winner.json"
def load_winners():
    if os.path.exists(WINNER_FILE):
        with open(WINNER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}
def save_winners(winners):
    with open(WINNER_FILE, "w", encoding="utf-8") as f:
        json.dump(winners, f, ensure_ascii=False, indent=4)
WINNER_DIC = load_winners()

# ====== 역할 이모지 매핑 ======
ROLE_EMOJI_DIC = {
    "1️⃣": "명지대학교",
    "2️⃣": "순천향대학교",
    "3️⃣": "건국대학교",
    "4️⃣": "상명대학교",
    "5️⃣": "중앙대학교",
    "6️⃣": "기타",
}

# ====== 동시 실행 방지 락 ======
lock = asyncio.Lock()
announce_lock = asyncio.Lock()  # 시작/종료 공지 중복 방지 용

# ====== 유틸 ======
def parse_server_time(s: str) -> datetime.datetime:
    """'YYYY-MM-DD HH:mm[:ss]' → KST aware datetime"""
    if len(s) == 16:
        s += ":00"
    dt_naive = datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    return KST.localize(dt_naive)

async def http_get_json(url: str):
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as r:
                if r.status != 200:
                    print(f"[contest-time] HTTP {r.status}")
                    return None
                return await r.json(content_type=None)
    except Exception as e:
        print(f"[contest-time] GET 실패: {e}")
        return None

async def fetch_contest_time():
    """{startTime, endTime, currentTime} → (start_at, end_at, now_at)"""
    data = await http_get_json(CONTEST_TIME_URL)
    if not data:
        return None
    try:
        start_at = parse_server_time(data["startTime"])
        end_at = parse_server_time(data["endTime"])
        now_raw = data.get("currentTime") or datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        now_at = parse_server_time(now_raw)
        return {"start_at": start_at, "end_at": end_at, "now_at": now_at}
    except Exception as e:
        print(f"[contest-time] 파싱 오류: {e}, data={data}")
        return None

def seconds_until(a: datetime.datetime, b: datetime.datetime) -> float:
    return (b - a).total_seconds()

def round_text(n: int | None) -> str:
    return f"{n}회" if n else (f"{DEFAULT_ROUND}회" if DEFAULT_ROUND else "대회")

async def channel_by_pref(guild: discord.Guild):
    return guild.get_channel(CHALANGE_DISCORD_CHANNEL_ID) or guild.get_channel(DISCORD_CHANNEL_ID)

async def ensure_channel_open(guild: discord.Guild):
    ch = guild.get_channel(CHALANGE_DISCORD_CHANNEL_ID)
    if not ch:
        print("[channel] 대회 채널 없음")
        return
    overwrite = ch.overwrites_for(guild.default_role)
    if overwrite.view_channel is True:
        return
    overwrite.view_channel = True
    await ch.set_permissions(guild.default_role, overwrite=overwrite)

async def ensure_channel_closed(guild: discord.Guild):
    ch = guild.get_channel(CHALANGE_DISCORD_CHANNEL_ID)
    if not ch:
        print("[channel] 대회 채널 없음")
        return
    overwrite = ch.overwrites_for(guild.default_role)
    if overwrite.view_channel is False:
        return
    overwrite.view_channel = False
    await ch.set_permissions(guild.default_role, overwrite=overwrite)

# ====== 리더보드 SSE ======
async def fetch_data_from_sse():
    """리더보드 SSE에서 첫 유효 데이터(팀 배열) 1회 수신"""
    if not API_URL:
        return None
    try:
        async with sse_client.EventSource(API_URL) as event_source:
            async for event in event_source:
                payload = event.data
                if not payload:
                    continue
                if isinstance(payload, str) and payload.startswith("data:"):
                    payload = payload.split("data:", 1)[1].strip()
                try:
                    data = json.loads(payload)
                except Exception:
                    continue
                if isinstance(data, list) and data:
                    return data
                if isinstance(data, dict) and isinstance(data.get("data"), list):
                    return data["data"]
    except Exception as e:
        print(f"[SSE] 수신 에러: {e}")
        return None

def top_n(entries: list[dict], n: int = 3) -> list[dict]:
    if not entries:
        return []
    try:
        ranked = sorted(entries, key=lambda x: (x.get("rank", 10**9), -x.get("totalPoint", 0)))
    except Exception:
        ranked = entries
    return ranked[:n]

async def send_start_announcement(guild: discord.Guild, n: int | None, end_at: datetime.datetime):
    ch = await channel_by_pref(guild)
    if not ch:
        return
    rt = round_text(n)
    embed = discord.Embed(
        title=f"🚀 **{rt} MSG CTF 시작!**",
        description="모든 참가자 여러분, 행운을 빕니다!",
        color=0x00ff00
    )
    embed.add_field(name="종료 시간", value=end_at.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S"), inline=False)
    await ch.send("@everyone", embed=embed)

async def post_winner_embed(guild: discord.Guild, n: int | None, when: datetime.datetime):
    """우승 공지: 팀 랭킹 상위 3팀 공지 → 채널 닫기"""
    teams = await fetch_data_from_sse()
    ch = await channel_by_pref(guild)

    if teams and ch:
        tops = top_n(teams, 3)
        rt = round_text(n)
        embed = discord.Embed(
            title=f"🏆 **{rt} MSG CTF 최종 결과 (TOP 3)**",
            color=0x00ff00,
        )
        lines = []
        for t in tops:
            r = t.get("rank")
            name = t.get("teamName", "N/A")
            pts = t.get("totalPoint", 0)
            solved = t.get("solvedCount", 0)
            lines.append(f"**{r}등** — {name}  ·  {pts:,}점  ·  {solved}문제")
        embed.description = "\n".join(lines) if lines else "결과를 불러오지 못했습니다."
        embed.set_footer(text=f"대회 종료 시간: {when.astimezone(KST).strftime('%Y-%m-%d %H:%M:%S')}")

        if n and tops:
            WINNER_DIC[str(n)] = tops[0].get("teamName", "N/A")
            save_winners(WINNER_DIC)

        await ch.send(embed=embed)

    await ensure_channel_closed(guild)

# ====== 중복 방지용 원샷 헬퍼 ======
def iso_key(dt: datetime.datetime) -> str:
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")

async def announce_start_once(guild: discord.Guild, n: int | None, start_at: datetime.datetime, end_at: datetime.datetime):
    key = f"START|{iso_key(start_at)}"
    async with announce_lock:
        if schedule.start_key == key:
            return
        schedule.start_key = key
    await ensure_channel_open(guild)
    await send_start_announcement(guild, n, end_at)

async def announce_end_once(guild: discord.Guild, n: int | None, end_at: datetime.datetime):
    key = f"END|{iso_key(end_at)}"
    async with announce_lock:
        if schedule.end_key == key:
            return
        schedule.end_key = key
    await post_winner_embed(guild, n, end_at)

# ====== 스케줄 상태 ======
class ScheduleState:
    def __init__(self):
        self.start_at: datetime.datetime | None = None
        self.end_at: datetime.datetime | None = None
        self.round_no: int | None = None
        self.open_task: asyncio.Task | None = None
        self.announce_task: asyncio.Task | None = None
        # 중복 방지 키(유니크 시간 단위)
        self.start_key: str | None = None
        self.end_key: str | None = None
    def cancel_all(self):
        for t in (self.open_task, self.announce_task):
            if t and not t.done():
                t.cancel()
        self.open_task = None
        self.announce_task = None
        # 시간 변경되면 새 키를 사용해야 하므로 리셋
        self.start_key = None
        self.end_key = None

schedule = ScheduleState()

async def schedule_from_api(guild: discord.Guild, round_no: int | None = None, notify_channel_id: int | None = None):
    """
    contest-time API 기반 자동 스케줄링.
    - 시작 전: 채널 오픈 + 시작 공지 예약(1회)
    - 진행 중: 즉시 오픈 + 시작 공지(1회)
    - 종료 시: TOP3 공지(1회) + 닫기
    """
    info = await fetch_contest_time()
    if not info:
        print("[schedule] contest-time 불러오기 실패")
        return

    start_at, end_at, now_at = info["start_at"], info["end_at"], info["now_at"]
    schedule.round_no = round_no
    changed = (schedule.start_at != start_at) or (schedule.end_at != end_at)

    if changed:
        schedule.cancel_all()
        schedule.start_at, schedule.end_at = start_at, end_at

    guild_channel = guild.get_channel(notify_channel_id or DISCORD_CHANNEL_ID)
    now = now_at

    # 이미 종료됨 → 즉시 종료 공지(원샷)
    if now >= end_at:
        await announce_end_once(guild, schedule.round_no, end_at)
        if changed and guild_channel:
            await guild_channel.send(f"대회가 이미 종료되었습니다. (종료: {end_at.strftime('%Y-%m-%d %H:%M:%S')})")
        return

    # 시작 전
    if now < start_at:
        open_delay = max(0.0, seconds_until(now, start_at))
        async def do_open():
            await asyncio.sleep(open_delay)
            await announce_start_once(guild, schedule.round_no, start_at, end_at)
        schedule.open_task = asyncio.create_task(do_open())

        announce_delay = max(0.0, seconds_until(now, end_at))
        async def do_announce():
            await asyncio.sleep(announce_delay)
            await announce_end_once(guild, schedule.round_no, end_at)
        schedule.announce_task = asyncio.create_task(do_announce())

        if changed and guild_channel:
            await guild_channel.send(
                "🗓 대회 일정 동기화 완료\n"
                f" - 시작: {start_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f" - 종료: {end_at.strftime('%Y-%m-%d %H:%M:%S')}"
            )
        return

    # 진행 중
    if start_at <= now < end_at:
        # 즉시 시작 공지(원샷)
        await announce_start_once(guild, schedule.round_no, start_at, end_at)

        announce_delay = max(0.0, seconds_until(now, end_at))
        async def do_announce():
            await asyncio.sleep(announce_delay)
            await announce_end_once(guild, schedule.round_no, end_at)
        schedule.announce_task = asyncio.create_task(do_announce())

        if changed and guild_channel:
            await guild_channel.send(
                "⏱ 대회가 진행 중으로 감지되어 채널을 열어두었습니다.\n"
                f" - 종료: {end_at.strftime('%Y-%m-%d %H:%M:%S')}"
            )

# ====== 백그라운드 감시(변경 자동 반영) ======
@tasks.loop(seconds=60)
async def watch_contest_time():
    """60초마다 contest-time을 확인해서 변경 시 재스케줄"""
    try:
        guild = bot.get_guild(DISCORD_SERVER_ID)
        if guild is None:
            return
        await schedule_from_api(guild, schedule.round_no)
    except Exception as e:
        print(f"[watch] 에러: {e}")

# ====== 수동 명령(Top3 + 동일 원샷 경로 사용) ======
async def announce_winner(ctx, wait_time, n, end_dt):
    await asyncio.sleep(wait_time)
    await announce_end_once(ctx.guild, n, end_dt)

async def schedule_open_channel(ctx, delay: float, n: int, start_dt: datetime.datetime, end_dt: datetime.datetime):
    await asyncio.sleep(delay)
    await announce_start_once(ctx.guild, n, start_dt, end_dt)

# ====== 이벤트/명령어 ======
@bot.event
async def on_ready():
    print("Bot is connecting to Discord")
    guild = bot.get_guild(DISCORD_SERVER_ID)
    # 역할 자동 생성
    if guild:
        for role_name in ROLE_EMOJI_DIC.values():
            if not discord.utils.get(guild.roles, name=role_name):
                try:
                    await guild.create_role(name=role_name)
                except Exception:
                    pass
    ch = bot.get_channel(DISCORD_CHANNEL_ID)
    if ch:
        await ch.send('CONNECTED')

    # 시작 시 동기화 + 감시 루프 시작
    if guild:
        await schedule_from_api(guild, DEFAULT_ROUND, DISCORD_CHANNEL_ID)
        if not watch_contest_time.is_running():
            watch_contest_time.start()

@bot.command()  # 수동: /대회시작 시작시 종료시 회차
async def 대회시작(ctx, start: int, end: int, n: int):
    now = datetime.datetime.now(KST)
    start_dt = now.replace(hour=start, minute=0, second=0, microsecond=0)
    end_dt = now.replace(hour=end, minute=0, second=0, microsecond=0)
    if end <= start:
        end_dt += datetime.timedelta(days=1)

    contest_duration = (end_dt - start_dt).total_seconds() / 3600
    wait_time = (end_dt - now).total_seconds()
    open_wait_time = max(0, (start_dt - now).total_seconds())
    if wait_time <= 0:
        await ctx.send("이미 대회가 종료된 시간입니다.")
        return

    await ctx.send(
        f"⏰ **{start}시부터 {end}시까지 {contest_duration:.1f}시간 동안 제 {n}회 대회를 진행합니다!**\n"
        f" - 시작: {start_dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f" - 종료: {end_dt.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    bot.loop.create_task(schedule_open_channel(ctx, open_wait_time, n, start_dt, end_dt))
    bot.loop.create_task(announce_winner(ctx, wait_time, n, end_dt))

@bot.command()  # 자동: /대회자동 [회차]
async def 대회자동(ctx, n: int | None = None):
    await ctx.reply("대회 일정 동기화를 시도합니다...")
    guild = ctx.guild or bot.get_guild(DISCORD_SERVER_ID)
    await schedule_from_api(guild, n or DEFAULT_ROUND, ctx.channel.id)

@bot.command()
async def 우승자(ctx):
    if not WINNER_DIC:
        await ctx.send("우승자가 없습니다!")
        return
    embed = discord.Embed(
        title="MSG CTF 대회 우승 팀",
        timestamp=datetime.datetime.now(pytz.UTC),
        color=0x00ff00
    )
    for n, name in WINNER_DIC.items():
        embed.add_field(name=f"{name}", value=f":trophy: 제 {n}회 우승 팀", inline=False)
    embed.set_thumbnail(url="https://tecoble.techcourse.co.kr/static/348a6c1ea3a4fa8b6990e3e3bf4e8490/20435/sample2.png")
    await ctx.channel.send(embed=embed)

@bot.command()
async def 공지(ctx, *, notice):
    if ctx.author.guild_permissions.send_messages:
        channel = bot.get_channel(CHALANGE_DISCORD_CHANNEL_ID)
        embed = discord.Embed(
            title="***[공지]***",
            description="공지 입니다!\n――――――――――――――――――――――――――――\n\n{}\n\n――――――――――――――――――――――――――――".format(notice),
            color=0x00ff00
        )
        embed.set_footer(text=f"TITLE | 담당관리자:{ctx.author}")
        await channel.send("@everyone", embed=embed)
    else:
        await ctx.channel.send(f"{ctx.author}, 당신은 관리자가 아닙니다.")

@bot.command()
async def 역할공지(ctx):
    embed = discord.Embed(
        title="***역할지급***",
        description="아래 이모티콘을 클릭하여 역할을 받으세요! \n\n" +
                    "\n".join([f"{emoji} : {role}" for emoji, role in ROLE_EMOJI_DIC.items()]),
        color=discord.Color.blue()
    )
    msg = await ctx.channel.send(embed=embed)
    for emoji in ROLE_EMOJI_DIC.keys():
        try:
            await msg.add_reaction(emoji)
        except discord.HTTPException:
            print("리액션 추가 실패")

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return
    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id)
    emoji = payload.emoji.name
    role_name = ROLE_EMOJI_DIC.get(emoji)
    if role_name:
        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            return
        async with lock:
            existing_roles = [discord.utils.get(guild.roles, name=r) for r in ROLE_EMOJI_DIC.values()]
            user_has_role = any(r in member.roles for r in existing_roles if r)
            channel = bot.get_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
            if user_has_role:
                try:
                    await member.send("역할은 하나만 선택할 수 있습니다.")
                except discord.Forbidden:
                    pass
                try:
                    await message.remove_reaction(payload.emoji, member)
                except discord.Forbidden:
                    print("봇이 반응을 제거할 권한이 없습니다.")
                return
            try:
                await member.add_roles(role)
                try:
                    await member.send(f"{role} 역할이 지급되었습니다.")
                except discord.Forbidden:
                    pass
            except discord.Forbidden:
                print("역할을 추가할 수 없습니다.")

@bot.event
async def on_raw_reaction_remove(payload):
    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id)
    emoji = payload.emoji.name
    role_name = ROLE_EMOJI_DIC.get(emoji)
    if role_name:
        role = discord.utils.get(guild.roles, name=role_name)
        if role:
            try:
                await member.remove_roles(role)
                try:
                    await member.send(f"{role} 역할이 삭제되었습니다.")
                except discord.Forbidden:
                    pass
            except discord.Forbidden:
                print("역할을 제거할 수 없습니다.")

bot.run(DISCORD_BOT_TOKEN)
