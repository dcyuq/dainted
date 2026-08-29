"""
Panel engine shared by the welcome and booster cogs.

Both are "send a configurable message when something happens". A guild sets
one message per event (welcome has join/leave, booster has boost) and edits it
through a single button panel - a Components V2 container split by the grey
separator line, with the controls underneath. No scattered subcommands.

A cog builds a Feature (its store, its events, its defaults) and opens a
SetupPanel. Everything else lives here.
"""

from __future__ import annotations

import discord

import embeds

ACCENT = embeds.ACCENT.value

MODES = [
    ("embed_plain", "Embed · no header", "clean embed, no title on top"),
    ("embed_title", "Embed · with header", "embed with a bold title"),
    ("text", "Plain text", "a normal message, no embed"),
]
MODE_LABELS = {key: label for key, label, _ in MODES}

BASE_PLACEHOLDERS = [
    ("{mention}", "pings the member"),
    ("{user}", "their display name"),
    ("{name}", "their username"),
    ("{tag}", "their full tag"),
    ("{id}", "their user id"),
    ("{server}", "the server name"),
    ("{count}", "the member count"),
    ("{avatar}", "their avatar - image fields only"),
    ("{server_icon}", "the server icon - image fields only"),
]


def can_manage(member: discord.Member) -> bool:
    perms = member.guild_permissions
    return perms.administrator or perms.manage_guild


def parse_color(text, fallback):
    if not text:
        return fallback
    text = text.strip().lstrip("#")
    try:
        value = int(text, 16)
    except ValueError:
        return fallback
    return value if 0 <= value <= 0xFFFFFF else fallback


def clean_url(text):
    if not text:
        return None
    value = text.strip()
    low = value.lower()
    if low in {"avatar", "user"}:
        return "avatar"
    if low in {"server", "guild", "icon"}:
        return "server"
    if low.startswith(("http://", "https://")):
        return value
    return None


class _PlainMessage(discord.ui.LayoutView):
    """A no-embed message sent as a Components V2 text block.

    Unlike raw message content, a text display renders markdown links -
    [label](https://...) - so plain-text mode still gets clickable links.
    Mentions inside it still ping when allowed_mentions permits.
    """

    def __init__(self, text):
        super().__init__(timeout=None)
        self.add_item(discord.ui.TextDisplay(text))


