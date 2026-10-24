import json
import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BANLIST_FILE = "banlist.json"
LEADER_ROLE_NAME = "Leader"

# Clan name, Clash of Clans tag, and matching Discord role name.
# Clan tags are stored without changing their case; lookup is case-insensitive.
CLANS = [
    {"name": "Shadow Warriors", "tag": "#CGU9PJJJ", "role": "Shadow Warriors"},
    {"name": "THE SHIELD", "tag": "#UQ0L8VVJ", "role": "The Shield"},
    {"name": "Synergy", "tag": "#2VP9PY2U", "role": "Synergy"},
    {"name": "Raiganj Royals", "tag": "#UVGRR9V2", "role": "Raiganj Royals"},
    {"name": "TiGêrs Øf BD", "tag": "#P9QCJRUQ", "role": "Tigers of BD"},
    {"name": "♣ASSAULTZ♣", "tag": "#2YCCGYP2J", "role": "ASSAULTZ"},
    {"name": "DEFEATED HEARTS", "tag": "#2Y90Q2GJV", "role": "Defeated Hearts"},
    {"name": "Castlevania", "tag": "#L0JQVCYL", "role": "Castlevania"},
    {"name": "#Legendz Rising", "tag": "#RCUPQPP2", "role": "Legendz Rising"},
    {"name": "The Legends", "tag": "#CYPGJUP8", "role": "The Legends"},
    {"name": "Red Archers", "tag": "#P28Y08G8", "role": "Red Archers"},
    {"name": "Tê@M $P®!T™", "tag": "#VUU9JJ2V", "role": "Team spirit"},
    {"name": "kalibo-bliss", "tag": "#8Y82PLV9", "role": "kalibo-bliss"},
    {"name": "Think about WAR", "tag": "#Q99CG88V", "role": "Think about WAR"},
    {"name": "forzuti", "tag": "#R92YP9JY", "role": "Forzuti"},
    {"name": "Eden", "tag": "#28U9PG0GL", "role": "Eden"},
    {"name": "KACHIN", "tag": "#RVJPRLG8", "role": "Kachin"},
    {"name": "PIRATES⚓️⚓️", "tag": "#QQ0YPJ02", "role": "Pirates"},
]

CLAN_BY_TAG = {clan["tag"].casefold(): clan for clan in CLANS}
CLAN_CHOICES = [
    app_commands.Choice(
        name=f'{clan["name"]} ({clan["tag"]})',
        value=clan["tag"],
    )
    for clan in CLANS
]


# ---------------------------------------------------------------------------
# Persistent storage
# ---------------------------------------------------------------------------

def _empty_data():
    return {"community": [], "clans": {}}


