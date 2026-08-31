import logging

import discord
from discord import app_commands
from discord.ext import commands

import embeds
from prefixes import prefix_of

log = logging.getLogger(__name__)

ACCENT = embeds.ACCENT.value


def _describe(command) -> str:
    """The one-line blurb for a command: its slash description, then docstring."""
    text = (command.description or "").strip()
    if not text:
        text = (command.short_doc or "").strip()
    return text


def _visible(bot):
    """Non-hidden top-level commands, grouped by cog name and sorted."""
    groups: dict[str, list] = {}
    for command in bot.commands:
        if command.hidden:
            continue
        cog = command.cog.qualified_name if command.cog else "Other"
        groups.setdefault(cog, []).append(command)
    for cmds in groups.values():
        cmds.sort(key=lambda c: c.name)
    return groups


class HelpView(discord.ui.LayoutView):
    """A single Components V2 card listing every command, grouped by feature."""

    def __init__(self, bot, prefix):
        super().__init__(timeout=None)
        name = bot.user.name if bot.user else "commands"
        groups = _visible(bot)

        container = discord.ui.Container(accent_colour=ACCENT)
        container.add_item(discord.ui.TextDisplay(f"## {name} · commands"))

        for cog in sorted(groups):
            lines = []
            for command in groups[cog]:
                desc = _describe(command)
                lines.append(
                    f"`{prefix}{command.name}` — {desc}" if desc else f"`{prefix}{command.name}`"
                )
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(f"**{cog}**\n" + "\n".join(lines)))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(
            f"-# `{prefix}help <command>` for details · slash versions work too · "
            "mention me to change my prefix"
        ))
        self.add_item(container)


class Help(commands.Cog):
    """Show what the bot can do."""

    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="help", description="List commands, or explain one.")
    @app_commands.describe(command="A command to explain in detail.")
    @commands.guild_only()
    async def help(self, ctx, *, command: str = None):
        prefix = prefix_of(ctx)

        if command is None:
            await ctx.send(view=HelpView(self.bot, prefix))
            return

        target = self.bot.get_command(command.strip().lstrip("/").lower())
        if target is None or target.hidden:
            await embeds.send(
                ctx, embeds.error(f"no command called **{command}**. try `{prefix}help`.")
            )
            return

        desc = _describe(target) or "no description."
        aliases = ", ".join(f"`{a}`" for a in target.aliases) if target.aliases else "none"
        signature = f" {target.signature}" if target.signature else ""
        body = (
            f"{desc}\n\n"
            f"**Aliases** — {aliases}\n"
            f"**Usage** — `{prefix}{target.qualified_name}{signature}`"
        )
        await embeds.send(ctx, embeds.build(body, title=f"{prefix}{target.qualified_name}"))


async def setup(bot):
    bot.help_command = None 
    await bot.add_cog(Help(bot))