class Feature:
    """One configurable message feature, backed by a storage.Store."""

    def __init__(self, *, key, label, store, events, defaults,
                 extra_toggles=(), token_extra=None, extra_placeholders=()):
        self.key = key
        self.label = label
        self.store = store
        self.settings = store.load()
        self.events = list(events)              # [(kind, label, blurb)]
        self._defaults = defaults               # kind -> dict
        self.extra_toggles = list(extra_toggles)  # [(field, label, on, off)]
        self.token_extra = token_extra          # (member, guild) -> dict
        self.placeholders = list(BASE_PLACEHOLDERS) + list(extra_placeholders)
        self.accent = ACCENT

    # ---- persistence --------------------------------------------------
    def save(self):
        self.store.save(self.settings)

    def defaults(self, kind):
        return dict(self._defaults(kind))

    def ensure(self, guild_id):
        guild = self.settings.setdefault(str(guild_id), {})
        for kind, _, _ in self.events:
            base = self.defaults(kind)
            if kind not in guild:
                guild[kind] = base
            else:
                for field, value in base.items():
                    guild[kind].setdefault(field, value)
        return guild

    def event(self, guild_id, kind):
        return self.ensure(guild_id).get(kind)

    def reset(self, guild_id, kind):
        self.ensure(guild_id)[kind] = self.defaults(kind)
        self.save()

    # ---- rendering ----------------------------------------------------
    def tokens(self, member, guild, count):
        data = {
            "mention": member.mention,
            "user": member.display_name,
            "name": member.name,
            "tag": str(member),
            "id": str(member.id),
            "server": guild.name,
            "count": str(count),
        }
        if self.token_extra:
            data.update(self.token_extra(member, guild))
        return data

    def render(self, text, member, guild, count):
        if not text:
            return text
        for key, value in self.tokens(member, guild, count).items():
            text = text.replace("{" + key + "}", str(value))
        return text

    def render_url(self, value, member, guild):
        if not value:
            return None
        if value == "avatar":
            return member.display_avatar.url
        if value == "server":
            return guild.icon.url if guild.icon else None
        return value

    def build_payload(self, event, member, guild, count):
        """Return (content, embed, view). Exactly one delivery shape is set.

        Text mode goes out as a Components V2 text block rather than raw
        content, so masked links like [label](https://...) render instead of
        showing their brackets. Embed descriptions render them already.
        """
        content = self.render(event.get("content", ""), member, guild, count)
        mode = event.get("mode", "embed_plain")

        if mode == "text":
            body = self.render(event.get("message", ""), member, guild, count)
            combined = "\n".join(part for part in (content, body) if part)
            if not combined:
                return None, None, None
            return None, None, _PlainMessage(combined[:3900])

        embed = discord.Embed(
            description=self.render(event.get("message", ""), member, guild, count)[:4096],
            color=event["color"] if event.get("color") is not None else self.accent,
        )
        if mode == "embed_title" and event.get("title"):
            embed.title = self.render(event["title"], member, guild, count)[:256]

        image = self.render_url(event.get("image_url"), member, guild)
        if image:
            embed.set_image(url=image)
        thumb = self.render_url(event.get("thumbnail"), member, guild)
        if thumb:
            embed.set_thumbnail(url=thumb)

        return (content[:2000] if content else None), embed, None

    def problems(self, event, guild):
        out = []
        if not event.get("channel_id"):
            out.append("a channel")
        else:
            channel = guild.get_channel(event["channel_id"])
            if channel is None:
                out.append("a channel that still exists")
            else:
                perms = channel.permissions_for(guild.me)
                if not perms.send_messages:
                    out.append("permission to post there")
                elif event.get("mode") != "text" and not perms.embed_links:
                    out.append("permission to embed there")
        if not (event.get("message") or "").strip() and not (event.get("content") or "").strip():
            out.append("a message")
        return out

    async def dispatch(self, member, guild, kind, count, channel_override=None):
        event = self.event(guild.id, kind)
        if not event or not event.get("enabled"):
            return
        channel = channel_override or guild.get_channel(event.get("channel_id"))
        if channel is None:
            return
        perms = channel.permissions_for(guild.me)
        if not perms.send_messages:
            return
        if event.get("mode") != "text" and not perms.embed_links:
            return

        content, embed, view = self.build_payload(event, member, guild, count)
        if content is None and embed is None and view is None:
            return

        mentions = (
            discord.AllowedMentions(users=True, roles=False, everyone=False)
            if event.get("ping")
            else discord.AllowedMentions.none()
        )
        try:
            await channel.send(content=content, embed=embed, view=view, allowed_mentions=mentions)
        except (discord.Forbidden, discord.HTTPException):
            pass


# ---- selects ----------------------------------------------------------
class EventSelect(discord.ui.Select):
    def __init__(self, panel):
        self.panel = panel
        super().__init__(
            placeholder="Which message?",
            options=[
                discord.SelectOption(
                    label=label, value=kind, description=blurb,
                    default=(kind == panel.kind),
                )
                for kind, label, blurb in panel.feature.events
            ],
        )

    async def callback(self, interaction):
        self.panel.kind = self.values[0]
        await self.panel.refresh(interaction)


class ChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, panel):
        self.panel = panel
        super().__init__(
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            placeholder="Which channel?",
        )

    async def callback(self, interaction):
        self.panel.event["channel_id"] = self.values[0].id
        await self.panel.refresh(interaction)


class ModeSelect(discord.ui.Select):
    def __init__(self, panel):
        self.panel = panel
        current = panel.event.get("mode", "embed_plain")
        super().__init__(
            placeholder="Message style",
            options=[
                discord.SelectOption(
                    label=label, value=key, description=blurb, default=(key == current),
                )
                for key, label, blurb in MODES
            ],
        )

    async def callback(self, interaction):
        self.panel.event["mode"] = self.values[0]
        await self.panel.refresh(interaction)


def _row(*items):
    row = discord.ui.ActionRow()
    for item in items:
        row.add_item(item)
    return row


# ---- button rows ------------------------------------------------------
class EditRow(discord.ui.ActionRow):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel

    @discord.ui.button(label="Message", style=discord.ButtonStyle.primary)
    async def message(self, interaction, button):
        await interaction.response.send_modal(MessageModal(self.panel))

    @discord.ui.button(label="Options", style=discord.ButtonStyle.secondary)
    async def options(self, interaction, button):
        view = OptionsView(self.panel)
        await interaction.response.send_message(
            view.blurb(), view=view, ephemeral=True
        )

    @discord.ui.button(label="Preview", style=discord.ButtonStyle.secondary)
    async def preview(self, interaction, button):
        await self.panel.preview(interaction)