def load_ban_data():
    """Read ban data, creating a fresh structure if the file does not exist."""
    try:
        with open(BANLIST_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return _empty_data()

    if not isinstance(data, dict):
        return _empty_data()

    data.setdefault("community", [])
    data.setdefault("clans", {})
    return data


def save_ban_data(data):
    """Write atomically so an interrupted write is less likely to corrupt data."""
    temp_file = f"{BANLIST_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(temp_file, BANLIST_FILE)


def normalize_player_tag(tag: str) -> str:
    return (tag or "").strip().upper().replace(" ", "")


def find_role(guild: discord.Guild, role_name: str):
    wanted = role_name.casefold()
    return discord.utils.find(
        lambda role: role.name.casefold() == wanted,
        guild.roles,
    )


def is_moderator(member: discord.Member) -> bool:
    # Matches the bot's existing /giveaway Manage Messages permission.
    # Administrator is included explicitly as a safeguard.
    perms = member.guild_permissions
    return perms.administrator or perms.manage_messages


def can_manage_clan(member: discord.Member, clan: dict) -> bool:
    if is_moderator(member):
        return True

    leader_role = find_role(member.guild, LEADER_ROLE_NAME)
    clan_role = find_role(member.guild, clan["role"])

    if leader_role is None or clan_role is None:
        return False

    member_role_ids = {role.id for role in member.roles}
    return (
        leader_role.id in member_role_ids
        and clan_role.id in member_role_ids
    )


def make_entry(name: str, tag: str, reason: str, moderator: discord.Member):
    return {
        "name": name.strip(),
        "tag": normalize_player_tag(tag),
        "reason": (reason or "No reason provided").strip(),
        "added_by": str(moderator),
        "added_by_id": moderator.id,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }


def find_entry(entries: list, player_tag: str):
    wanted = normalize_player_tag(player_tag)
    for entry in entries:
        if normalize_player_tag(entry.get("tag", "")) == wanted:
            return entry
    return None


def build_list_embed(title: str, entries: list, scope: str):
    embed = discord.Embed(
        title=title,
        color=discord.Color.red() if entries else discord.Color.green(),
    )

    if not entries:
        embed.description = "No players are currently on this ban list."
        return embed

    # Discord embeds have field and character limits. Keep each page compact.
    lines = []
    for index, entry in enumerate(entries, start=1):
        name = discord.utils.escape_markdown(entry.get("name", "Unknown"))
        tag = discord.utils.escape_markdown(entry.get("tag", "No tag"))
        reason = discord.utils.escape_markdown(
            entry.get("reason", "No reason provided")
        )
        lines.append(f"**{index}. {name}** (`{tag}`)\nReason: {reason}")

    description = "\n\n".join(lines)
    if len(description) > 3900:
        description = description[:3850] + "\n\n… List shortened to fit Discord's embed limit."

    embed.description = description
    embed.set_footer(text=f"{len(entries)} banned player(s) • {scope}")
    return embed


# ---------------------------------------------------------------------------
# Slash-command groups
# ---------------------------------------------------------------------------

class BanList(commands.Cog):
    community = app_commands.Group(
        name="community",
        description="View and manage the community-wide ban list.",
    )
    clan = app_commands.Group(
        name="clan",
        description="View and manage clan-specific ban lists.",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @community.command(name="banlist", description="View the community-wide ban list.")
    async def community_banlist(self, interaction: discord.Interaction):
        data = load_ban_data()
        embed = build_list_embed(
            "🚫 Community Ban List",
            data.get("community", []),
            "Community-wide",
        )
        await interaction.response.send_message(embed=embed)

    @community.command(name="banadd", description="Add a player to the community-wide ban list.")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(
        player_name="The player's in-game name",
        player_tag="The player's Clash of Clans tag",
        reason="Why the player is being banned",
    )
    async def community_banadd(
        self,
        interaction: discord.Interaction,
        player_name: app_commands.Range[str, 1, 100],
        player_tag: app_commands.Range[str, 3, 20],
        reason: app_commands.Range[str, 1, 500] = "No reason provided",
    ):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        if not is_moderator(interaction.user):
            await interaction.response.send_message(
                "You need **Manage Messages** permission to manage the community ban list.",
                ephemeral=True,
            )
            return

        data = load_ban_data()
        entries = data.setdefault("community", [])
        tag = normalize_player_tag(player_tag)

        if find_entry(entries, tag):
            await interaction.response.send_message(
                f"`{tag}` is already on the community ban list.", ephemeral=True
            )
            return

        entry = make_entry(player_name, tag, reason, interaction.user)
        entries.append(entry)
        save_ban_data(data)

        await interaction.response.send_message(
            f"✅ Added **{discord.utils.escape_markdown(player_name)}** (`{tag}`) "
            "to the **community ban list**."
        )

    @community.command(name="banremove", description="Remove a player from the community-wide ban list.")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(player_tag="The player's Clash of Clans tag")
    async def community_banremove(
        self,
        interaction: discord.Interaction,
        player_tag: app_commands.Range[str, 3, 20],
    ):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        if not is_moderator(interaction.user):
            await interaction.response.send_message(
                "You need **Manage Messages** permission to manage the community ban list.",
                ephemeral=True,
            )
            return

        data = load_ban_data()
        entries = data.setdefault("community", [])
        tag = normalize_player_tag(player_tag)
        entry = find_entry(entries, tag)

        if entry is None:
            await interaction.response.send_message(
                f"`{tag}` was not found on the community ban list.", ephemeral=True
            )
            return

        entries.remove(entry)
        save_ban_data(data)
        await interaction.response.send_message(
            f"✅ Removed **{entry.get('name', 'Unknown')}** (`{tag}`) "
            "from the **community ban list**."
        )

    @clan.command(name="banlist", description="View the ban list for a selected clan.")
    @app_commands.describe(clan="Choose which clan's ban list to view")
    @app_commands.choices(clan=CLAN_CHOICES)
    async def clan_banlist(
        self,
        interaction: discord.Interaction,
        clan: app_commands.Choice[str],
    ):
        selected = CLAN_BY_TAG.get(clan.value.casefold())
        if selected is None:
            await interaction.response.send_message(
                "That clan is not configured.", ephemeral=True
            )
            return

        data = load_ban_data()
        clan_entries = data.setdefault("clans", {}).get(selected["tag"], [])
        embed = build_list_embed(
            f"🚫 {selected['name']} Ban List",
            clan_entries,
            f'{selected["name"]} ({selected["tag"]})',
        )
        await interaction.response.send_message(embed=embed)

    @clan.command(name="banadd", description="Add a player to a selected clan's ban list.")
    @app_commands.describe(
        clan="Choose the clan this ban applies to",
        player_name="The player's in-game name",
        player_tag="The player's Clash of Clans tag",
        reason="Why the player is being banned from this clan",
    )
    @app_commands.choices(clan=CLAN_CHOICES)
    async def clan_banadd(
        self,
        interaction: discord.Interaction,
        clan: app_commands.Choice[str],
        player_name: app_commands.Range[str, 1, 100],
        player_tag: app_commands.Range[str, 3, 20],
        reason: app_commands.Range[str, 1, 500] = "No reason provided",
    ):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        selected = CLAN_BY_TAG.get(clan.value.casefold())
        if selected is None:
            await interaction.response.send_message(
                "That clan is not configured.", ephemeral=True
            )
            return

        if not can_manage_clan(interaction.user, selected):
            await interaction.response.send_message(
                f"You need **Manage Messages** permission, or both the **{LEADER_ROLE_NAME}** "
                f"role and **{selected['role']}** role, to edit this clan's ban list.",
                ephemeral=True,
            )
            return

        data = load_ban_data()
        clan_lists = data.setdefault("clans", {})
        entries = clan_lists.setdefault(selected["tag"], [])
        tag = normalize_player_tag(player_tag)

        if find_entry(entries, tag):
            await interaction.response.send_message(
                f"`{tag}` is already on the {selected['name']} ban list.",
                ephemeral=True,
            )
            return

        entries.append(make_entry(player_name, tag, reason, interaction.user))
        save_ban_data(data)
        await interaction.response.send_message(
            f"✅ Added **{discord.utils.escape_markdown(player_name)}** (`{tag}`) "
            f"to **{selected['name']}'s** ban list."
        )

    @clan.command(name="banremove", description="Remove a player from a selected clan's ban list.")
    @app_commands.describe(
        clan="Choose the clan whose ban list should be edited",
        player_tag="The player's Clash of Clans tag",
    )
    @app_commands.choices(clan=CLAN_CHOICES)
    async def clan_banremove(
        self,
        interaction: discord.Interaction,
        clan: app_commands.Choice[str],
        player_tag: app_commands.Range[str, 3, 20],
    ):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        selected = CLAN_BY_TAG.get(clan.value.casefold())
        if selected is None:
            await interaction.response.send_message(
                "That clan is not configured.", ephemeral=True
            )
            return

        if not can_manage_clan(interaction.user, selected):
            await interaction.response.send_message(
                f"You need **Manage Messages** permission, or both the **{LEADER_ROLE_NAME}** "
                f"role and **{selected['role']}** role, to edit this clan's ban list.",
                ephemeral=True,
            )
            return

        data = load_ban_data()
        clan_lists = data.setdefault("clans", {})
        entries = clan_lists.setdefault(selected["tag"], [])
        tag = normalize_player_tag(player_tag)
        entry = find_entry(entries, tag)

        if entry is None:
            await interaction.response.send_message(
                f"`{tag}` was not found on the {selected['name']} ban list.",
                ephemeral=True,
            )
            return

        entries.remove(entry)
        save_ban_data(data)
        await interaction.response.send_message(
            f"✅ Removed **{entry.get('name', 'Unknown')}** (`{tag}`) "
            f"from **{selected['name']}'s** ban list."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(BanList(bot))
