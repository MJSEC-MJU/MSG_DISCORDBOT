# TiketBot.py — contest-time API + leaderboard SSE(팀 랭킹) + 자동 리스케줄 (상태 전이 시에만 공지)
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
# 리더보드 SSE (기본: 팀 랭킹 스트림)
API_URL = os.getenv("API_URL") or "https://msgctf.kr/api/leaderboard/stream"
# 대회 시간 API
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

# ====== 유틸 ======
def parse_server_time(s: str) -> datetime.datetime:
    """'YYYY-MM-DD HH:mm[:ss]' → KST aware datetime"""
    if len(s) == 16:  # 'YYYY-MM-DD HH:mm'
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
    """
    {startTime, endTime, currentTime} → (start_at, end_at, now_at)
    """
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

async def ensure_channel_open(guild: discord.Guild, n: int | None = None):
    channel = guild.get_channel(CHALANGE_DISCORD_CHANNEL_ID)
    if not channel:
        print("[channel] 대회 채널 없음")
        return
    overwrite = channel.overwrites_for(guild.default_role)
    if overwrite.view_channel is True:
        return
    overwrite.view_channel = True
    await channel.set_permissions(guild.default_role, overwrite=overwrite)
    if n:
        await channel.send(f":loudspeaker: **지금부터 제{n}회 MSG CTF 대회를 시작합니다!** :loudspeaker:")

async def ensure_channel_closed(guild: discord.Guild):
    channel = guild.get_channel(CHALANGE_DISCORD_CHANNEL_ID)
    if not channel:
        print("[channel] 대회 채널 없음")
        return
    overwrite = channel.overwrites_for(guild.default_role)
    if overwrite.view_channel is False:
        return
    overwrite.view_channel = False
    await channel.set_permissions(guild.default_role, overwrite=overwrite)

# ====== 리더보드 SSE 파싱 ======
async def fetch_data_from_sse():
    """
    리더보드 SSE에서 첫 유효 데이터(팀 배열)를 1회 수신
    - 서버가 'data:[{...}]'처럼 보내도 처리
    - 일부 클라이언트는 'event.data'에 JSON만 주므로 둘 다 지원
    """
    if not API_URL:
        return None
    try:
        async with sse_client.EventSource(API_URL) as event_source:
            async for event in event_source:
                payload = event.data
                if not payload:
                    continue
                # 원본 라인이 'data:[{...}]'로 오는 경우 제거
                if isinstance(payload, str) and payload.startswith("data:"):
                    payload = payload.split("data:", 1)[1].strip()
                try:
                    data = json.loads(payload)
                except Exception:
                    # keepalive 등 비-JSON 라인 무시
                    continue

                # 표준 팀 랭킹 배열
                if isinstance(data, list) and data:
                    return data
                # { "data": [...] } 래핑 형태도 허용
                if isinstance(data, dict) and isinstance(data.get("data"), list):
                    return data["data"]
    except Exception as e:
        print(f"[SSE] 수신 에러: {e}")
        return None

def pick_top_team(entries: list[dict]) -> dict | None:
    """
    rank가 있으면 rank 오름차순, 없으면 totalPoint 내림차순으로 1위 선별
    """
    if not entries:
        return None
    try:
        ranked = sorted(entries, key=lambda x: (x.get("rank", 10**9), -x.get("totalPoint", 0)))
        return ranked[0]
    except Exception:
        return entries[0]

