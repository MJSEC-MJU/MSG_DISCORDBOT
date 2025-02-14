from yt_token import Token
import re
import yt_dlp
from discord.ui import Button, View, Modal, TextInput
from discord.ext import commands
import discord
import asyncio

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 유튜브 URL 검증 정규식
YOUTUBE_URL_REGEX = re.compile(
    r"(https?://)?(www\.)?(youtube\.com|youtu\.?be)/.+"
)

queue = []  # 노래 대기열
current_song = None  # 현재 재생 중인 노래 정보


async def mute_all_members(guild, channel):
    """모든 멤버 음소거"""
    for member in channel.members:
        if not member.bot:
            try:
                await member.edit(mute=True)
            except Exception as e:
                print(f"❌ {member.display_name} 음소거 실패: {e}")


async def unmute_all_members(guild, channel):
    """모든 멤버 음소거 해제"""
    for member in channel.members:
        if not member.bot:
            try:
                await member.edit(mute=False)
            except Exception as e:
                print(f"❌ {member.display_name} 음소거 해제 실패: {e}")


async def play_next_song(ctx):
    """큐에서 다음 노래를 가져와 재생"""
    global current_song

    if not queue:
        await ctx.send("🎵 대기열에 노래가 없습니다.")
        return

    song = queue.pop(0)
    current_song = song

    try:
        voice_client = ctx.voice_client
        if not voice_client or not voice_client.is_connected():
            return

        ydl_opts = {
            "format": "bestaudio/best",
            "quiet": True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(song["url"], download=False)
            url = info["url"]

        ffmpeg_options = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn"
        }
        voice_client.play(discord.FFmpegPCMAudio(url, **ffmpeg_options),
                          after=lambda e: asyncio.run_coroutine_threadsafe(play_next_song(ctx), bot.loop))

        await ctx.send(f"🎶 **재생 중:** {song['title']}")

    except Exception as e:
        await ctx.send(f"❌ 오류 발생: {str(e)}")


class MusicModal(discord.ui.Modal, title="노래 추가"):
    url = discord.ui.TextInput(
        label="YouTube URL 입력", placeholder="https://www.youtube.com/watch?v=...")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()

        url = self.url.value.strip()
        if not YOUTUBE_URL_REGEX.match(url):
            await interaction.followup.send("❌ 유효한 YouTube URL을 입력하세요.", ephemeral=True)
            return

        try:
            ydl_opts = {"format": "bestaudio/best", "quiet": True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                title = info.get("title", "제목 없음")
                queue.append({"title": title, "url": url})
                await interaction.followup.send(f"✅ 노래 추가됨: {title}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 오류 발생: {str(e)}", ephemeral=True)


class MusicButtons(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=None)
        self.ctx = ctx

    @discord.ui.button(label="🎵 노래 추가", style=discord.ButtonStyle.green)
    async def add_song(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(MusicModal())

    @discord.ui.button(label="📜 플레이리스트 보기", style=discord.ButtonStyle.blurple)
    async def show_playlist(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not queue:
            await interaction.response.send_message("🎶 현재 대기 중인 노래가 없습니다.", ephemeral=True)
            return

        playlist = "\n".join(
            [f"{idx+1}. {song['title']}" for idx, song in enumerate(queue)]).strip()
        await interaction.response.send_message(f"🎼 **현재 플레이리스트:**\n{playlist}", ephemeral=True)

    @discord.ui.button(label="🎶 노래 재생", style=discord.ButtonStyle.green)
    async def play_song_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()  # 응답을 지연시켜 NotFound 예외 방지

        if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
            await interaction.followup.send("🔊 이미 노래가 재생 중입니다.", ephemeral=True)
            return

        if queue:
            await play_next_song(self.ctx)  # 올바르게 노래 재생 함수 호출
            await interaction.followup.send("🎶 노래를 재생합니다!", ephemeral=True)
        else:
            await interaction.followup.send("🎶 대기열이 비어 있습니다.", ephemeral=True)

    @discord.ui.button(label="⏭ 노래 건너뛰기", style=discord.ButtonStyle.blurple)
    async def skip_song(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.stop()
            await interaction.response.send_message("⏭ 노래를 건너뛰었습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("⏭ 현재 재생 중인 노래가 없습니다.", ephemeral=True)

    @discord.ui.button(label="🛑 노래 중단", style=discord.ButtonStyle.red)
    async def stop_song(self, interaction: discord.Interaction, button: discord.ui.Button):
        """현재 재생 중인 노래를 중단하고 큐를 초기화"""
        if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.stop()
            queue.clear()
            await interaction.response.send_message("🛑 노래를 중단하고 대기열을 초기화했습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("🛑 현재 재생 중인 노래가 없습니다.", ephemeral=True)


@bot.command(name="음악패널")
async def music_panel(ctx):
    await ctx.send("🎶 **음악 컨트롤 패널**", view=MusicButtons(ctx))


@bot.command()
async def 입장(ctx):
    """봇을 음성 채널에 입장하고 모든 사용자를 음소거"""
    if ctx.author.voice:
        channel = ctx.author.voice.channel
        if ctx.voice_client:
            await ctx.voice_client.move_to(channel)
        else:
            await channel.connect()
            await ctx.send(f"✅ {channel.name} 채널에 입장했습니다.")

        await mute_all_members(ctx.guild, channel)
        await ctx.send("🎶 **음악 컨트롤 패널**을 선택하세요.", view=MusicButtons(ctx))
    else:
        await ctx.send("❌ 먼저 음성 채널에 들어가세요.")


@bot.command()
async def 나가(ctx):
    """봇을 음성 채널에서 퇴장하고 모든 사용자의 음소거를 해제"""
    if ctx.voice_client:
        channel = ctx.voice_client.channel
        await unmute_all_members(ctx.guild, channel)  # 음소거 해제
        await ctx.voice_client.disconnect()
        queue.clear()
        await ctx.send("👋 음성 채널에서 나갔습니다. (모든 사용자 음소거 해제)")
    else:
        await ctx.send("❌ 현재 음성 채널에 연결되어 있지 않습니다.")


@bot.event
async def on_ready():
    print(f"✅ {bot.user} 봇이 실행 중!")
    await bot.change_presence(status=discord.Status.online, activity=None)

bot.run(Token)
