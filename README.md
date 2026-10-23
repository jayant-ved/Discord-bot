# Clash of Clans Giveaway Discord Bot

An automated, asynchronous Discord bot built with Python that manages multi-clan giveaways across 20+ Clash of Clans clans using live API data and weighted probability draws.

---

## Problem & Motivation

Managing giveaways for large gaming communities spanning multiple in-game clans and communication platforms (Discord, WhatsApp, in-game only) presents major challenges:
* **Fragmented Platforms:** Traditional Discord giveaway bots require users to react in a server, leaving out in-game and WhatsApp members.
* **Manual Bottlenecks:** Tracking over 800+ players across 20 clans via spreadsheets is slow and error-prone.
* **Fair Reward Distribution:** Giving top contributors bonus entries based on activity was previously done manually.

This bot automates the entire pipeline: fetching live player rosters directly from Supercell's API, parsing activity leaderboards, and executing mathematically fair, weighted draws.

---

## Features

* ** Concurrent Multi-Clan Ingestion:** Uses `aiohttp` and `asyncio.gather` to query the official Clash of Clans API across 20+ clan tags simultaneously in ~1–2 seconds.
* ** Automated Weighted Probabilities:** Implements `random.choices()` to assign custom weights based on activity rankings:
  * **Ranks 1–3:** 5 entries (1 base + 4 bonus)
  * **Ranks 4–10:** 4 entries (1 base + 3 bonus)
  * **Ranks 11–50:** 3 entries (1 base + 2 bonus)
  * **Ranks 51–99:** 2 entries (1 base + 1 bonus)
  * **All other members:** 1 base entry
* ** Native Discord Slash Commands:** Admin-restricted `/giveaway` command with rich embed announcements.
* ** 24/7 Cloud Ready:** Designed for continuous deployment with environment variable isolation.

---

## Tech Stack

* **Language:** Python 3.10+
* **Discord Framework:** `discord.py`
* **HTTP / Networking:** `aiohttp`, `certifi`, `asyncio`
* **API:** Official Clash of Clans REST API
* **Configuration:** `python-dotenv`

---