async def post_winner_embed(guild: discord.Guild, n: int | None, when: datetime.datetime):
    """
    우승자 임베드 전송(팀 랭킹 기준) + 채널 닫기
    """
    teams = await fetch_data_from_sse()
    ch = guild.get_channel(CHALANGE_DISCORD_CHANNEL_ID) or guild.get_channel(DISCORD_CHANNEL_ID)

    if teams and ch:
        winner = pick_top_team(teams) or {}
        team_name = winner.get("teamName", "N/A")
        score = winner.get("totalPoint", 0)
        solved = winner.get("solvedCount", 0)
        rank = winner.get("rank", 1)

        round_text = f"{n}회" if n else (f"{DEFAULT_ROUND}회" if DEFAULT_ROUND else "대회")
        embed = discord.Embed(
            title=f"🏆 **{round_text} MSG CTF 우승 팀 발표** 🏆",
            description="대회의 결과입니다.",
            color=0x00ff00,
        )
        embed.add_field(name="순위", value=f"{rank}등", inline=True)
        embed.add_field(name="팀", value=team_name, inline=True)
        embed.add_field(name="점수", value=f"{score:,}점", inline=True)
        embed.add_field(name="푼 문제", value=f"{solved}개", inline=True)
        embed.set_footer(text=f"대회 종료 시간: {when.astimezone(KST).strftime('%Y-%m-%d %H:%M:%S')}")

        if n:
            WINNER_DIC[str(n)] = team_name
            save_winners(WINNER_DIC)

        await ch.send(embed=embed)

    await ensure_channel_closed(guild)

# ====== 스케줄 상태 ======
class ScheduleState:
    def __init__(self):
        self.start_at: datetime.datetime | None = None
        self.end_at: datetime.datetime | None = None
        self.round_no: int | None = None
        self.open_task: asyncio.Task | None = None
        self.announce_task: asyncio.Task | None = None
        # 중복 공지 방지용
        self.state: str | None = None          # 'BEFORE' | 'RUNNING' | 'ENDED'
        self.announced: bool = False           # 우승자 공지 1회만

    def cancel_all(self):
        for t in (self.open_task, self.announce_task):
            if t and not t.done():
                t.cancel()
        self.open_task = None
        self.announce_task = None
        self.announced = False
        self.state = None

schedule = ScheduleState()

