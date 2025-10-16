# DJBOT.py
# - yt_dlp 호출은 asyncio.to_thread로 오프로딩 → 이벤트 루프 블로킹 방지
# - 플레이리스트 재귀 차단(noplaylist)
# - 네트워크 타임아웃/재시도
# - FFmpeg PATH는 .env의 FFMPEG_PATH로 지정 가능(없으면 'ffmpeg')
# - DISCORD_BOT_TOKEN은 .env에서 읽음

import os
import re
import asyncio
import discord
import yt_dlp
from discord.ext import commands
from discord.ui import Modal
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")  # .env에 DISCORD_BOT_TOKEN=... 넣기
FFMPEG_PATH = os.getenv("FFMPEG_PATH") or "ffmpeg"  # 필요시 .env에 FFMPEG_PATH=C:/ffmpeg/bin/ffmpeg.exe 등

# ──────────────────────────────────────────────────────────────────────────────
# Intents (privileged members intent는 off: 게이트웨이 에러 방지)
# ──────────────────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True  # 음성 상태만 필요
# intents.members = False   # 기본값 False (명시 안 해도 됨)

bot = commands.Bot(command_prefix="!", intents=intents)

# 유튜브 URL 검증
YOUTUBE_URL_REGEX = re.compile(r"(https?://)?(www\.)?(youtube\.com|youtu\.?be)/.+")

# 대기열
queue = []              # [{ "title": str, "url": str }]
current_song = None     # 현재 곡 dict
queue_lock = asyncio.Lock()

# yt_dlp 옵션(정보 추출/스트림 공통)
YDL_OPTS_INFO = {
    "format": "bestaudio/best",
    "quiet": True,
    "noplaylist": True,        # 플레이리스트 재귀 방지
    "extract_flat": False,
    "socket_timeout": 10,      # 네트워크 타임아웃
    "retries": 2,
}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def _ytdlp_extract(url: str, ydl_opts: dict):
    """동기 함수(스레드에서 실행): yt_dlp 정보 추출"""
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def build_ffmpeg_source(stream_url: str) -> discord.FFmpegPCMAudio:
    """FFmpeg 오디오 소스 생성 (자동 재연결 옵션 포함)"""
    return discord.FFmpegPCMAudio(
        stream_url,
        before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        options="-vn",
        executable=FFMPEG_PATH,
    )


async def mute_all_members(channel: discord.VoiceChannel):
    """채널 내 모든 유저 음소거(봇 제외)"""
    for member in channel.members:
        if member.bot:
            continue
        try:
            await member.edit(mute=True)
        except Exception as e:
            print(f"❌ {member.display_name} 음소거 실패: {e}")


async def unmute_all_members(channel: discord.VoiceChannel):
    """채널 내 모든 유저 음소거 해제(봇 제외)"""
    for member in channel.members:
        if member.bot:
            continue
        try:
            await member.edit(mute=False)
        except Exception as e:
            print(f"❌ {member.display_name} 음소거 해제 실패: {e}")


async def play_next_song(ctx: commands.Context):
    """큐에서 다음 곡 재생"""
    global current_song
    async with queue_lock:
        if not queue:
            await ctx.send("🎵 대기열에 노래가 없습니다.")
            current_song = None
            return
        song = queue.pop(0)

    current_song = song
    vc = ctx.voice_client
    if not vc or not vc.is_connected():
        await ctx.send("❌ 음성 채널 연결이 끊어졌습니다.")
        return

    try:
        # yt_dlp 정보 추출은 스레드로 오프로딩
        info = await asyncio.to_thread(_ytdlp_extract, song["url"], YDL_OPTS_INFO)
        stream_url = info["url"]

        def _after_play(err):
            # 재생이 끝나면 다음 곡 재생 예약
            fut = play_next_song(ctx)
            asyncio.run_coroutine_threadsafe(fut, bot.loop)

        vc.play(build_ffmpeg_source(stream_url), after=_after_play)
        await ctx.send(f"🎶 **재생 중:** {song['title']}")
    except Exception as e:
        await ctx.send(f"❌ 재생 오류: {e}")
        # 오류 시 다음 곡 시도
        async with queue_lock:
            if queue:
                await play_next_song(ctx)


