"""
Settings "panels": a titled card split into sections by the thin grey
separator line (Discord Components V2). Falls back to a plain embed when the
running discord.py is too old to know about layout components, so the same
call works everywhere.

    view, embed = panels.card("Welcome", ["line one", "line two"], accent=embeds.OK)
    await embeds.send(ctx, embed, view=view)
"""

from __future__ import annotations

import discord

# Components V2 (Container / Separator / TextDisplay / LayoutView) landed in
# discord.py 2.6. Detect it and degrade gracefully.
SUPPORTED = (
    hasattr(discord.ui, "LayoutView")
    and hasattr(discord.ui, "Container")
    and hasattr(discord.ui, "Separator")
    and hasattr(discord.ui, "TextDisplay")
)


if SUPPORTED:

    class _Card(discord.ui.LayoutView):
        def __init__(self, title: str, blocks: list[str], accent: int | None):
            super().__init__(timeout=None)
            container = discord.ui.Container(accent_colour=accent)
            container.add_item(discord.ui.TextDisplay(f"### {title}"))
            for block in blocks:
                container.add_item(discord.ui.Separator())
                container.add_item(discord.ui.TextDisplay(block))
            self.add_item(container)


def card(title: str, blocks: list[str], accent: int | None = None):
    """
    Returns (view, embed). Exactly one is non-None: a Components V2 view where
    available, otherwise an embed carrying the same content.
    """
    blocks = [b for b in blocks if b]

    if SUPPORTED:
        return _Card(title, blocks, accent), None

    embed = discord.Embed(
        title=title,
        color=accent,
        description="\n\n".join(blocks),
    )
    return None, embed