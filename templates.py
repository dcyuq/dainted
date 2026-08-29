"""
Placeholder rendering shared by welcome / booster / vanity messages.

People write formats with {tokens}. Unknown tokens are left untouched, so a
stray brace never blows up a message.

    render("welcome {user} to {server} - now {members} strong", member=member)
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"\{([a-zA-Z0-9_.]+)\}")


def build_tokens(member=None, guild=None, **extra) -> dict[str, str]:
    tokens: dict[str, str] = {}

    if member is not None:
        tokens.update(
            {
                "user": member.mention,
                "user.mention": member.mention,
                "user.name": member.name,
                "user.display": member.display_name,
                "user.id": str(member.id),
                "user.tag": str(member),
                "user.avatar": member.display_avatar.url,
            }
        )

    guild = guild or getattr(member, "guild", None)
    if guild is not None:
        boosts = guild.premium_subscription_count or 0
        tokens.update(
            {
                "server": guild.name,
                "server.name": guild.name,
                "server.id": str(guild.id),
                "members": str(guild.member_count),
                "member_count": str(guild.member_count),
                "boosts": str(boosts),
                "boost_count": str(boosts),
                "boosters": str(len(guild.premium_subscribers)),
                "level": str(guild.premium_tier),
                "tier": str(guild.premium_tier),
            }
        )
        if guild.icon:
            tokens["server.icon"] = guild.icon.url

    for key, value in extra.items():
        tokens[key] = str(value)

    return tokens


def render(template: str | None, **kwargs) -> str | None:
    if not template:
        return template
    tokens = build_tokens(**kwargs)
    return _TOKEN.sub(lambda m: tokens.get(m.group(1), m.group(0)), template)


# Shown by the `variables` subcommand.
VARIABLES = [
    ("{user}", "mentions the member"),
    ("{user.name}", "their username"),
    ("{user.display}", "their nickname / display name"),
    ("{user.id}", "their id"),
    ("{user.tag}", "their full tag"),
    ("{user.avatar}", "their avatar url"),
    ("{server}", "the server name"),
    ("{server.id}", "the server id"),
    ("{members}", "current member count"),
    ("{boosts}", "current boost count"),
    ("{boosters}", "number of people boosting"),
    ("{level}", "boost tier / level"),
]