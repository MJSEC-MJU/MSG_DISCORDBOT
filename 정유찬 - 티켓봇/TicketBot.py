import discord
from discord.ext import commands

import os
import asyncio

# 로그 폴더가 없는 경우 생성 (나중에 필요 없음)
if not os.path.exists("ticket_logs"):
    os.makedirs("ticket_logs")

# .env 파일에서 TOKEN과 CHANNEL_ID 로드
from dotenv import load_dotenv
load_dotenv()

TOKEN = os.getenv('TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')
GUILD_ID = os.getenv('GUILD_ID')


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # 뷰에 timeout 없음
        self.add_item(CreateTicketButton())

class CreateTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="📩 티켓 생성", style=discord.ButtonStyle.blurple, custom_id="create_ticket")

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild

        # 카테고리 가져오기 또는 생성
        category = discord.utils.get(guild.categories, name="Tickets")
        if not category:
            category = await guild.create_category("Tickets")

        # 사용자별 티켓 채널 확인
        existing_channel = discord.utils.get(
            category.text_channels,  # 카테고리 내 텍스트 채널만 검색
            name=f"ticket-{interaction.user.id}"
        )
        if existing_channel:
            embed = discord.Embed(
                title="❗ 이미 티켓이 존재합니다",
                description=(
                    f"이미 생성된 티켓이 있습니다: {existing_channel.mention}\n"
                    "해당 티켓에서 문제를 해결하거나 아래 버튼을 눌러 티켓을 종료하세요."
                ),
                color=discord.Color.red()
            )
            view = CloseTicketView()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return

        # 새 티켓 채널 생성
        channel = await guild.create_text_channel(
            f"ticket-{interaction.user.id}",
            category=category
        )

        # 권한 설정
        await channel.set_permissions(interaction.user, read_messages=True, send_messages=True)
        await channel.set_permissions(guild.me, read_messages=True, send_messages=True)
        await channel.set_permissions(guild.default_role, read_messages=False)

        # 자동화된 초기 메시지 전송
        embed = discord.Embed(
            title="🎫 티켓 생성됨",
            description=(
                "티켓이 성공적으로 생성되었습니다. 문제가 있다면 여기에 메시지를 남겨주세요.\n\n"
                "아래의 **'Close Ticket' 버튼**을 눌러 티켓을 종료할 수 있습니다."
            ),
            color=discord.Color.green()
        )
        embed.set_footer(text="티켓을 종료하면 대화 로그가 저장됩니다.")
        
        # Close Ticket 버튼 포함
        view = CloseTicketView()
        await channel.send(embed=embed, view=view)

        # 사용자에게 응답
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)

class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CloseTicketButton())

class CloseTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🔒 티켓 종료 (Close Ticket)", style=discord.ButtonStyle.red, custom_id="close_ticket")

    async def callback(self, interaction: discord.Interaction):
        category = interaction.channel.category

        # 로그 기록 저장
        log_content = ""
        async for msg in interaction.channel.history(limit=None, oldest_first=True):
            log_content += f"[{msg.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {msg.author}: {msg.content}\n"

        log_filename = f"ticket_logs/ticket-{interaction.channel.name}.txt"
        with open(log_filename, "w", encoding="utf-8") as f:
            f.write(log_content)

        # Interaction 응답 - 사용자가 해당 채널에서 볼 수 있는 메시지
        await interaction.response.send_message(
            "티켓 채널이 성공적으로 종료되었습니다. 필요 시 다시 생성해주세요!", ephemeral=True
        )

        # 1.5초 지연
        await asyncio.sleep(1.5)

        # 채널 삭제
        await interaction.channel.delete()

        # 카테고리 삭제
        if category and not category.text_channels:
            await category.delete()

        ''' 개인 태그 후 알림 메시지 전송 (예: @정유찬 티켓이 성공적으로 종료되었습니다. 필요 시 다시 생성해주세요!)
        # CHANNEL_ID로 알림 메시지 전송
        guild = interaction.guild
        channel = guild.get_channel(int(os.getenv("CHANNEL_ID")))  # CHANNEL_ID 가져오기
        if channel:
            await channel.send(
                content=f"{interaction.user.mention}, 티켓이 성공적으로 종료되었습니다. 필요 시 다시 생성해주세요!"
            )
        else:
            print(f"지정된 CHANNEL_ID({os.getenv('CHANNEL_ID')})가 유효하지 않습니다.")
        '''
class TicketBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.messages = True
        intents.guilds = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        try:
            '''특정 서버에서만 작동시키고 싶은 경우
            if GUILD_ID:
                guild = discord.Object(id=int(GUILD_ID))
                await self.tree.sync(guild=guild)
                print(f"/ 커맨드가 서버 {GUILD_ID}에 동기화되었습니다.")
            else:
                await self.tree.sync()
                print("/ 커맨드가 모든 서버에 동기화되었습니다.")
            '''
            await self.tree.sync()
            print("/ 커맨드가 모든 서버에 동기화되었습니다.")
        except Exception as e:
            print(f"명령어 동기화 중 에러 발생: {e}")

