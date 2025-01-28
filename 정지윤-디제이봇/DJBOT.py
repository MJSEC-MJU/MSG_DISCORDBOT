import discord
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput
import yt_dlp
import re
from yt_token import Token

intents = discord.Intents.default()
intents.message_content = True  # 메시지 내용에 대한 권한 활성화

bot = commands.Bot(command_prefix="!", intents=intents)

# 유튜브 URL 검증 양식
YOUTUBE_URL_REGEX = re.compile(
    r"(https?://)?(www\.)?(youtube\.com|youtu\.?be)/.+"  # URL 검증 정규식
)

# 플레이리스트
queue = []


# 노래 재생 버튼을 클릭했을 때 실행될 함수
async def play_song(interaction, url):
    try:
        # 응답을 지연 처리하지 않음, 한 번만 응답
        await interaction.response.send_message(f"✅ 노래가 재생됩니다: {url}")

    except Exception as e:
        await interaction.response.send_message(f"❌ 오류 발생: {str(e)}", ephemeral=True)


class MusicButtons(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

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

    @discord.ui.button(label="⏹ 노래 중단", style=discord.ButtonStyle.red)
    async def stop_song(self, interaction: discord.Interaction, button: discord.ui.Button):
        queue.clear()
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("⏹ 음악이 중지되었습니다.", ephemeral=True)

    @discord.ui.button(label="🎶 노래 재생", style=discord.ButtonStyle.green)
    async def play_song_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if queue:
            song = queue.pop(0)
            await play_song(interaction, song["url"])  # 첫 번째 노래부터 재생
        else:
            await interaction.response.send_message("🎶 대기열에 노래가 없습니다.", ephemeral=True)


class MusicModal(discord.ui.Modal, title="노래 추가"):
    url = discord.ui.TextInput(
        label="YouTube URL 입력", placeholder="https://www.youtube.com/watch?v=...")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()  # 응답을 지연 처리

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


@bot.command(name="음악패널")
async def music_panel(ctx):
    await ctx.send("🎶 **음악 컨트롤 패널**", view=MusicButtons())


@bot.event
async def on_ready():
    print(f"✅ {bot.user} 봇이 실행 중!")
    await bot.change_presence(status=discord.Status.online, activity=None)


@bot.command()
async def 입장(ctx):
    """봇을 음성 채널에 입장"""
    if ctx.author.voice:
        channel = ctx.author.voice.channel
        if ctx.voice_client:
            await ctx.voice_client.move_to(channel)
        else:
            await channel.connect()
            await ctx.send(f"✅ {channel.name} 채널에 입장했습니다.")

        # 입장 후 모달을 띄워서 선택할 수 있도록
        view = MusicButtons()  # 음성 채널에 입장한 후 음악 패널을 제공하는 버튼
        await ctx.send("🎶 **음악 컨트롤 패널**을 선택하세요.", view=view)

    else:
        await ctx.send("❌ 먼저 음성 채널에 들어가세요.")


@bot.command()
async def 나가(ctx):
    """봇을 음성 채널에서 퇴장"""
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        queue.clear()
        await ctx.send("👋 음성 채널에서 나갔습니다.")
    else:
        await ctx.send("❌ 현재 음성 채널에 연결되어 있지 않습니다.")

bot.run(Token)
