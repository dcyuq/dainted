import discord

ACCENT = discord.Color(0x2E1A47)

_installed = False


def install():
    """Make ACCENT the default colour for every embed in the process.

    Call this once at startup, before the cogs load. Any embed built without
    an explicit colour comes out themed, whether or not the cog that built it
    remembered to use this module. Passing a colour still wins, which is what
    user built embeds will do later on.
    """
    global _installed
    if _installed:
        return

    original = discord.Embed.__init__

    def patched(self, *, colour=None, color=None, **kwargs):
        if colour is None and color is None:
            colour = ACCENT
        original(self, colour=colour, color=color, **kwargs)

    discord.Embed.__init__ = patched
    _installed = True


def build(description=None, *, title=None, color=None, **kwargs):
    """Every embed the bot sends on its own behalf goes through here.

    The colour is fixed to ACCENT unless a caller passes one explicitly.
    """
    return discord.Embed(
        title=title,
        description=description,
        color=ACCENT if color is None else color,
        **kwargs,
    )


def notice(description, *, title=None, **kwargs):
    """A neutral or successful result. No header unless one is asked for."""
    return build(description, title=title, **kwargs)


def error(description, *, title=None, **kwargs):
    """Something the user needs to fix or be told about.

    House style: no header. The message carries itself, bold the words that
    matter. Pass `title` explicitly on the rare embed that wants one.
    """
    return build(description, title=title, **kwargs)


# Aliases so older call sites keep working.
ok = notice
success = notice
info = notice
note = build


async def send(target, embed=None, *, view=None, content=None, ephemeral=False,
               allowed_mentions=None, **kwargs):
    """Reply to a Context, an Interaction, or any messageable with one call.

    Degrades an embed to plain text if the bot lacks embed permission. Handles
    a Components V2 view (no embed) transparently.
    """
    if allowed_mentions is None:
        allowed_mentions = discord.AllowedMentions.none()

    payload = {"allowed_mentions": allowed_mentions, **kwargs}
    if content is not None:
        payload["content"] = content
    if embed is not None:
        payload["embed"] = embed
    if view is not None:
        payload["view"] = view

    if isinstance(target, discord.Interaction):
        try:
            if target.response.is_done():
                return await target.followup.send(ephemeral=ephemeral, **payload)
            return await target.response.send_message(ephemeral=ephemeral, **payload)
        except discord.HTTPException:
            return None

    try:
        return await target.send(**payload)
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        return None

    if embed is not None:
        fallback = "\n".join(part for part in (embed.title, embed.description) if part)
        if fallback:
            try:
                return await target.send(fallback[:2000], allowed_mentions=allowed_mentions)
            except discord.HTTPException:
                return None
    return None