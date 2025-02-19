import discord, pytz, datetime
from discord.ext import commands,tasks
from discord.utils import get
import os
import asyncio
import json
from dotenv import load_dotenv
from aiohttp_sse_client import client as sse_client

load_dotenv()

DISCORD_BOT_TOKEN = str(os.getenv("DISCORD_BOT_TOKEN"))
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
DISCORD_SERVER_ID = int(os.getenv("DISCORD_SERVER_ID"))
CHALANGE_DISCORD_CHANNEL_ID = int(os.getenv("CHALANGE_DISCORD_CHANNEL_ID"))
API_URL = os.getenv("API_URL")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='/',intents=intents) #명령어

WINNER_FILE = "winner.json"
WINNER_DIC = {}
def load_winners():
    if os.path.exists(WINNER_FILE):
        with open(WINNER_FILE, "r",encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_winners(winners):
    with open(WINNER_FILE, "w",encoding="utf-8") as f:
        json.dump(winners, f, ensure_ascii=False, indent=4)

WINNER_DIC= load_winners()

# API요청
async def fetch_data():
    async with sse_client.EventSource(API_URL) as event_source:
        async for event in event_source:
            try:
                data = json.loads(event.data)
            except Exception as e:
                print("JSON 파싱 실패:", e)
                data = event.data
            return data
        
# 대회 우승자 발표 함수
async def announce_winner(ctx, wait_time, n):
    await asyncio.sleep(wait_time)  # 지정한 시간 후 실행
    try:
        data = await fetch_data()
        # API 응답 형식에 따라 results 할당
        if isinstance(data, list):
            results = data
        elif isinstance(data, dict) and "data" in data:
            results = data["data"]
        else:
            await ctx.send("API 응답이 올바르지 않습니다.")
            return

        # 1위 데이터 가져오기
        if results:
            winner = results[0]
            rank = 1
            name = winner["userid"]
            score = winner["totalPoint"]
            school = winner["univ"] if winner["univ"] else "N/A"

            embed = discord.Embed(
                title=f"🏆 **{n}회 대회 우승자 발표** 🏆",
                description="대회의 결과입니다.",
                color=0x00ff00
            )
            embed.add_field(name="순위", value=f"{rank}등", inline=True)
            embed.add_field(name="이름", value=name, inline=True)
            embed.add_field(name="점수", value=f"{score}점", inline=True)
            embed.add_field(name="학교", value=school, inline=True)
            embed.set_footer(text=f"대회 종료 시간: {datetime.datetime.now(pytz.timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S')}")

            WINNER_DIC[name] = n
            save_winners(WINNER_DIC)
            channel = bot.get_channel(DISCORD_CHANNEL_ID)
            await channel.send(embed=embed)
            await close_channel(ctx)
        else:
            await ctx.send("대회 결과가 없습니다.")
    except Exception as e:
        await ctx.send(f"오류 발생: {str(e)}")

async def schedule_open_channel(ctx, delay: float,n): #open time
    """지정된 delay(초) 후에 채널을 오픈하는 함수"""
    await asyncio.sleep(delay)
    await open_channel(ctx,n)
    channel = ctx.guild.get_channel(CHALANGE_DISCORD_CHANNEL_ID)
    if channel:
        await ctx.send(f"{channel.mention} 채널이 열렸습니다.")

async def open_channel(ctx,n): #channel open
    channel_id = CHALANGE_DISCORD_CHANNEL_ID
    channel = ctx.guild.get_channel(channel_id)
    if not channel:
        await ctx.send("NO CHANNEL")
        return
    overwrite = channel.overwrites_for(ctx.guild.default_role)
    overwrite.view_channel = True
    await channel.set_permissions(ctx.guild.default_role,overwrite=overwrite)
    await channel.send(f":loudspeaker: **지금 부터 제{n}회 대회를 시작합니다!**:loudspeaker: ")

async def close_channel(ctx): #channel close
    channel_id = CHALANGE_DISCORD_CHANNEL_ID
    channel = ctx.guild.get_channel(channel_id)
    if not channel:
        await ctx.send("NO CHANNEL")
        return
    overwrite = channel.overwrites_for(ctx.guild.default_role)
    overwrite.view_channel = False
    await channel.set_permissions(ctx.guild.default_role,overwrite=overwrite)

#이모지별 대학교
user_role = ""
ROLE_EMOJI_DIC={"\U00000031\U000020E3":"명지대학교",
                "\U00000032\U000020E3":"세종대학교",
                "\U00000033\U000020E3":"건국대학교"}

#동시 실행 방지
lock = asyncio.Lock()

#봇 시작 알림
@bot.event
async def on_ready():
    print("Bot is connecting to Discord")
    guild = bot.get_guild(DISCORD_SERVER_ID)
    for role_name in ROLE_EMOJI_DIC.values():
        if not discord.utils.get(guild.roles,name=role_name):
            await guild.create_role(name=role_name)
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if channel is None:
        print('NO CHANNEL')
        return
    await channel.send('CONNECTED')

@bot.command() #/대회시작 (시간) (대회 회차) / (시간 만큼 타이머 진행)
async def 대회시작(ctx, start:int, end:int, n:int):
    tz = pytz.timezone("Asia/Seoul")
    now = datetime.datetime.now(tz)
    # 오늘 날짜 기준으로 시작/종료 datetime 생성
    start_dt = now.replace(hour=start, minute=0, second=0, microsecond=0)
    end_dt = now.replace(hour=end, minute=0, second=0, microsecond=0)
    
    # 만약 종료 시간이 시작 시간보다 작거나 같다면(예: 23시 ~ 1시), 종료 시간을 다음날로 간주
    if end <= start:
        end_dt += datetime.timedelta(days=1)
    
    contest_duration = (end_dt - start_dt).total_seconds() / 3600  # 시간 단위
    wait_time = (end_dt - now).total_seconds()  # 지금부터 종료까지 남은 시간(초)
    open_wait_time = (start_dt - now).total_seconds()
    
    if wait_time <= 0:
        await ctx.send("이미 대회가 종료된 시간입니다.")
        return

    if open_wait_time < 0:
        open_wait_time = 0

    bot.loop.create_task(schedule_open_channel(ctx,open_wait_time,n))

    await ctx.send(f"⏰ **{start}시부터 {end}시까지 {contest_duration:.1f}시간동안 제 {n}회 대회를 진행합니다!**")
    # 별도의 태스크로 우승자 발표 함수를 실행 (대회 종료 시간까지 기다림)
    bot.loop.create_task(announce_winner(ctx, wait_time, n))

@bot.command()
async def 우승자(ctx):
    print("우승자 명령어 실행됨")
    if len(WINNER_DIC) == 0:
        await ctx.send("우승자가 없습니다!")
        return
    embed = discord.Embed(title="코딩 대회 우승자", timestamp=datetime.datetime.now(pytz.timezone('UTC')),color=0x00ff00)
    for name,n in WINNER_DIC.items(): 
        embed.add_field(name=f"{name}",value=f":trophy: 제 {n}회 우승자",inline=False)
    embed.set_thumbnail(url="https://tecoble.techcourse.co.kr/static/348a6c1ea3a4fa8b6990e3e3bf4e8490/20435/sample2.png")
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    await ctx.channel.send (embed=embed)

@bot.command()
async def 공지(ctx):
    i = (ctx.author.guild_permissions.send_messages)
    if i is True:
        notice = ctx.content[4:]
        channel = bot.get_channel(DISCORD_CHANNEL_ID)
        embed = discord.Embed(title="***[역할 선택]***",description="학교에 맞는 이모지를 선택 해주시기 바랍니다\n――――――――――――――――――――――――――――\n\n{}\n\n――――――――――――――――――――――――――――".format(notice),color=0x00ff00)
        embed.set_footer(text="TITLE | 담당관리자:{}".format(ctx.author))
        await channel.send("@everyone", embed=embed)
    if i is False:
        await ctx.channel.send("{}, 당신은 관리자가 아닙니다.".format(ctx.author))

@bot.command()
async def 역할공지(ctx):
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    embed = discord.Embed(title="***역할지급***",description="아래 이모티콘을 클릭하여 역할을 받으세요! \n\n"+"\n".join([f"{emoji} : {role}"for emoji, role in ROLE_EMOJI_DIC.items()]),color=discord.Color.blue())
    ctx = await ctx.channel.send(embed=embed)
    for emoji in ROLE_EMOJI_DIC.keys():
        try:
            await ctx.add_reaction(emoji)
        except discord.HTTPException as e:
            print("ERROR")

@bot.event#역할지급
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return
    
    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id)

    emoji = payload.emoji.name
    role_name = ROLE_EMOJI_DIC.get(emoji)

    if role_name:
        role = discord.utils.get(guild.roles, name = role_name)
        if not role:
            return
        
        async with lock:
            existing_roles = [discord.utils.get(guild.roles,name = r)for r in ROLE_EMOJI_DIC.values()]
            user_has_role = any(r in member.roles for r in existing_roles)
            channel = bot.get_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)

            if user_has_role:
                await member.send("역할은 하나만 선택할 수 있습니다.")
                try:
                    await message.remove_reaction(payload.emoji, member)
                except discord.Forbidden:
                    print("봇이 반응을 제거할 권한이 없습니다.")
                return

            if role:
                try:
                    await member.add_roles(role)
                except discord.Forbidden:
                    print("역할을 추가할 수 없습니다.")
                try:
                    await member.send(f"{role} 역할이 지급되었습니다.")
                except discord.Forbidden:
                    print(f"{member}에게 DM을 보낼 수 없습니다")

@bot.event #역할 삭제
async def on_raw_reaction_remove(payload):
    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id)

    emoji = payload.emoji.name
    role_name = ROLE_EMOJI_DIC.get(emoji)

    if role_name:
        role = discord.utils.get(guild.roles,name=role_name)
        if role:
            await member.remove_roles(role)
            try:
                await member.send(f"{role} 역할이 삭제되었습니다.")
            except discord.Forbidden:
                print(f"{member}에게 DM을 보낼 수 없습니다")

bot.run(DISCORD_BOT_TOKEN)