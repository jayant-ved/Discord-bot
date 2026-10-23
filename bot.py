import os
import certifi

# Force Python and aiohttp to use the installed certificates
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()

import random
import json
import asyncio
import aiohttp
import re
import unicodedata
import discord
import requests
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# Tokens
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
COC_API_TOKEN = os.getenv("COC_API_TOKEN")

# Persistent giveaway state used by /reroll
GIVEAWAY_STATE_FILE = "giveaway_state.json"

def save_giveaway_state(state):
    """Save the latest giveaway snapshot so rerolls survive bot restarts."""
    with open(GIVEAWAY_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def load_giveaway_state():
    """Load the latest giveaway snapshot, if one exists."""
    try:
        with open(GIVEAWAY_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

# The 18 clan tags included in this giveaway
CLAN_TAGS = [
    "#RCUPQPP2", "#L0JQVCYL", "#2Y90Q2GJV", "#28U9PG0GL",
    "#RVJPRLG8", "#QQ0YPJ02", "#UVGRR9V2", "#P28Y08G8",
    "#2VP9PY2U", "#UQ0L8VVJ", "#P9QCJRUQ", "#R92YP9JY",
    "#8Y82PLV9", "#2YCCGYP2J", "#RQ2RQ2U9", "#CGU9PJJJ",
    "#Q99CG88V", "#VUU9JJ2V"
]

# 1. Helpers for parsing and matching ClashPerk player names
def normalize_player_name(name: str) -> str:
    """Normalize names so capitalization, extra spaces, and invisible format chars don't break matching."""
    name = unicodedata.normalize("NFKC", name or "")
    name = "".join(ch for ch in name if unicodedata.category(ch) != "Cf")
    return " ".join(name.split()).casefold()

def parse_clashperk_embed(embed_text: str):
    """Parse ranked player rows from ClashPerk's text/table output.

    Expected rows resemble: 1 17m 6s 292 Wardriver...
    The source text may come from an embed description or one/more embed fields.
    """
    ranked_players = []
    for raw_line in (embed_text or "").splitlines():
        print("[DEBUG RAW ROW]", repr(raw_line))
        #line = raw_line.strip().strip("`| ")
        # Remove invisible formatting characters and Markdown table backticks.
        line = "".join(
            ch for ch in raw_line
            if unicodedata.category(ch) != "Cf"
        )
        line = line.replace("`", "").strip().strip("| ")
        if not line:
            continue

        # Ignore headings and table separators.
        if re.search(r"\b(rank|last[- ]?on|score|name|most active members)\b", line, re.I):
            continue
        if re.fullmatch(r"[-+|:=` ]+", line):
            continue

        # Rank + activity duration (e.g. 17m 6s, 2h 33m, 1d 2h) + score + name.
        # Durations may include spaces, so capture up to the score before the name.
        match = re.match(
            r"^\s*(\d{1,3})\s+"
            r"(?:(?:\d+\s*[dhms])\s*)+"
            r"(\d{1,4})\s+(.+?)\s*$",
            line,
            re.I,
        )
        if match:
            rank = int(match.group(1))
            name = match.group(3).strip().strip("`| ")
            if 1 <= rank <= 999 and name:
                ranked_players.append((rank, name))
                continue

        # Fallback for rows where the activity column is rendered differently,
        # but rank and score are still separate numeric columns.
        fallback = re.match(r"^\s*(\d{1,3})\s+.+?\s+(\d{1,4})\s+(.+?)\s*$", line)
        if fallback:
            rank = int(fallback.group(1))
            name = fallback.group(3).strip().strip("`| ")
            if 1 <= rank <= 999 and name:
                ranked_players.append((rank, name))

    # Keep the first occurrence of each rank; the list is already rank ordered.
    unique = {}
    for rank, name in ranked_players:
        unique.setdefault(rank, name)
    return sorted(unique.items())

# 2. Async function to fetch clan members from CoC API, with retries for temporary failures.
async def fetch_clan_members(session, clan_tag, headers, semaphore):
    formatted_tag = clan_tag.strip().replace('#', '%23')
    url = f"https://api.clashofclans.com/v1/clans/{formatted_tag}/members"
    max_attempts = 4  # First attempt + up to 3 retries
    retryable_statuses = {429, 500, 502, 503, 504}

    for attempt in range(1, max_attempts + 1):
        try:
            # Limit simultaneous requests so all 18 clans do not hit the API at once.
            async with semaphore:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        members = [
                            {
                                "name": member["name"],
                                "tag": member["tag"],
                                "donations": member.get("donations", 0),
                            }
                            for member in data.get("items", [])
                        ]
                        print(f"[CoC] Fetched {len(members)} members from clan {clan_tag}.")
                        return members

                    status = response.status
                    retry_after_header = response.headers.get("Retry-After")
                    if status not in retryable_statuses:
                        print(f"[CoC] Permanent failure for clan {clan_tag}: HTTP {status}; not retrying.")
                        return []

                    if attempt == max_attempts:
                        print(f"[CoC] Giving up on clan {clan_tag} after {attempt} attempts: HTTP {status}.")
                        return []

                    try:
                        retry_after = float(retry_after_header) if retry_after_header else None
                    except (TypeError, ValueError):
                        retry_after = None

                    delay = min(retry_after, 30) if retry_after is not None else 2 ** (attempt - 1)
                    print(
                        f"[CoC] Temporary HTTP {status} for clan {clan_tag}; "
                        f"retry {attempt + 1}/{max_attempts} in {delay:.1f}s."
                    )

        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt == max_attempts:
                print(f"[CoC] Giving up on clan {clan_tag} after {attempt} attempts: {type(exc).__name__}: {exc}")
                return []

            delay = 2 ** (attempt - 1)
            print(
                f"[CoC] Temporary network/timeout error for clan {clan_tag} "
                f"({type(exc).__name__}); retry {attempt + 1}/{max_attempts} in {delay}s."
            )

        await asyncio.sleep(delay)

    return []

# 3. Initialize Bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 4. Sync Slash Commands on Startup
@bot.event
async def on_ready():
    print("MY DISCLOUD IP IS:", requests.get('https://api.ipify.org').text)
    await bot.tree.sync()
    print(f"✅ Bot is online as {bot.user}")

# 5. Define the /giveaway Command
@bot.tree.command(name="giveaway", description="Fetch all 18 clans and apply ClashPerk activity bonuses!")
# UPDATED: Added min_donations description and changed permissions to allow mods
@app_commands.describe(
    channel="The channel where ClashPerk posted the activity list", 
    min_donations="Minimum donation requirement for a member to enter"
)
@app_commands.default_permissions(manage_messages=True)
async def giveaway(interaction: discord.Interaction, channel: discord.TextChannel, min_donations: int = 0):
    # Defer response since API fetching takes a brief moment
    await interaction.response.defer()

    if not COC_API_TOKEN:
        await interaction.followup.send("❌ Error: `COC_API_TOKEN` missing in `.env`.", ephemeral=True)
        return

    # 1. Fetch ClashPerk's ranked list from selected channel.
    # ClashPerk may place its table in the embed description OR embed fields.
    ranked_players = []
    async for message in channel.history(limit=1):
        for embed in message.embeds:
            title = embed.title or ""
            description = embed.description or ""
            field_text = "\n".join(
                f"{field.name}\n{field.value}" for field in embed.fields
            )
            combined_text = "\n".join(part for part in [description, field_text] if part)
            looks_like_activity = (
                "most active members" in title.casefold()
                or "last-on" in combined_text.casefold()
                or "last on" in combined_text.casefold()
            )
            if not looks_like_activity:
                continue

            print(
                f"[ClashPerk] Found candidate embed: title={title!r}, "
                f"description_chars={len(description)}, fields={len(embed.fields)}"
            )
            ranked_players = parse_clashperk_embed(combined_text)
            if ranked_players:
                break
        if ranked_players:
            break

    print(f"[ClashPerk] Parsed {len(ranked_players)} ranked players.")
    if not ranked_players:
        print("[ClashPerk] WARNING: No ranked players parsed from the latest 50 messages. Check the candidate embed text layout.")

    # 2. Fetch members across the 18 configured clans with limited concurrency and retries
    headers = {
        "Authorization": f"Bearer {COC_API_TOKEN}",
        "Accept": "application/json"
    }

    timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
    connector = aiohttp.TCPConnector(limit=5)
    semaphore = asyncio.Semaphore(3)  # At most 3 clan requests in flight at once.

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        tasks = [fetch_clan_members(session, tag, headers, semaphore) for tag in CLAN_TAGS]
        results = await asyncio.gather(*tasks)

    # Summarize coverage so failed clan fetches are visible in the host logs.
    failed_clans = [tag for tag, members in zip(CLAN_TAGS, results) if not members]
    print(
        f"[CoC] Fetch summary: {len(CLAN_TAGS) - len(failed_clans)}/{len(CLAN_TAGS)} clans returned members; "
        f"{sum(len(clan) for clan in results)} total members fetched."
    )
    if failed_clans:
        print("[CoC] Clans still not fetched after retries: " + ", ".join(failed_clans))

    all_members = [member for clan in results for member in clan]

    if not all_members:
        await interaction.followup.send("❌ Error: Could not fetch members from Clash of Clans API.")
        return

    # 3. Map normalized player names to their actual ClashPerk rank.
    # Keep the rank parsed from the embed instead of assuming every row was parsed.
    rank_map = {normalize_player_name(name): rank for rank, name in ranked_players}

    
    # DIAGNOSTIC: Compare ClashPerk names against CoC API names.
    coc_name_map = {}

    for member in all_members:
        key = normalize_player_name(member["name"])
        coc_name_map.setdefault(key, []).append(member["name"])

    print("\n========== CLASHPERK NAME MATCH REPORT ==========")
    print(f"ClashPerk rows parsed: {len(ranked_players)}")
    print(f"CoC members fetched: {len(all_members)}")

    matched = 0
    unmatched_clashperk = []

    for rank, cp_name in ranked_players:
        key = normalize_player_name(cp_name)

        if key in coc_name_map:
            matched += 1
            coc_names = ", ".join(repr(n) for n in coc_name_map[key])

            if 1 <= rank <= 3:
                tickets = 5
            elif rank <= 10:
                tickets = 4
            elif rank <= 50:
                tickets = 3
            elif rank <= 99:
                tickets = 2
            else:
                tickets = 1

            print(
                f"[MATCH] ClashPerk={cp_name!r} | "
                f"CoC={coc_names} | Rank=#{rank} | "
                f"Expected tickets={tickets}"
            )
        else:
            unmatched_clashperk.append((rank, cp_name))
            print(
                f"[NO MATCH] ClashPerk={cp_name!r} | "
                f"Normalized={key!r} | Rank=#{rank}"
            )

    unmatched_coc = [
        member["name"]
        for member in all_members
        if normalize_player_name(member["name"]) not in rank_map
    ]

    print("\n--------------- SUMMARY ---------------")
    print(f"Matched ClashPerk rows: {matched}")
    print(f"Unmatched ClashPerk rows: {len(unmatched_clashperk)}")
    print(f"CoC members without activity rank: {len(unmatched_coc)}")

    print("\nFirst 30 unmatched CoC names:")
    for name in unmatched_coc[:30]:
        print(f"  [COC ONLY] {name!r}")

    print("========================================\n")

    participants = []
    weights = []

    # 4. Assign base entry + bonus entries
    # UPDATED: Logic to unpack member data and filter out low donators
    for member_data in all_members:
        name = member_data["name"]
        tag = member_data["tag"]
        donations = member_data["donations"]
        
        if donations < min_donations:
            continue
            
        rank = rank_map.get(normalize_player_name(name))

        if rank and 1 <= rank <= 3:
            weight = 5  # 1 base + 4 bonus
        elif rank and 4 <= rank <= 10:
            weight = 4  # 1 base + 3 bonus
        elif rank and 11 <= rank <= 50:
            weight = 3  # 1 base + 2 bonus
        elif rank and 51 <= rank <= 99:
            weight = 2  # 1 base + 1 bonus
        else:
            weight = 1  # 1 base entry

        participants.append((name, tag))
        weights.append(weight)

        # Diagnostic only: check the host console if a ranked player gets base entry.
        if rank is not None:
            print(f"[Rank match] {name!r} -> rank #{rank}, weight={weight}")

    matched_count = sum(1 for (name, _tag) in participants if normalize_player_name(name) in rank_map)
    print(f"[ClashPerk] Matched {matched_count}/{len(participants)} eligible participants to activity ranks.")

    # ADDED: Failsafe just in case nobody met the donation requirement
    if not participants:
        await interaction.followup.send(f"❌ No eligible members met the criteria of **{min_donations}+ donations**.")
        return

    # Save the exact participant pool and weights used for this giveaway.
    # Rerolls use this snapshot instead of refetching live CoC data.
    giveaway_state = {
        "participants": [
            {
                "name": name,
                "tag": tag,
                "weight": weight,
                "rank": rank_map.get(normalize_player_name(name))
            }
            for (name, tag), weight in zip(participants, weights)
        ],
        "min_donations": min_donations,
        "clans_scanned": len(CLAN_TAGS),
        "previous_winners": []
    }

    winner_name, winner_tag = random.choices(participants, weights=weights, k=1)[0]
    winner_rank = rank_map.get(normalize_player_name(winner_name))

    giveaway_state["previous_winners"].append({"name": winner_name, "tag": winner_tag})
    save_giveaway_state(giveaway_state)

    # 5. Build and send result embed
    embed = discord.Embed(
        title="🎉 Giveaway Result 🎉",
        description=f"Congratulations to **{winner_name} ({winner_tag})** for winning the giveaway!",
        color=discord.Color.gold()
    )
    embed.add_field(name="Clans Scanned", value=str(len(CLAN_TAGS)), inline=True)
    embed.add_field(name="Total Pool", value=f"{len(participants)} players", inline=True)
    
    # ADDED: Optional field to show what the required minimum was
    if min_donations >= 0:
        embed.add_field(name="Donation Requirement", value=f"{min_donations}+ donations", inline=True)
        
    embed.add_field(
        name="Activity Tier",
        value=f"Rank #{winner_rank} (Bonus applied)" if winner_rank else "Base Entry (1 ticket)",
        inline=False
    )
    embed.set_footer(text="Live clan data & ClashPerk activity weights calculated.")

    await interaction.followup.send(embed=embed)

# 6. Reroll the latest giveaway
@bot.tree.command(name="reroll", description="Reroll the latest giveaway without refetching CoC data!")
@app_commands.default_permissions(manage_messages=True)
async def reroll(interaction: discord.Interaction):
    state = load_giveaway_state()

    if not state or not state.get("participants"):
        await interaction.response.send_message(
            "❌ No saved giveaway found. Run `/giveaway` first.", ephemeral=True
        )
        return

    previous_tags = {winner.get("tag") for winner in state.get("previous_winners", [])}
    remaining = [p for p in state["participants"] if p.get("tag") not in previous_tags]

    if not remaining:
        await interaction.response.send_message(
            "❌ No eligible players remain for another reroll. Everyone in the saved giveaway pool has already won.",
            ephemeral=True
        )
        return

    winner = random.choices(remaining, weights=[p.get("weight", 1) for p in remaining], k=1)[0]
    state.setdefault("previous_winners", []).append({"name": winner["name"], "tag": winner["tag"]})
    save_giveaway_state(state)

    winner_rank = winner.get("rank")
    embed = discord.Embed(
        title="🔄 Giveaway Reroll 🎉",
        description=f"Congratulations to **{winner['name']} ({winner['tag']})** for winning the reroll!",
        color=discord.Color.gold()
    )
    embed.add_field(name="Total Pool", value=f"{len(state['participants'])} players", inline=True)
    embed.add_field(name="Donation Requirement", value=f"{state.get('min_donations', 0)}+ donations", inline=True)
    embed.add_field(
        name="Activity Tier",
        value=f"Rank #{winner_rank} (Bonus applied)" if winner_rank else "Base Entry (1 ticket)",
        inline=False
    )
    embed.set_footer(text="Rerolled from the saved giveaway pool.")
    await interaction.response.send_message(embed=embed)

# 7. Run the bot
bot.run(DISCORD_TOKEN)