# ──────────────────────────────────────────────────────────────────────────────
# UI (Modal & Buttons)
# ──────────────────────────────────────────────────────────────────────────────
class MusicModal(Modal, title="노래 추가"):
    url = discord.ui.TextInput(
        label="YouTube URL 입력",
        placeholder="https://www.youtube.com/watch?v=...",
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        link = self.url.value.strip()
        if not YOUTUBE_URL_REGEX.match(link):
            await interaction.followup.send("❌ 유효한 YouTube URL을 입력하세요.", ephemeral=True)
            return

        try:
            info = await asyncio.to_thread(_ytdlp_extract, link, YDL_OPTS_INFO)
            title = info.get("title") or "제목 없음"
            async with queue_lock:
                queue.append({"title": title, "url": link})
            await interaction.followup.send(f"✅ 노래 추가됨: **{title}**", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 메타데이터 수집 오류: {e}", ephemeral=True)


class MusicButtons(discord.ui.View):
    def __init__(self, ctx: commands.Context):
        super().__init__(timeout=None)
        self.ctx = ctx

    @discord.ui.button(label="🎵 노래 추가", style=discord.ButtonStyle.green)
    async def add_song(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(MusicModal())

    @discord.ui.button(label="📜 플레이리스트 보기", style=discord.ButtonStyle.blurple)
    async def show_playlist(self, interaction: discord.Interaction, _button: discord.ui.Button):
        async with queue_lock:
            if not queue:
                await interaction.response.send_message("🎶 현재 대기 중인 노래가 없습니다.", ephemeral=True)
                return
            playlist = "\n".join([f"{i+1}. {s['title']}" for i, s in enumerate(queue)])
        await interaction.response.send_message(f"🎼 **현재 플레이리스트:**\n{playlist}", ephemeral=True)

    @discord.ui.button(label="🎶 노래 재생", style=discord.ButtonStyle.green)
    async def play_song_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            await interaction.followup.send("🔊 이미 노래가 재생 중입니다.", ephemeral=True)
            return

        async with queue_lock:
            has_queue = bool(queue)

        if has_queue:
            await play_next_song(self.ctx)
            await interaction.followup.send("🎶 노래를 재생합니다!", ephemeral=True)
        else:
            await interaction.followup.send("🎶 대기열이 비어 있습니다.", ephemeral=True)

    @discord.ui.button(label="⏭ 건너뛰기", style=discord.ButtonStyle.blurple)
    async def skip_song(self, interaction: discord.Interaction, _button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await interaction.response.send_message("⏭ 노래를 건너뛰었습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("⏭ 현재 재생 중인 노래가 없습니다.", ephemeral=True)

    @discord.ui.button(label="🛑 중단", style=discord.ButtonStyle.red)
    async def stop_song(self, interaction: discord.Interaction, _button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
        async with queue_lock:
            queue.clear()
        await interaction.response.send_message("🛑 재생 중단 & 대기열 초기화 완료.", ephemeral=True)


# ──────────────────────────────────────────────────────────────────────────────
# Commands
# ──────────────────────────────────────────────────────────────────────────────
@bot.command(name="음악패널")
async def music_panel(ctx: commands.Context):
    await ctx.send("🎶 **음악 컨트롤 패널**", view=MusicButtons(ctx))


@bot.command()
async def 입장(ctx: commands.Context):
    """봇을 음성 채널에 입장 + 모두 음소거 + 패널 노출"""
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("❌ 먼저 음성 채널에 들어가세요.")
        return

    channel = ctx.author.voice.channel
    if ctx.voice_client and ctx.voice_client.is_connected():
        await ctx.voice_client.move_to(channel)
    else:
        await channel.connect()
    await ctx.send(f"✅ {channel.name} 채널에 입장했습니다.")

    await mute_all_members(channel)
    await ctx.send("🎶 **음악 컨트롤 패널**을 선택하세요.", view=MusicButtons(ctx))


@bot.command()
async def 나가(ctx: commands.Context):
    """봇 퇴장 + 모두 음소거 해제 + 큐 초기화"""
    vc = ctx.voice_client
    if not vc:
        await ctx.send("❌ 현재 음성 채널에 연결되어 있지 않습니다.")
        return

    channel = vc.channel
    await unmute_all_members(channel)
    await vc.disconnect()
    async with queue_lock:
        queue.clear()
    await ctx.send("👋 음성 채널에서 나갔습니다. (모든 사용자 음소거 해제 & 대기열 비움)")


# ──────────────────────────────────────────────────────────────────────────────
# Events
# ──────────────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ {bot.user} 봇이 실행 중!")
    await bot.change_presence(status=discord.Status.online, activity=None)


# ──────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN 이(가) .env 에 설정되어 있지 않습니다.")
    bot.run(TOKEN)