''' 초기 세팅
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.message_content = True
'''

bot = TicketBot()

''' 만약 티켓 서버를 따로 지정할 경우
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    bot.add_view(TicketView())  # 티켓 생성 뷰 등록
    # CHANNEL_ID로 지정된 채널에 티켓 생성 버튼 표시
    if CHANNEL_ID:
        try:
            channel = bot.get_channel(int(CHANNEL_ID))
            if channel:
                view = TicketView()
                await channel.send("티켓 생성을 위해 버튼을 눌러주세요!:", view=view)
                print(f"티켓 Setup 메시지 -> {CHANNEL_ID}")
            else:
                print(f"채널 ID {CHANNEL_ID}가 발견되지 않았습니다.")
        except Exception as e:
            print(f"티켓을 만드는데 에러가 발생했습니다...: {e}")
    else:
        print("CHANNEL_ID가 유효하지 않음.")
'''

@bot.tree.command(name="티켓", description="티켓 생성 버튼을 표시합니다.")
async def ticket(interaction: discord.Interaction):
    view = TicketView()

    # 티켓 사용법 임베드 생성
    help_embed = discord.Embed(
        title="🎟️ 티켓 사용 방법",
        description=(
            "**티켓 사용 가이드**\n\n"
            "1. `/티켓` 명령어를 입력해 티켓 생성 버튼을 표시합니다! \n"
            "2. `📩 티켓 생성` 버튼을 눌러 티켓 채널을 만듭니다.\n"
            "3. 생성된 티켓 채널 안에서 운영진과 대화를 할 수 있습니다!\n"
            "-💬 질문, 요청 등 운영진과 소통해야 할 대화가 있으시다면 티켓 봇을 통해 대화해주세요!!\n"
            "4. `🔒 티켓 종료` 버튼을 눌러 티켓을 종료할 수 있습니다.\n\n"
            "티켓 종료 시, 대화 내용이 저장되고 채널이 삭제됩니다."
        ),
        color=discord.Color.blurple()
    )
    help_embed.set_footer(text="다른 추가 질문이 있다면 운영진에게 문의해주세요!")

    await interaction.response.defer()

    message = await interaction.followup.send(
        content="티켓 생성을 위해 아래 버튼을 눌러주세요!",
        view=view,
        embed=help_embed,
        ephemeral=False  # 모든 사용자가 볼 수 있도록
    )

    await message.pin()
    print("티켓 생성 메시지를 고정했습니다.")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    #채널 id를 가져와야 함 (env 파일에서서)
    if CHANNEL_ID:
        try:
            channel = bot.get_channel(int(CHANNEL_ID)) 
            if channel:
                view = TicketView()
                
                # 티켓 사용법 임베드 생성
                help_embed = discord.Embed(
                    title="🎟️ 티켓 사용 방법",
                    description=(
                        "**티켓 사용 가이드**\n\n"
                        "1. `/티켓` 명령어를 입력해 티켓 생성 버튼을 표시합니다! \n"
                        "2. `📩 티켓 생성` 버튼을 눌러 티켓 채널을 만듭니다.\n"
                        "3. 생성된 티켓 채널 안에서 운영진과 대화를 할 수 있습니다!\n"
                        "-💬 질문, 요청 등 운영진과 소통해야 할 대화가 있으시다면 티켓 봇을 통해 대화해주세요!!\n"
                        "4. `🔒 티켓 종료` 버튼을 눌러 티켓을 종료할 수 있습니다.\n\n"
                        "티켓 종료 시, 대화 내용이 저장되고 채널이 삭제됩니다."
                    ),
                    color=discord.Color.blurple()
                )
                help_embed.set_footer(text="다른 추가 질문이 있다면 운영진에게 문의해주세요!")

                # 메시지 전송 (버튼 + 임베드)
                message =await channel.send(
                    content="티켓 생성을 위해 아래 버튼을 눌러주세요!",
                    view=view,
                    embed=help_embed
                )
                await message.pin()
                print(f"티켓 생성 메시지 전송 및 고정 완료 -> 채널 ID: {CHANNEL_ID}")
            else:
                print(f"유효하지 않은 채널 ID: {CHANNEL_ID}")
        except Exception as e:
            print(f"채널에 메시지를 전송하는 중 에러 발생: {e}")
    else:
        print("CHANNEL_ID가 설정되지 않았습니다.")

if TOKEN:
    bot.run(TOKEN)
else:
    print("에러: 토큰 설정 안됨.")