async def schedule_from_api(
    guild: discord.Guild,
    round_no: int | None = None,
    notify_channel_id: int | None = None,
    silent: bool = False,
):
    """
    contest-time API를 읽어 현재/시작/종료 기준으로 자동 스케줄링.
    - 값이 변경되면 기존 예약 취소 후 재예약.
    - 채팅 공지는 silent=False 이고 (시간 변경 or 상태 전이)일 때만 보냄.
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

    # 현재 상태 계산
    if now_at >= end_at:
        new_state = "ENDED"
    elif now_at < start_at:
        new_state = "BEFORE"
    else:
        new_state = "RUNNING"

    should_notify = (not silent) and (changed or schedule.state != new_state)
    guild_channel = guild.get_channel(notify_channel_id) if notify_channel_id else None

    # --- 상태별 처리 ---
    if new_state == "ENDED":
        if not schedule.announced:
            await ensure_channel_closed(guild)
            await post_winner_embed(guild, schedule.round_no, end_at)
            schedule.announced = True

        if should_notify and guild_channel:
            await guild_channel.send(
                f"대회가 종료되었습니다. (종료: {end_at.strftime('%Y-%m-%d %H:%M:%S')})"
            )

    elif new_state == "BEFORE":
        if changed:
            open_delay = max(0.0, seconds_until(now_at, start_at))
            async def do_open():
                await asyncio.sleep(open_delay)
                await ensure_channel_open(guild, schedule.round_no)
            schedule.open_task = asyncio.create_task(do_open())

            announce_delay = max(0.0, seconds_until(now_at, end_at))
            async def do_announce():
                await asyncio.sleep(announce_delay)
                await post_winner_embed(guild, schedule.round_no, end_at)
                schedule.announced = True
            schedule.announce_task = asyncio.create_task(do_announce())

        if should_notify and guild_channel:
            open_in = int(max(0, seconds_until(now_at, start_at)))
            end_in = int(max(0, seconds_until(now_at, end_at)))
            await guild_channel.send(
                "🗓 대회 일정 동기화 완료\n"
                f" - 시작: {start_at.strftime('%Y-%m-%d %H:%M:%S')} (in {open_in}s)\n"
                f" - 종료: {end_at.strftime('%Y-%m-%d %H:%M:%S')} (in {end_in}s)"
            )

    else:  # RUNNING
        await ensure_channel_open(guild, schedule.round_no)

        if changed:
            announce_delay = max(0.0, seconds_until(now_at, end_at))
            async def do_announce():
                await asyncio.sleep(announce_delay)
                await post_winner_embed(guild, schedule.round_no, end_at)
                schedule.announced = True
            schedule.announce_task = asyncio.create_task(do_announce())

        if should_notify and guild_channel:
            left_in = int(max(0, seconds_until(now_at, end_at)))
            await guild_channel.send(
                "⏱ 대회가 진행 중으로 감지되어 채널을 열어두었습니다.\n"
                f" - 종료: {end_at.strftime('%Y-%m-%d %H:%M:%S')} (in {left_in}s)"
            )

    # 상태 갱신
    schedule.state = new_state

# ====== 백그라운드 감시(변경 자동 반영) ======
@tasks.loop(seconds=60)
async def watch_contest_time():
    """60초마다 contest-time을 확인해서 변경 시 재스케줄(조용히 동기화)"""
    try:
        guild = bot.get_guild(DISCORD_SERVER_ID)
        if guild is None:
            return
        await schedule_from_api(guild, schedule.round_no, notify_channel_id=None, silent=True)
    except Exception as e:
        print(f"[watch] 에러: {e}")

# ====== 기존 수동 명령(유지, 팀 랭킹으로 수정) ======
async def announce_winner(ctx, wait_time, n):
    await asyncio.sleep(wait_time)
    try:
        teams = await fetch_data_from_sse()
        if teams:
            winner = pick_top_team(teams) or {}
            team_name = winner.get("teamName", "N/A")
            score = winner.get("totalPoint", 0)
            solved = winner.get("solvedCount", 0)
            embed = discord.Embed(
                title=f"🏆 **{n}회 MSG CTF 우승 팀 발표** 🏆",
                description="대회의 결과입니다.",
                color=0x00ff00
            )
            embed.add_field(name="순위", value=f"{winner.get('rank', 1)}등", inline=True)
            embed.add_field(name="팀", value=team_name, inline=True)
            embed.add_field(name="점수", value=f"{score:,}점", inline=True)
            embed.add_field(name="푼 문제", value=f"{solved}개", inline=True)
            embed.set_footer(text=f"대회 종료 시간: {datetime.datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}")
            WINNER_DIC[str(n)] = team_name
            save_winners(WINNER_DIC)
            channel = bot.get_channel(CHALANGE_DISCORD_CHANNEL_ID)
            await channel.send(embed=embed)
            await ensure_channel_closed(ctx.guild)
        else:
            await ctx.send("대회 결과가 없습니다.")
    except Exception as e:
        await ctx.send(f"오류 발생: {str(e)}")

async def schedule_open_channel(ctx, delay: float, n: int):
    await asyncio.sleep(delay)
    await ensure_channel_open(ctx.guild, n)
    channel = ctx.guild.get_channel(CHALANGE_DISCORD_CHANNEL_ID)
    if channel:
        await ctx.send(f"{channel.mention} 채널이 열렸습니다.")

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
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if channel:
        await channel.send('CONNECTED')

    # 시작 시 한번 동기화(공지 O) + 감시 루프 시작(공지 X)
    if guild:
        await schedule_from_api(guild, DEFAULT_ROUND, DISCORD_CHANNEL_ID, silent=False)
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
    bot.loop.create_task(schedule_open_channel(ctx, open_wait_time, n))
    await ctx.send(f"⏰ **{start}시부터 {end}시까지 {contest_duration:.1f}시간동안 제 {n}회 대회를 진행합니다!**")
    bot.loop.create_task(announce_winner(ctx, wait_time, n))

@bot.command()  # 자동: /대회자동 [회차]
async def 대회자동(ctx, n: int | None = None):
    guild = ctx.guild or bot.get_guild(DISCORD_SERVER_ID)
    await schedule_from_api(guild, n or DEFAULT_ROUND, ctx.channel.id, silent=False)

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
