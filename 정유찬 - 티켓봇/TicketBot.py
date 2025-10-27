import discord
from discord.ext import commands
import os
import asyncio
import json
from datetime import datetime
from dotenv import load_dotenv

# 로그 폴더가 없는 경우 생성
if not os.path.exists("ticket_logs"):
    os.makedirs("ticket_logs")

# 설정 파일 경로
CONFIG_FILE = "ticket_config.json"

# .env 파일에서 환경변수 로드
load_dotenv()

TOKEN = os.getenv('TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')
GUILD_ID = os.getenv('GUILD_ID')
SUPPORT_ROLE_ID = os.getenv('SUPPORT_ROLE_ID')  # 운영진 역할 ID 
DEFAULT_SUPPORT_ROLE_NAME = "관리자"

class TicketConfig:
    """티켓 설정을 관리하는 클래스"""
    @staticmethod
    def load():
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        return {}
    
    @staticmethod
    def save(config):
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    
    @staticmethod
    def get_active_tickets():
        config = TicketConfig.load()
        return config.get('active_tickets', {})
    
    @staticmethod
    def add_ticket(user_id, channel_id):
        config = TicketConfig.load()
        if 'active_tickets' not in config:
            config['active_tickets'] = {}
        config['active_tickets'][str(user_id)] = channel_id
        TicketConfig.save(config)
    
    @staticmethod
    def remove_ticket(user_id):
        config = TicketConfig.load()
        if 'active_tickets' in config and str(user_id) in config['active_tickets']:
            del config['active_tickets'][str(user_id)]
            TicketConfig.save(config)

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CreateTicketButton())

class CreateTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="티켓 생성",
            style=discord.ButtonStyle.blurple,
            custom_id="persistent_create_ticket"  # persistent를 위한 고정 ID
        )

    async def callback(self, interaction: discord.Interaction):
        # 이미 처리 중인지 확인
        await interaction.response.defer(ephemeral=True)
        
        try:
            guild = interaction.guild
            user_id = str(interaction.user.id)
            
            # 카테고리 가져오기 또는 생성
            category = discord.utils.get(guild.categories, name="Tickets")
            if not category:
                category = await guild.create_category("Tickets")
            
            # 저장된 활성 티켓 확인
            active_tickets = TicketConfig.get_active_tickets()
            
            if user_id in active_tickets:
                channel_id = active_tickets[user_id]
                existing_channel = guild.get_channel(channel_id)
                
                if existing_channel:
                    embed = discord.Embed(
                        title="이미 티켓이 존재합니다",
                        description=(
                            f"이미 생성된 티켓이 있습니다: {existing_channel.mention}\n"
                            "해당 티켓에서 문제를 해결하거나 티켓을 종료하세요."
                        ),
                        color=discord.Color.red()
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    return
                else:
                    # 채널이 실제로는 없으면 설정에서 제거
                    TicketConfig.remove_ticket(user_id)
            
            # 새 티켓 채널 생성
            channel_name = f"ticket-{interaction.user.name}-{interaction.user.discriminator}"
            channel = await guild.create_text_channel(
                channel_name,
                category=category,
                topic=f"Ticket by {interaction.user.mention} | Created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
            # 권한 설정
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True,
                    attach_files=True,
                    embed_links=True
                ),
                guild.me: discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True,
                    manage_channels=True,
                    manage_messages=True
                )
            }
            
            support_role = None

            # 운영진 역할이 있다면 권한 추가
            if SUPPORT_ROLE_ID:
                support_role = guild.get_role(int(SUPPORT_ROLE_ID))

            if not support_role:
                support_role = discord.utils.get(guild.roles, name=DEFAULT_SUPPORT_ROLE_NAME)
            
            if support_role:
                overwrites[support_role] = discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True,
                    manage_messages=True
                )
            
            # 관리자 권한 추가
            for role in guild.roles:
                if role.permissions.administrator:
                    overwrites[role] = discord.PermissionOverwrite(
                        read_messages=True,
                        send_messages=True,
                        manage_messages=True
                    )
            
            await channel.edit(overwrites=overwrites)
            
            # 설정 파일에 티켓 저장
            TicketConfig.add_ticket(user_id, channel.id)
            
            # 초기 메시지 전송
            embed = discord.Embed(
                title="티켓이 생성되었습니다",
                description=(
                    f"**티켓 생성자:** {interaction.user.mention}\n"
                    f"**생성 시간:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    "문제가 있다면 여기에 메시지를 남겨주세요.\n"
                    "운영진이 곧 확인하고 답변드리겠습니다.\n\n"
                    "해결이 완료되면 아래의 **'티켓 종료' 버튼**을 눌러주세요."
                ),
                color=discord.Color.green()
            )
            embed.set_footer(text="티켓을 종료하면 대화 로그가 저장됩니다.")
            
            view = CloseTicketView()
            await channel.send(embed=embed, view=view)
            
            # 운영진에게 알림 (옵션)
            if support_role:
                await channel.send(f"{support_role.mention} 새 티켓이 생성되었습니다!")
            
            # 사용자에게 응답
            success_embed = discord.Embed(
                title="티켓 생성 완료",
                description=f"티켓이 생성되었습니다: {channel.mention}",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=success_embed, ephemeral=True)
            
        except Exception as e:
            error_embed = discord.Embed(
                title="오류 발생",
                description=f"티켓 생성 중 오류가 발생했습니다.\n`{str(e)}`",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=error_embed, ephemeral=True)
            print(f"티켓 생성 오류: {e}")

