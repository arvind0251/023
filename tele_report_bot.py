#!/usr/bin/env python3
"""
Tele Report Bot – all‑in‑one CLI + Aiogram interface
---------------------------------------------------
• **CLI** modes:
    - `python tele_report_bot.py login`   → interactive user‑account login (Telethon)
    - `python tele_report_bot.py status`  → show login status of saved sessions
• **Bot** mode (default):
    - `python tele_report_bot.py`         → starts Aiogram bot

Admin‑only bot commands
-----------------------
/start                → help text
/report <user> <1‑5>  → mass‑report with every saved session
/accounts             → how many user sessions configured
/status               → per‑phone login status check
/addadmin <id|@user>  → add more admins at runtime (username or numeric id)

Notes
-----
* User sessions live in `sessions/`.
* Account registry in `accounts.json`.
* Initial admins loaded from env var `ADMIN_IDS` (comma‑separated integers).
* Runtime /addadmin updates only live until process restarts unless you also update env/db.
* Requires: aiogram, telethon, python‑dotenv
"""

import os
import json
import asyncio
import random
import logging
from typing import Set

from dotenv import load_dotenv
from telethon.sync import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.functions.messages import ReportRequest
from telethon.tl.types import (
    InputReportReasonSpam,
    InputReportReasonPornography,
    InputReportReasonFake,
    InputReportReasonIllegalDrugs,
    InputReportReasonViolence,
)

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ParseMode

SESSIONS_DIR = "sessions"
ACCOUNTS_FILE = "accounts.json"

# ---------------------------------------------------------------------------
# 0. Load environment
# ---------------------------------------------------------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN env variable not set")

# Parse ADMIN_IDS env → Set[int]
_admin_env = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: Set[int] = {int(x) for x in _admin_env.replace(" ", "").split(",") if x.isdigit()}

# ---------------------------------------------------------------------------
# 1. Initialise Aiogram bot / dispatcher
# ---------------------------------------------------------------------------
bot = Bot(BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

# ---------------------------------------------------------------------------
# 2. Telethon helpers
# ---------------------------------------------------------------------------

def load_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    return json.load(open(ACCOUNTS_FILE))

async def iter_clients():
    """Yield (client, phone) pairs for every authorised Telethon session."""
    for acc in load_accounts():
        client = TelegramClient(f"{SESSIONS_DIR}/{acc['phone']}", acc["api_id"], acc["api_hash"])
        await client.connect()
        if await client.is_user_authorized():
            yield client, acc["phone"]
        else:
            await client.disconnect()

# ---------------------------------------------------------------------------
# 3. Core async job – mass report
# ---------------------------------------------------------------------------
async def report_username(username: str, reason, label: str, per_account: int = 25):
    async for client, phone in iter_clients():
        try:
            user = await client.get_input_entity(username)
            note = f"Bot‑triggered report ({label})"
            for _ in range(per_account):
                try:
                    await client(ReportRequest(user, [], reason, note))
                    await asyncio.sleep(random.uniform(2.5, 6))
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 1)
        except Exception as e:
            logging.warning("Report error via %s: %s", phone, e)
        finally:
            await client.disconnect()

# ---------------------------------------------------------------------------
# 4. Bot helpers / decorators
# ---------------------------------------------------------------------------

def admin_only(handler):
    async def wrapper(message: types.Message, *args, **kwargs):
        if message.from_user.id not in ADMIN_IDS:
            return await message.reply("⛔️ Only admins allowed.")
        return await handler(message, *args, **kwargs)
    return wrapper

# ---------------------------------------------------------------------------
# 5. Bot command handlers
# ---------------------------------------------------------------------------
@dp.message(Command("start"))
async def start_cmd(msg: types.Message):
    await msg.reply(
        "👋 <b>Tele‑Report Bot ready.</b>\n"
        "<b>Admin commands</b>:\n"
        "• /report &lt;user&gt; &lt;1‑5&gt; – mass‑report\n"
        "• /addadmin &lt;id|@user&gt; – add admin (runtime)\n"
        "• /accounts – how many sessions\n"
        "• /status – session auth status\n"
    )