class PowerRow(discord.ui.ActionRow):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        on = bool(panel.event.get("enabled"))
        self.toggle.label = "Turn off" if on else "Turn on"
        self.toggle.style = discord.ButtonStyle.danger if on else discord.ButtonStyle.success

    @discord.ui.button(label="Turn on", style=discord.ButtonStyle.success)
    async def toggle(self, interaction, button):
        event = self.panel.event
        if not event.get("enabled"):
            problems = self.panel.feature.problems(event, self.panel.guild)
            if problems:
                await interaction.response.send_message(
                    embed=embeds.error("still needs: " + ", ".join(problems)),
                    ephemeral=True,
                )
                return
        event["enabled"] = not event.get("enabled")
        await self.panel.refresh(interaction)

    @discord.ui.button(label="Placeholders", style=discord.ButtonStyle.secondary)
    async def placeholders(self, interaction, button):
        await self.panel.show_placeholders(interaction)

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.danger)
    async def reset(self, interaction, button):
        self.panel.feature.reset(self.panel.guild.id, self.panel.kind)
        await self.panel.refresh(interaction)


# ---- modals -----------------------------------------------------------
class MessageModal(discord.ui.Modal):
    def __init__(self, panel):
        super().__init__(title=f"{panel.feature.label} message")
        self.panel = panel
        event = panel.event

        self.f_content = discord.ui.TextInput(
            label="Text above the embed (put {mention} to ping)",
            default=event.get("content", ""),
            style=discord.TextStyle.paragraph,
            max_length=2000,
            required=False,
        )
        self.f_title = discord.ui.TextInput(
            label="Title (only shows in header mode)",
            default=event.get("title", ""),
            max_length=256,
            required=False,
        )
        self.f_message = discord.ui.TextInput(
            label="Message",
            default=event.get("message", ""),
            style=discord.TextStyle.paragraph,
            max_length=4000,
            required=True,
        )
        for item in (self.f_content, self.f_title, self.f_message):
            self.add_item(item)

    async def on_submit(self, interaction):
        event = self.panel.event
        event["content"] = self.f_content.value
        event["title"] = self.f_title.value
        event["message"] = self.f_message.value
        await interaction.response.defer()
        await self.panel.refresh()


class StyleModal(discord.ui.Modal):
    def __init__(self, panel):
        super().__init__(title="Colour & images")
        self.panel = panel
        event = panel.event

        self.f_color = discord.ui.TextInput(
            label="Colour hex",
            default=f"{(event.get('color') if event.get('color') is not None else panel.feature.accent):06X}",
            placeholder="2E1A47",
            max_length=7,
            required=False,
        )
        self.f_image = discord.ui.TextInput(
            label="Large image URL",
            default=event.get("image_url") or "",
            placeholder="https://... or {avatar} or {server_icon}",
            required=False,
        )
        self.f_thumb = discord.ui.TextInput(
            label="Thumbnail",
            default=event.get("thumbnail") or "",
            placeholder="avatar · server · a url · none",
            required=False,
        )
        for item in (self.f_color, self.f_image, self.f_thumb):
            self.add_item(item)

    async def on_submit(self, interaction):
        event = self.panel.event
        event["color"] = parse_color(self.f_color.value, self.panel.feature.accent)
        event["image_url"] = clean_url(self.f_image.value)
        thumb = self.f_thumb.value.strip().lower()
        event["thumbnail"] = None if thumb in {"", "none", "off"} else clean_url(self.f_thumb.value)
        await interaction.response.defer()
        await self.panel.refresh()


# ---- options sub-panel (ephemeral) ------------------------------------
class OptionsView(discord.ui.View):
    def __init__(self, panel):
        super().__init__(timeout=300)
        self.panel = panel

        self.add_item(_ToggleButton("ping", "Ping", "notifies them", "silent"))
        for field, label, on, off in panel.feature.extra_toggles:
            self.add_item(_ToggleButton(field, label, on, off))
        self.add_item(_StyleButton())

    async def interaction_check(self, interaction):
        return interaction.user.id == self.panel.author_id

    def blurb(self):
        event = self.panel.event
        lines = [f"**{self.panel.feature.label} options**", ""]
        lines.append(f"**Ping** — {'notifies them' if event.get('ping') else 'silent'}")
        for field, label, on, off in self.panel.feature.extra_toggles:
            lines.append(f"**{label}** — {on if event.get(field) else off}")
        return "\n".join(lines)


