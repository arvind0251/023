# tele_report_bot.py
"""
All‑in‑one Telegram tool:
 • CLI mode: add/login new user accounts (Telethon) or check status
 • Bot mode : admin‑only commands to trigger mass reports with those user accounts

Run examples
------------
# ➜ python tele_report_bot.py login      # interactive account adder (one‑time)
# ➜ python tele_report_bot.py status     # check all saved accounts
# ➜ python tele_report_bot.py            # start aiogram bot (needs BOT_TOKEN)

Environment variables (create .env or export):
---------------------------------------------
BOT_TOKEN = 123456:ABC...   # @BotFather token
ADMIN_IDS = 1000001,1000002 # comma‑separated Telegram user_ids allowed to use commands

Files generated / expected
--------------------------
accounts.json   # list of {api_id, api_hash, phone}
sessions/       # *.session files (Telethon user sessions)

Dependencies
------------
 pip install -U aiogram==3.* telethon python-dotenv
"""

import asyncio, argparse, json, os, random, sys, time, logging
from pathlib import Path
from typing import List, Tuple

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.functions.messages import ReportRequest
from telethon.tl.types import (
    InputReportReasonSpam, InputReportReasonPornography,
    InputReportReasonFake, InputReportReasonIllegalDrugs, InputReportReasonViolence,
)

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command

# ---------------------------------------------------------------------------
# Configuration / constants
# ---------------------------------------------------------------------------
SESS_DIR = Path("sessions")
ACCOUNTS_FILE = Path("accounts.json")
SESS_DIR.mkdir(exist_ok=True)

# Telethon reasons pool
REASONS: List[Tuple[object, str]] = [
    (InputReportReasonSpam(), "Spam"),
    (InputReportReasonPornography(), "Pornography"),
    (InputReportReasonFake(), "Fake / Impersonation"),
    (InputReportReasonIllegalDrugs(), "Illegal Drugs"),
    (InputReportReasonViolence(), "Violence / Terror"),
]

# ---------------------------------------------------------------------------
# Helper functions for accounts management
# ---------------------------------------------------------------------------

def load_accounts():
    if ACCOUNTS_FILE.exists():
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_accounts(accounts):
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(accounts, f, indent=4)

async def iter_clients():
    """Async generator yielding (client, phone) for every authorised session."""
    for acc in load_accounts():
        client = TelegramClient(SESS_DIR / acc["phone"], acc["api_id"], acc["api_hash"])
        try:
            await client.connect()
            if await client.is_user_authorized():
                yield client, acc["phone"]
            else:
                await client.disconnect()
        except Exception:
            await client.disconnect()

# ---------------------------------------------------------------------------
# CLI utilities
# ---------------------------------------------------------------------------
async def login_new_account():
    print("\n=== Add & Login New Account ===")
    api_id = int(input("API ID: "))
    api_hash = input("API HASH: ").strip()
    phone = input("Phone (+CCXXXXXXXXXX): ").strip()

    client = TelegramClient(SESS_DIR / phone, api_id, api_hash)
    await client.connect()
    try:
        if await client.is_user_authorized():
            print("[!] This account is already authorised.")
        else:
            await client.send_code_request(phone)
            code = input("Enter the code you received: ").strip()
            try:
                await client.sign_in(phone, code)
            except PasswordHashInvalidError:
                pwd = input("Two‑factor password required: ")
                await client.sign_in(password=pwd)
            print("[+] Logged in successfully → session saved.")
            accounts = load_accounts()
            accounts.append({"api_id": api_id, "api_hash": api_hash, "phone": phone})
            save_accounts(accounts)
    finally:
        await client.disconnect()

async def check_status():
    print("\n=== Account Status ===")
    if not load_accounts():
        print("No accounts saved.")
        return
    async for client, phone in iter_clients():
        print(f"{phone}: Logged In ✅")
        await client.disconnect()

# ---------------------------------------------------------------------------
# Telethon task: mass report username
# ---------------------------------------------------------------------------
async def report_username(username: str, reason_idx: int, per_account: int = 10):
    reason_obj, reason_lbl = REASONS[reason_idx]
    log = []

    async for client, phone in iter_clients():
        try:
            target = await client.get_input_entity(username)
            txt = f"Bot‑triggered report: {reason_lbl}"
            for i in range(per_account):
                try:
                    await client(ReportRequest(target, [], reason_obj, txt))
                    log.append(f"[{phone}] {i+1}/{per_account} ✅")
                    await asyncio.sleep(random.uniform(3, 6))
                except FloodWaitError as e:
                    log.append(f"[{phone}] FloodWait {e.seconds}s – sleeping…")
                    await asyncio.sleep(e.seconds + 1)
        except Exception as e:
            log.append(f"[{phone}] error: {e}")
        finally:
            await client.disconnect()
    return "\n".join(log)

# ---------------------------------------------------------------------------
# Aiogram bot setup
# ---------------------------------------------------------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x}

bot = Bot(BOT_TOKEN, parse_mode=ParseMode.HTML) if BOT_TOKEN else None

async def admin_only(handler):
    async def wrapper(message: types.Message):
        if message.from_user.id not in ADMIN_IDS:
            await message.reply("⛔️ Only admins allowed.")
            return
        return await handler(message)
    return wrapper

dp = Dispatcher()

@dp.message(Command("start"))
async def start_cmd(msg: types.Message):
    await msg.reply("👋 <b>Ready.</b>\nCommands:\n/report &lt;user&gt; &lt;reason_id 1‑5&gt; [count]\n/accounts – how many sessions")

@dp.message(Command("accounts"))
@admin_only
async def accounts_cmd(msg: types.Message):
    total = len(load_accounts())
    await msg.reply(f"📊 <b>{total}</b> accounts configured.")

@dp.message(Command("report"))
@admin_only
async def report_cmd(msg: types.Message):
    parts = msg.text.split()
    if len(parts) < 3:
        await msg.reply("Usage: /report &lt;username&gt; &lt;reason_id 1‑5&gt; [count]")
        return
    username = parts[1].lstrip("@")
    try:
        reason_idx = int(parts[2]) - 1
        if not 0 <= reason_idx < len(REASONS):
            raise ValueError
    except ValueError:
        await msg.reply("Reason_id must be 1‑5")
        return
    per_account = int(parts[3]) if len(parts) > 3 else 10
    await msg.reply(f"🚀 Reporting @{username} – reason <b>{REASONS[reason_idx][1]}</b> – {per_account}× per account…")
    log = await report_username(username, reason_idx, per_account)
    if len(log) > 4000:
        log = log[-4000:]
    await msg.reply("<pre>" + log + "</pre>")

# ---------------------------------------------------------------------------
# Entrypoint logic
# ---------------------------------------------------------------------------
async def run_bot():
    logging.basicConfig(level=logging.INFO)
    from aiogram import executor
    await dp.start_polling(bot)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Telegram multi‑account reporter bot + CLI")
    parser.add_argument("mode", nargs="?", choices=["login", "status"], help="login = add new account, status = check accounts")
    args = parser.parse_args()

    if args.mode == "login":
        asyncio.run(login_new_account())
    elif args.mode == "status":
        asyncio.run(check_status())
    else:
        if not BOT_TOKEN:
            print("[!] BOT_TOKEN not set. Export BOT_TOKEN and ADMIN_IDS first.")
            sys.exit(1)
        try:
            asyncio.run(run_bot())
        except (KeyboardInterrupt, SystemExit):
            print("Bot stopped.")