@dp.message(Command("report"))
@admin_only
async def report_cmd(msg: types.Message):
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3:
        return await msg.reply("Usage: /report &lt;username&gt; &lt;reason_id 1‑5&gt;")
    username = parts[1].lstrip("@")
    try:
        idx = int(parts[2])
    except ValueError:
        return await msg.reply("Reason_id must be integer 1‑5")
    reasons = [
        (InputReportReasonSpam(), "Spam"),
        (InputReportReasonPornography(), "Pornography"),
        (InputReportReasonFake(), "Fake account"),
        (InputReportReasonIllegalDrugs(), "Illegal drugs"),
        (InputReportReasonViolence(), "Violence / Hate"),
    ]
    if not 1 <= idx <= 5:
        return await msg.reply("Reason_id must be between 1‑5")
    reason, label = reasons[idx - 1]
    await msg.reply(f"🚀 Starting reports on <b>@{username}</b> for <b>{label}</b>…")
    asyncio.create_task(report_username(username, reason, label))
    await msg.reply("Task queued ✅")

@dp.message(Command("accounts"))
@admin_only
async def accounts_cmd(msg: types.Message):
    await msg.reply(f"📊 <b>{len(load_accounts())}</b> sessions configured.")

@dp.message(Command("status"))
@admin_only
async def status_cmd(msg: types.Message):
    lines = []
    for acc in load_accounts():
        phone = acc["phone"]
        try:
            client = TelegramClient(f"{SESSIONS_DIR}/{phone}", acc["api_id"], acc["api_hash"])
            await client.connect()
            auth = await client.is_user_authorized()
            await client.disconnect()
            lines.append(f"{phone}: {'✅' if auth else '❌'}")
        except Exception as e:
            lines.append(f"{phone}: ⚠️ {e}")
    await msg.reply("\n".join(lines))

# ---------- NEW: /addadmin supports <id> *or* @username ----------
@dp.message(Command("addadmin"))
@admin_only
async def addadmin_cmd(msg: types.Message):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        return await msg.reply("Usage: /addadmin &lt;user_id | @username&gt;")
    target = parts[1].strip()

    # 1️⃣ If numeric
    if target.lstrip("-").isdigit():
        new_id = int(target)
    else:
        # 2️⃣ Resolve username → id
        if target.startswith("@"):
            target = target[1:]
        try:
            chat = await bot.get_chat(target)  # needs mutual chat or PM /start
            new_id = chat.id
        except Exception as e:
            return await msg.reply(f"❌ Could not resolve username: {e}")

    ADMIN_IDS.add(new_id)
    await msg.reply(
        f"✅ <code>{new_id}</code> added to admin list (RAM‑only).\n"
        "Add it to ADMIN_IDS env var for persistence."
    )

# ---------------------------------------------------------------------------
# 6. CLI helper coroutines (login, status)
# ---------------------------------------------------------------------------
async def cli_login():
    try:
        count = int(input("How many accounts to add? "))
    except ValueError:
        print("Please enter a number.")
        return
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    for i in range(count):
        print(f"[Account {i + 1}/{count}]")
        try:
            api_id = int(input("API_ID: "))
            api_hash = input("API_HASH: ").strip()
            phone = input("Phone (+cc...): ").strip()
            client = TelegramClient(f"{SESSIONS_DIR}/{phone}", api_id, api_hash)
            await client.start(phone=phone)
            await client.disconnect()
            data = load_accounts()
            data.append({"api_id": api_id, "api_hash": api_hash, "phone": phone})
            with open(ACCOUNTS_FILE, "w") as fp:
                json.dump(data, fp, indent=2)
            print(f"✅ Saved session for {phone}")
        except Exception as e:
            print("❌", e)

async def cli_status():
    accounts = load_accounts()
    if not accounts:
        print("No accounts saved.")
        return
    for acc in accounts:
        phone = acc["phone"]
        try:
            client = TelegramClient(f"{SESSIONS_DIR}/{phone}", acc["api_id"], acc["api_hash"])
            await client.connect()
            ok = await client.is_user_authorized()
            await client.disconnect()
            print(f"{phone}: {'Logged in' if ok else '❌ Not authorised'}")
        except Exception as e:
            print(f"{phone}: ⚠️ {e}")

# ---------------------------------------------------------------------------
# 7. Entrypoint – argparse
# ---------------------------------------------------------------------------

def run_cli():
    import argparse
    parser = argparse.ArgumentParser(description="Tele Report Bot")
    parser.add_argument(
        "mode",
        nargs="?",
        default="bot",
        choices=["bot", "login", "status"],
        help="login → interactive add accounts | status → check sessions | bot → start Aiogram (default)",
    )
    args = parser.parse_args()

    if args.mode == "login":
        asyncio.run(cli_login())
    elif args.mode == "status":
        asyncio.run(cli_status())
    else:
        logging.basicConfig(level=logging.INFO)
        from aiogram import executor
        executor.start_polling(dp, skip_updates=True)

# ---------------------------------------------------------------------------
if __name__ == "__main__":
    run_cli()