class _ToggleButton(discord.ui.Button):
    def __init__(self, field, label, on_text, off_text):
        super().__init__(label=f"Toggle {label}", style=discord.ButtonStyle.secondary)
        self.field = field

    async def callback(self, interaction):
        view = self.view
        event = view.panel.event
        event[self.field] = not event.get(self.field)
        view.panel.feature.save()
        await view.panel.refresh()
        await interaction.response.edit_message(content=view.blurb(), view=view)


class _StyleButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Colour & images", style=discord.ButtonStyle.primary)

    async def callback(self, interaction):
        if self.view.panel.event.get("mode") == "text":
            await interaction.response.send_message(
                embed=embeds.notice("plain text has no colour or images."), ephemeral=True
            )
            return
        await interaction.response.send_modal(StyleModal(self.view.panel))


# ---- the panel --------------------------------------------------------
class SetupPanel(discord.ui.LayoutView):
    def __init__(self, feature, guild, author_id, kind, message=None):
        super().__init__(timeout=600)
        self.feature = feature
        self.guild = guild
        self.author_id = author_id
        self.kind = kind
        self.message = message
        self.build()

    @property
    def event(self):
        return self.feature.event(self.guild.id, self.kind)

    def _summary(self, event):
        channel = self.guild.get_channel(event["channel_id"]) if event.get("channel_id") else None
        lines = [
            f"**Status** — {'on' if event.get('enabled') else 'off'}",
            f"**Channel** — {channel.mention if channel else 'not set'}",
            f"**Style** — {MODE_LABELS.get(event.get('mode'), '—')}",
            f"**Ping** — {'notifies them' if event.get('ping') else 'silent'}",
        ]
        for field, label, on, off in self.feature.extra_toggles:
            lines.append(f"**{label}** — {on if event.get(field) else off}")
        return "\n".join(lines)

    def _message_block(self, event):
        parts = []
        if event.get("content"):
            parts.append(f"**Above the embed**\n{event['content']}")
        if event.get("mode") == "embed_title" and event.get("title"):
            parts.append(f"**Title**\n{event['title']}")
        body = event.get("message") or "*nothing written yet*"
        if len(body) > 500:
            body = body[:500] + "…"
        parts.append(f"**Message**\n{body}")
        return "\n\n".join(parts)

    def build(self, interactive=True):
        self.clear_items()
        event = self.event
        accent = event["color"] if event.get("color") is not None else self.feature.accent

        container = discord.ui.Container(accent_colour=accent)
        container.add_item(discord.ui.TextDisplay(f"## {self.feature.label} setup"))
        container.add_item(discord.ui.TextDisplay(self._summary(event)))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(self._message_block(event)))

        problems = self.feature.problems(event, self.guild)
        container.add_item(discord.ui.Separator())
        if problems:
            container.add_item(discord.ui.TextDisplay(f"-# still needs: {', '.join(problems)}"))
        else:
            container.add_item(discord.ui.TextDisplay("-# channel · style · message · options — edit below"))
        self.add_item(container)

        if not interactive:
            return

        if len(self.feature.events) > 1:
            self.add_item(_row(EventSelect(self)))
        self.add_item(_row(ChannelSelect(self)))
        self.add_item(_row(ModeSelect(self)))
        self.add_item(EditRow(self))
        self.add_item(PowerRow(self))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=embeds.error("this panel isn't yours - run the command yourself."),
                ephemeral=True,
            )
            return False
        if not can_manage(interaction.user):
            await interaction.response.send_message(
                embed=embeds.error("you need **Manage Server** for that."), ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        self.build(interactive=False)
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def refresh(self, interaction=None):
        self.feature.save()
        self.build()
        if interaction is not None and not interaction.response.is_done():
            await interaction.response.edit_message(view=self)
        elif self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def preview(self, interaction):
        event = self.event
        count = self.guild.member_count or len(self.guild.members)
        content, embed, view = self.feature.build_payload(event, interaction.user, self.guild, count)
        if content is None and embed is None and view is None:
            await interaction.response.send_message(
                embed=embeds.error("nothing to preview yet - write a **message** first."),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            content=content, embed=embed, view=view, ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def show_placeholders(self, interaction):
        body = "\n".join(f"`{tok}` — {desc}" for tok, desc in self.feature.placeholders)
        embed = embeds.build(body, title=f"{self.feature.label} placeholders")
        embed.set_footer(text="a {mention} inside an embed won't notify - put it above the embed.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def open_panel(ctx, feature, kind):
    feature.ensure(ctx.guild.id)
    feature.save()
    panel = SetupPanel(feature, ctx.guild, ctx.author.id, kind)
    message = await ctx.send(view=panel)
    panel.message = message
    return panel