class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CloseTicketButton())

class CloseTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="티켓 종료 (Close Ticket)",
            style=discord.ButtonStyle.red,
            custom_id="persistent_close_ticket"  # persistent를 위한 고정 ID
        )
        self.closing = False  # 중복 클릭 방지

    async def callback(self, interaction: discord.Interaction):
        # 이미 종료 중인지 확인
        if self.closing:
            await interaction.response.send_message(
                "이미 티켓이 종료 중입니다. 잠시만 기다려주세요.",
                ephemeral=True
            )
            return
        
        self.closing = True
        await interaction.response.defer(ephemeral=True)
        
        try:
            channel = interaction.channel
            category = channel.category
            
            # 티켓 채널인지 확인
            if not channel.name.startswith("ticket-"):
                await interaction.followup.send(
                    "이 채널은 티켓 채널이 아닙니다.",
                    ephemeral=True
                )
                self.closing = False
                return
            
            # 로그 저장
            log_content = f"=== 티켓 로그 ===\n"
            log_content += f"채널: {channel.name}\n"
            log_content += f"종료자: {interaction.user}\n"
            log_content += f"종료 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            log_content += "=" * 50 + "\n\n"
            
            message_count = 0
            async for msg in channel.history(limit=None, oldest_first=True):
                # 봇의 시스템 메시지는 제외하고 실제 대화만 기록
                if not msg.author.bot or (msg.content and not msg.embeds):
                    log_content += f"[{msg.created_at.strftime('%Y-%m-%d %H:%M:%S')}] "
                    log_content += f"{msg.author.name}: {msg.content}\n"
                    if msg.attachments:
                        for attachment in msg.attachments:
                            log_content += f"  첨부파일: {attachment.url}\n"
                    message_count += 1
            
            log_content += f"\n총 {message_count}개의 메시지가 기록되었습니다."
            
            # 로그 파일 저장
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            log_filename = f"ticket_logs/ticket_{channel.name}_{timestamp}.txt"
            with open(log_filename, "w", encoding="utf-8") as f:
                f.write(log_content)
            
            # 티켓 생성자 찾기
            user_id = None
            for uid, cid in TicketConfig.get_active_tickets().items():
                if cid == channel.id:
                    user_id = uid
                    break
            
            # 설정에서 티켓 제거
            if user_id:
                TicketConfig.remove_ticket(user_id)
            
            # 종료 메시지
            await interaction.followup.send(
                "티켓이 종료되었습니다. 3초 후 이 채널이 삭제됩니다.",
                ephemeral=True
            )
            
            # 채널 삭제 전 대기
            await asyncio.sleep(3)
            
            # 채널 삭제
            try:
                await channel.delete(reason=f"티켓 종료 - {interaction.user}")
            except discord.Forbidden:
                print(f"채널 삭제 권한 없음: {channel.name}")
            except Exception as e:
                print(f"채널 삭제 오류: {e}")
            
            # 카테고리가 비어있으면 삭제
            if category and len(category.channels) == 0:
                try:
                    await category.delete(reason="빈 티켓 카테고리 삭제")
                except:
                    pass
                    
        except Exception as e:
            print(f"티켓 종료 오류: {e}")
            try:
                await interaction.followup.send(
                    f"티켓 종료 중 오류가 발생했습니다: {str(e)}",
                    ephemeral=True
                )
            except:
                pass
        finally:
            self.closing = False

class TicketBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.messages = True
        intents.guilds = True
        intents.message_content = True
        intents.members = True  # 멤버 관련 권한 추가
        super().__init__(command_prefix="!", intents=intents)
        self.persistent_views_added = False

    async def setup_hook(self):
        # Persistent Views 등록
        if not self.persistent_views_added:
            self.add_view(TicketView())
            self.add_view(CloseTicketView())
            self.persistent_views_added = True
        
        # 슬래시 커맨드 동기화
        try:
            if GUILD_ID:
                guild = discord.Object(id=int(GUILD_ID))
                await self.tree.sync(guild=guild)
                print(f"명령어가 서버 {GUILD_ID}에 동기화되었습니다.")
            else:
                await self.tree.sync()
                print("명령어가 모든 서버에 동기화되었습니다.")
        except Exception as e:
            print(f"명령어 동기화 중 에러 발생: {e}")

bot = TicketBot()

@bot.tree.command(name="티켓", description="티켓 생성 버튼을 표시합니다.")
@discord.app_commands.default_permissions(manage_channels=True)  # 관리자만 사용 가능
async def ticket(interaction: discord.Interaction):
    await interaction.response.defer()
    
    view = TicketView()
    
    # 티켓 사용법 임베드
    help_embed = discord.Embed(
        title="티켓 시스템",
        description=(
            "**티켓 사용 가이드**\n\n"
            "**티켓 생성**\n"
            "아래 버튼을 눌러 운영진과 1:1 대화 채널을 생성할 수 있습니다.\n\n"
            "**티켓 사용**\n"
            "• 질문, 건의사항, 신고 등을 편하게 작성해주세요\n"
            "• 운영진이 확인 후 답변드립니다\n"
            "• 파일 첨부도 가능합니다\n\n"
            "**티켓 종료**\n"
            "문제가 해결되면 티켓 내 종료 버튼을 눌러주세요.\n"
            "대화 내용은 자동으로 저장됩니다."
        ),
        color=discord.Color.blurple(),
        timestamp=datetime.now()
    )
    help_embed.set_footer(text="티켓은 1인당 1개만 생성 가능합니다")
    
    await interaction.followup.send(
        embed=help_embed,
        view=view
    )

