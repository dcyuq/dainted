import logging

import discord
from discord import app_commands
from discord.ext import commands

import embeds
from greeter import Feature, can_manage, open_panel
from storage import Store

log = logging.getLogger(__name__)

EVENTS = [
    ("join", "Welcome", "when someone joins"),
    ("leave", "Goodbye", "when someone leaves"),
]


def defaults(kind):
    if kind == "join":
        return {
            "enabled": False,
            "channel_id": None,
            "mode": "embed_plain",
            "content": "{mention}",
            "title": "Welcome",
            "message": "welcome to **{server}**, {mention} — you're member **{count}**.",
            "color": None,
            "image_url": None,
            "thumbnail": "avatar",
            "ping": True,
            "replace_default": True,
        }
    return {
        "enabled": False,
        "channel_id": None,
        "mode": "embed_plain",
        "content": "",
        "title": "Goodbye",
        "message": "**{user}** left **{server}**.",
        "color": None,
        "image_url": None,
        "thumbnail": "avatar",
        "ping": False,
    }


feature = Feature(
    key="welcome",
    label="Welcome",
    store=Store("welcome.json"),
    events=EVENTS,
    defaults=defaults,
)


class Welcome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if can_manage(ctx.author):
            return True
        raise commands.MissingPermissions(["manage_guild"])

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        count = member.guild.member_count or len(member.guild.members)
        await feature.dispatch(member, member.guild, "join", count)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        count = member.guild.member_count or len(member.guild.members)
        await feature.dispatch(member, member.guild, "leave", count)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        settings = feature.settings.get(str(channel.guild.id))
        if not settings:
            return
        changed = False
        for kind, _, _ in EVENTS:
            event = settings.get(kind)
            if event and event.get("channel_id") == channel.id:
                event["channel_id"] = None
                event["enabled"] = False
                changed = True
        if changed:
            feature.save()

    @commands.hybrid_command(name="welcome", aliases=["wel"],
                             description="Set up the welcome message.")
    @app_commands.default_permissions(manage_guild=True)
    @commands.guild_only()
    async def welcome(self, ctx):
        await open_panel(ctx, feature, "join")

    @commands.hybrid_command(name="goodbye", aliases=["leave"],
                             description="Set up the leave message.")
    @app_commands.default_permissions(manage_guild=True)
    @commands.guild_only()
    async def goodbye(self, ctx):
        await open_panel(ctx, feature, "leave")


async def setup(bot):
    await bot.add_cog(Welcome(bot))