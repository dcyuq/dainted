import logging

import discord
from discord import app_commands
from discord.ext import commands

import embeds
from greeter import Feature, can_manage, open_panel
from storage import Store

log = logging.getLogger(__name__)

EVENTS = [("boost", "Booster", "when someone boosts")]

BOOST_TYPES = {
    discord.MessageType.premium_guild_subscription,
    discord.MessageType.premium_guild_tier_1,
    discord.MessageType.premium_guild_tier_2,
    discord.MessageType.premium_guild_tier_3,
}


def defaults(kind):
    return {
        "enabled": False,
        "channel_id": None,
        "mode": "embed_plain",
        "content": "{mention}",
        "title": "New booster",
        "message": "thank you for boosting **{server}**, {mention} — we're at **{boosts}** boosts now.",
        "color": None,
        "image_url": None,
        "thumbnail": "avatar",
        "ping": True,
        "replace_default": True,
    }


def boost_tokens(member, guild):
    return {
        "boosts": str(guild.premium_subscription_count or 0),
        "boosters": str(len(guild.premium_subscribers)),
        "level": str(guild.premium_tier),
    }


feature = Feature(
    key="booster",
    label="Booster",
    store=Store("booster.json"),
    events=EVENTS,
    defaults=defaults,
    token_extra=boost_tokens,
    extra_toggles=[
        ("replace_default", "Replace default", "removes Discord's line", "keeps Discord's line"),
    ],
    extra_placeholders=[
        ("{boosts}", "the boost count"),
        ("{boosters}", "how many people boost"),
        ("{level}", "the boost tier"),
    ],
)


class Booster(commands.Cog):
    """Announce new server boosts."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if can_manage(ctx.author):
            return True
        raise commands.MissingPermissions(["manage_guild"])

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.type not in BOOST_TYPES:
            return

        event = feature.event(message.guild.id, "boost")

        if not event.get("enabled"):
            return

        if event.get("replace_default", True):
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass

        member = message.guild.get_member(message.author.id) or message.author
        # fall back to the channel the boost happened in when none is set
        channel = message.guild.get_channel(event.get("channel_id")) or message.channel
        count = message.guild.member_count or len(message.guild.members)
        await feature.dispatch(member, message.guild, "boost", count, channel_override=channel)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        settings = feature.settings.get(str(channel.guild.id))
        if not settings:
            return
        event = settings.get("boost")
        if event and event.get("channel_id") == channel.id:
            event["channel_id"] = None
            feature.save()

    @commands.hybrid_command(name="booster", aliases=["boost"],
                             description="Set up the boost message.")
    @app_commands.default_permissions(manage_guild=True)
    @commands.guild_only()
    async def booster(self, ctx):
        await open_panel(ctx, feature, "boost")


async def setup(bot):
    await bot.add_cog(Booster(bot))