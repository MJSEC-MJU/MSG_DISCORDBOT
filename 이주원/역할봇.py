import discord, pytz, datetime
from discord.ext import commands,tasks
from discord.utils import get
import os
import asyncio
import requests
import json
import aiohttp
from dotenv import load_dotenv
import pytz

load_dotenv()

DISCORD_BOT_TOKEN = str(os.getenv("DISCORD_BOT_TOKEN"))
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
DISCORD_SERVER_ID = int(os.getenv("DISCORD_SERVER_ID"))
API_URL = str(os.getenv("API_URL"))

bot = commands.Bot(command_prefix='/',intents=discord.Intents.all()) #명령어

WINNER_FILE = "winner.json"

#토큰,서버아이디

#이모지별 대학교
user_role = ""
ROLE_EMOJI_DIC={"\U00000031\U000020E3":"명지대학교",
                "\U00000032\U000020E3":"세종대학교",
                "\U00000033\U000020E3":"건국대학교"}

def load_winners():
    if os.path.exists(WINNER_FILE):
        with open(WINNER_FILE, "r",encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_winners(winners):
    with open(WINNER_FILE, "w",encoding="utf-8") as f:
        json.dump(winners, f, ensure_ascii=False, indent=4)

WINNER_DIC= load_winners()

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


@bot.event
async def on_message(message):
    if message.content == "/우승자":#우승자 알림
        if len(WINNER_DIC) == 0:
            await message.send("우승자가 없습니다!")
            return
        embed = discord.Embed(title="코딩 대회 우승자", timestamp=datetime.datetime.now(pytz.timezone('UTC')),color=0x00ff00)
        for name,n in WINNER_DIC.items(): 
            embed.add_field(name=f"{name}",value=f":trophy: 제 {n}회 우승자",inline=False)
        embed.set_thumbnail(url="https://tecoble.techcourse.co.kr/static/348a6c1ea3a4fa8b6990e3e3bf4e8490/20435/sample2.png")
        channel = bot.get_channel(DISCORD_CHANNEL_ID)
        await message.channel.send (embed=embed)

    if message.content.startswith ("/공지"):#공지 작성
        i = (message.author.guild_permissions.send_messages)
        if i is True:
            notice = message.content[4:]
            channel = bot.get_channel(DISCORD_CHANNEL_ID)
            embed = discord.Embed(title="***[역할 선택]***",description="학교에 맞는 이모지를 선택 해주시기 바랍니다\n――――――――――――――――――――――――――――\n\n{}\n\n――――――――――――――――――――――――――――".format(notice),color=0x00ff00)
            embed.set_footer(text="TITLE | 담당관리자:".format(message.author))
            await channel.send("@everyone", embed=embed)
        if i is False:
            await message.channel.send("{}, 당신은 관리자가 아닙니다.".format(message.author))

    if message.content == "/역할공지":#역할지급 공지 한번만 공지해두면 실행시키고나서 이전 메시지에 이모지를 달아도 역할을 지급해줌
        channel = bot.get_channel(DISCORD_CHANNEL_ID)
        embed = discord.Embed(title="***역할지급***",description="아래 이모티콘을 클릭하여 역할을 받으세요! \n\n"+"\n".join([f"{emoji} : {role}"for emoji, role in ROLE_EMOJI_DIC.items()]),color=discord.Color.blue())
        ctx = await message.channel.send(embed=embed)
        for emoji in ROLE_EMOJI_DIC.keys():
            try:
                await ctx.add_reaction(emoji)
            except discord.HTTPException as e:
                print("ERROR")

bot.command()#/대회시작 (시간) (대회 회차)(시간 만큼 타이머 진행행)
async def 대회시작(ctx, hours: int, n:int):
    if not isinstance(hours,int) or hours <=0:
        await ctx.send("올바른 시간을 입력해 주세요")
        return
    if not isinstance(n,int) or n <=0:
        await ctx.send("올바른 대회 회차를 입력해 주세요")
        return
    await ctx.send(f"⏰ **{hours}시간 후 제 {n}회 대회가 종료 됩니다!**")
    
    async def fetch_data():
        async with aiohttp.ClientSession() as session:
            async with session.get(API_URL) as response:
                if response.status == 200:
                    return await response.json()
                return None

    async def announce_winner(ctx):
        """대회 우승자 발표"""
        await asyncio.sleep(hours * 3600)  # 초 단위 변경경

        try:
            data = await fetch_data()  # API 데이터 가져오기
            if data and "data" in data:
                results = data["data"]

                # 1위 데이터 가져오기
                if results:
                    winner = results[0]
                    rank = 1
                    name = winner["userid"]
                    score = winner["totalPoint"]
                    school = winner["univ"] if winner["univ"] else "N/A"

                    # 임베드 생성
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

                    # 우승자 저장
                    WINNER_DIC[name] = n
                    save_winners(WINNER_DIC)  # 우승자 저장 함수 호출

                    await ctx.send(embed=embed)  # 디스코드 채널에 메시지 전송
                else:
                    await ctx.send("대회 결과가 없습니다.")
            else:
                await ctx.send("API 응답이 올바르지 않습니다.")
        except Exception as e:
            await ctx.send(f"오류 발생: {str(e)}")
    asyncio.create_task(announce_winner())

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