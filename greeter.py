from __future__ import annotations
import re
import discord
import embeds

try:
    import emoji as _emoji_lib
except ImportError:
    _emoji_lib = None

ACCENT = embeds.ACCENT.value
_EMOJI_NAME = re.compile(r":([a-zA-Z0-9_]{2,32}):")

def resolve_emojis(text, guild):
    if not text or ":" not in text:
        return text

    if guild is not None:
        lookup = {e.name.lower(): str(e) for e in getattr(guild, "emojis", [])}
        if lookup:
            text = _EMOJI_NAME.sub(
                lambda m: lookup.get(m.group(1).lower(), m.group(0)), text
            )

    if _emoji_lib is not None:
        try:
            text = _emoji_lib.emojize(text, language="alias")
        except TypeError:
            try:
                text = _emoji_lib.emojize(text, use_aliases=True)
            except Exception:
                pass
        except Exception:
            pass

    return text

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

def _asset_label(value):
    if not value:
        return "none"
    if value == "avatar":
        return "their avatar"
    if value == "server":
        return "server icon"
    return "custom url"

class _PlainMessage(discord.ui.LayoutView):
    def __init__(self, text):
        super().__init__(timeout=None)
        self.add_item(discord.ui.TextDisplay(text))

class Feature:
    def __init__(self, *, key, label, store, events, defaults, extra_toggles=(), token_extra=None, extra_placeholders=()):
        self.key = key
        self.label = label
        self.store = store
        self.settings = store.load()
        self.events = list(events)
        self._defaults = defaults
        self.extra_toggles = list(extra_toggles)
        self.token_extra = token_extra
        self.placeholders = list(BASE_PLACEHOLDERS) + list(extra_placeholders)
        self.accent = ACCENT

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
        return resolve_emojis(text, guild)

    def render_url(self, value, member, guild):
        if not value:
            return None
        if value == "avatar":
            return member.display_avatar.url
        if value == "server":
            return guild.icon.url if guild.icon else None
        return value

    def build_payload(self, event, member, guild, count):
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

class EditRow(discord.ui.ActionRow):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel

    @discord.ui.button(label="Message", style=discord.ButtonStyle.primary)
    async def message(self, interaction, button):
        await interaction.response.send_modal(MessageModal(self.panel))

    @discord.ui.button(label="Images", style=discord.ButtonStyle.secondary)
    async def images(self, interaction, button):
        if self.panel.event.get("mode") == "text":
            await interaction.response.send_message(
                embed=embeds.notice("plain text has no thumbnail or image - switch to an embed style first."),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(StyleModal(self.panel))

    @discord.ui.button(label="Options", style=discord.ButtonStyle.secondary)
    async def options(self, interaction, button):
        view = OptionsView(self.panel)
        await interaction.response.send_message(view.blurb(), view=view, ephemeral=True)

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
        super().__init__(title="Thumbnail, image & colour")
        self.panel = panel
        event = panel.event

        self.f_thumb = discord.ui.TextInput(
            label="Thumbnail (small, top-right corner)",
            default=event.get("thumbnail") or "",
            placeholder="avatar · server · an image url · none",
            required=False,
        )
        self.f_image = discord.ui.TextInput(
            label="Image (large, across the bottom)",
            default=event.get("image_url") or "",
            placeholder="an image url · {avatar} · {server_icon} · none",
            required=False,
        )
        self.f_color = discord.ui.TextInput(
            label="Colour hex (blank = default)",
            default="" if event.get("color") is None else f"{event['color']:06X}",
            placeholder="2E1A47",
            max_length=7,
            required=False,
        )
        for item in (self.f_thumb, self.f_image, self.f_color):
            self.add_item(item)

    async def on_submit(self, interaction):
        event = self.panel.event
        thumb = self.f_thumb.value.strip()
        event["thumbnail"] = None if thumb.lower() in {"", "none", "off"} else clean_url(thumb)
        image = self.f_image.value.strip()
        event["image_url"] = None if image.lower() in {"", "none", "off"} else clean_url(image)
        raw = self.f_color.value.strip()
        if raw.lower() in {"", "none", "default"}:
            event["color"] = None
        else:
            current = event["color"] if event.get("color") is not None else self.panel.feature.accent
            event["color"] = parse_color(raw, current)
        await interaction.response.defer()
        await self.panel.refresh()

class OptionsView(discord.ui.View):
    def __init__(self, panel):
        super().__init__(timeout=300)
        self.panel = panel
        self.add_item(_ToggleButton("ping", "Ping", "notifies them", "silent"))
        for field, label, on, off in panel.feature.extra_toggles:
            self.add_item(_ToggleButton(field, label, on, off))

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
            f"**Thumbnail** — {_asset_label(event.get('thumbnail'))}",
            f"**Image** — {_asset_label(event.get('image_url'))}",
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