@bot.tree.command(name="티켓정리", description="비활성 티켓 채널을 정리합니다.")
@discord.app_commands.default_permissions(administrator=True)  # 관리자만 사용
async def cleanup_tickets(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    cleaned = 0
    active_tickets = TicketConfig.get_active_tickets()
    
    for user_id, channel_id in list(active_tickets.items()):
        channel = interaction.guild.get_channel(channel_id)
        if not channel:
            TicketConfig.remove_ticket(user_id)
            cleaned += 1
    
    embed = discord.Embed(
        title="티켓 정리 완료",
        description=f"존재하지 않는 {cleaned}개의 티켓 정보를 정리했습니다.",
        color=discord.Color.green()
    )
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.event
async def on_ready():
    print(f"봇 로그인 완료: {bot.user}")
    print(f"서버 수: {len(bot.guilds)}")
    
    # 봇 상태 설정
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="티켓 요청"
        )
    )
    
    # 기존 티켓 채널 확인 및 정리
    config = TicketConfig.load()
    if 'active_tickets' in config:
        print(f"활성 티켓 수: {len(config['active_tickets'])}")
    
    # CHANNEL_ID가 설정되어 있으면 자동으로 티켓 메시지 생성
    if CHANNEL_ID:
        try:
            channel = bot.get_channel(int(CHANNEL_ID))
            if channel:
                # 중복 방지: 최근 50개 메시지 중 봇이 보낸 티켓 메시지가 있는지 확인
                ticket_message_exists = False
                
                async for message in channel.history(limit=50):
                    # 봇이 보낸 메시지이고, 티켓 생성 버튼이 있는지 확인
                    if message.author == bot.user:
                        # 임베드가 있고 "티켓 시스템" 또는 "티켓 사용" 텍스트가 있는지 확인
                        if message.embeds:
                            for embed in message.embeds:
                                if "티켓 시스템" in str(embed.title) or "티켓 사용" in str(embed.description):
                                    ticket_message_exists = True
                                    print(f"기존 티켓 메시지 발견 - 새 메시지 생성 건너뜀")
                                    break
                        
                        # View 컴포넌트가 있는지 확인 (버튼 확인)
                        if message.components:
                            for row in message.components:
                                for component in row.children:
                                    if hasattr(component, 'label') and "티켓 생성" in str(component.label):
                                        ticket_message_exists = True
                                        print(f"기존 티켓 버튼 발견 - 새 메시지 생성 건너뜀")
                                        break
                    
                    if ticket_message_exists:
                        break
                
                # 티켓 메시지가 없을 때만 새로 생성
                if not ticket_message_exists:
                    view = TicketView()
                    
                    # 티켓 사용법 임베드 생성
                    help_embed = discord.Embed(
                        title="티켓 시스템",
                        description=(
                            "**티켓 사용 가이드**\n\n"
                            "**티켓 생성**\n"
                            "아래 버튼을 눌러 운영진과 1:1 대화 채널을 생성할 수 있습니다.\n\n"
                            "**티켓 사용**\n"
                            "• 질문, 건의사항, 신고 등을 편하게 작성해주세요\n"
                            "• 운영진이 확인 후 답변드립니다\n"
                            "• 파일 첨부도 가능합니다\n\n"
                            "**티켓 종료**\n"
                            "문제가 해결되면 티켓 내 종료 버튼을 눌러주세요.\n"
                            "대화 내용은 자동으로 저장됩니다."
                        ),
                        color=discord.Color.blurple(),
                        timestamp=datetime.now()
                    )
                    help_embed.set_footer(text="티켓은 1인당 1개만 생성 가능합니다")
                    
                    # 메시지 전송 및 고정
                    message = await channel.send(
                        content="티켓 생성을 위해 아래 버튼을 눌러주세요!",
                        view=view,
                        embed=help_embed
                    )
                    
                    # 메시지 고정 (선택사항)
                    try:
                        await message.pin()
                        print(f"티켓 생성 메시지 전송 및 고정 완료 -> 채널 ID: {CHANNEL_ID}")
                    except discord.Forbidden:
                        print(f"티켓 생성 메시지 전송 완료 (고정 권한 없음) -> 채널 ID: {CHANNEL_ID}")
                    except discord.HTTPException:
                        print(f"티켓 생성 메시지 전송 완료 (고정 메시지 한계 도달) -> 채널 ID: {CHANNEL_ID}")
            else:
                print(f"유효하지 않은 채널 ID: {CHANNEL_ID}")
        except Exception as e:
            print(f"채널에 메시지를 전송하는 중 에러 발생: {e}")
    else:
        print("CHANNEL_ID가 설정되지 않았습니다. /티켓 명령어로 티켓 시스템을 활성화할 수 있습니다.")

# 에러 핸들러
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    print(f"에러 발생: {error}")

if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("에러: 토큰이 설정되지 않았습니다. .env 파일을 확인하세요.")