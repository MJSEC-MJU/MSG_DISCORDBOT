import discord, pytz, datetime
from discord.ext import commands,tasks
from discord.utils import get
import os
from dotenv import load_dotenv

load_dotenv()
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_SERVER_ID = os.getenv("DISCORD_SERVER_ID")

bot = commands.Bot(command_prefix='/',intents=discord.Intents.all()) #명령어


#토큰,서버아이디

#이모지별 대학교
user_role = ""
ROLE_EMOJI_DIC={"\U00000031\U000020E3":"명지대학교",
                "\U00000032\U000020E3":"00대학교",
                "\U00000033\U000020E3":"01대학교"}

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
        channel.send('NO CHANNEL')
        return
    await channel.send('CONNECTED')


@bot.event
async def on_message(message):
    if message.content == "/우승자":#우승자 알림
        embed = discord.Embed(title="코딩 대회 우승자", timestamp=datetime.datetime.now(pytz.timezone('UTC')),color=0x00ff00)
        embed.add_field(name="유저네임",value=":trophy: 제 1회 우승자",inline=False)
        embed.add_field(name="유저네임",value=":trophy: 제 2회 우승자",inline=False)
        embed.add_field(name="유저네임",value=":trophy: 제 3회 우승자",inline=False)
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