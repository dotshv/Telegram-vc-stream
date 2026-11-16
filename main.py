import os
import sys
import time
import json
import asyncio
import aiohttp
import shutil

try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.types import Message, CallbackQuery
from pyrogram.raw import functions, types
from pyrogram.errors import (
    SessionPasswordNeeded,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    PasswordHashInvalid,
    FloodWait,
    UserDeactivated,
    AuthKeyUnregistered,
    MessageNotModified
)

API_ID = int(os.environ.get("API_ID", 21705136))
API_HASH = os.environ.get("API_HASH", "78730e89d196e160b0f1992018c6cb19")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "9216527978:AAE9B0nDJ_FcqdLD41Vlwf12QBubafZQYxI")
DATABASE_FILE = "bot_data.json"

database = {"users": {}}
user_states = {}
user_active_clients = {}
broadcast_progress = {}

import pyrogram.errors

def create_dynamic_error(name):
    err_cls = type(name, (pyrogram.errors.RPCError,), {"ID": name.upper(), "NAME": name})
    setattr(pyrogram.errors, name, err_cls)
    if hasattr(pyrogram.errors, "exceptions"):
        for submod_name in ["bad_request_400", "forbidden_403", "not_acceptable_406", "internal_server_error_500"]:
            submod = getattr(pyrogram.errors.exceptions, submod_name, None)
            if submod:
                setattr(submod, name, err_cls)
    return err_cls

pyrogram.errors.__getattr__ = create_dynamic_error

if hasattr(pyrogram.errors, "exceptions"):
    for submod_name in ["bad_request_400", "forbidden_403", "not_acceptable_406", "internal_server_error_500"]:
        submod = getattr(pyrogram.errors.exceptions, submod_name, None)
        if submod:
            submod.__getattr__ = create_dynamic_error

missing_errors = [
    "GroupcallForbidden",
    "GroupCallForbidden",
    "GroupcallInvalid",
    "GroupCallInvalid",
    "GroupcallAlreadyStarted",
    "GroupCallAlreadyStarted",
    "GroupcallNotFound",
    "GroupCallNotFound",
    "GroupcallJoinMissing",
    "GroupCallJoinMissing",
    "GroupcallServerCrash",
    "GroupCallServerCrash"
]
for err_name in missing_errors:
    create_dynamic_error(err_name)

from pyrogram.raw.core import TLObject
import pyrogram.raw.functions as raw_funcs
import pyrogram.raw.types as raw_types

def make_raw_stub(module_obj, name):
    cls = type(name, (TLObject,), {
        "ID": 0,
        "QUALNAME": f"{module_obj.__name__}.{name}",
        "read": classmethod(lambda c, b: c()),
        "write": lambda self, *a, **kw: b""
    })
    setattr(module_obj, name, cls)
    return cls

for mod in [raw_funcs, raw_types]:
    if mod:
        mod.__getattr__ = lambda n, m=mod: make_raw_stub(m, n)

if hasattr(raw_funcs, "phone"):
    raw_funcs.phone.__getattr__ = lambda n, m=raw_funcs.phone: make_raw_stub(m, n)
    for fn in ["CreateConferenceCall", "JoinConferenceCall", "LeaveConferenceCall", "InviteToConferenceCall", "GetConferenceCall"]:
        make_raw_stub(raw_funcs.phone, fn)

if hasattr(raw_types, "phone"):
    raw_types.phone.__getattr__ = lambda n, m=raw_types.phone: make_raw_stub(m, n)
    for tn in ["PhoneCallConference", "InputPhoneCallConference"]:
        make_raw_stub(raw_types.phone, tn)

if hasattr(raw_funcs, "channels"):
    raw_funcs.channels.__getattr__ = lambda n, m=raw_funcs.channels: make_raw_stub(m, n)

import inspect

def patch_raw_callable_class(cls):
    try:
        orig_init = cls.__init__
        sig = inspect.signature(orig_init)
        accepted = set(sig.parameters.keys())
        if "kwargs" in accepted:
            return
        def safe_init(self, *args, **kwargs):
            filtered = {k: v for k, v in kwargs.items() if k in accepted}
            return orig_init(self, *args, **filtered)
        cls.__init__ = safe_init
    except Exception:
        pass

if hasattr(raw_funcs, "phone"):
    for attr_name in dir(raw_funcs.phone):
        obj = getattr(raw_funcs.phone, attr_name)
        if isinstance(obj, type):
            patch_raw_callable_class(obj)

PYTGCALLS_ERROR = ""
try:
    from pytgcalls import PyTgCalls
    PYTGCALLS_AVAILABLE = True
except Exception as e:
    PYTGCALLS_AVAILABLE = False
    PYTGCALLS_ERROR = str(e)

import wave

def ensure_silence_file():
    path = "silence.wav"
    if not os.path.exists(path) or os.path.getsize(path) < 100:
        with wave.open(path, "wb") as f:
            f.setnchannels(2)
            f.setsampwidth(2)
            f.setframerate(48000)
            f.writeframes(b"\x00" * 48000 * 4 * 10)
    return os.path.abspath(path)

def load_database():
    global database
    if os.path.exists(DATABASE_FILE):
        try:
            with open(DATABASE_FILE, "r", encoding="utf-8") as f:
                database = json.load(f)
        except Exception:
            database = {"users": {}}
    else:
        database = {"users": {}}
    if "users" not in database:
        database["users"] = {}
    if "admins" not in database:
        database["admins"] = {"usernames": ["mr_radhe_il", "dotshv"], "user_ids": []}
    else:
        for default_admin in ["mr_radhe_il", "dotshv"]:
            if default_admin not in [u.lower() for u in database["admins"].get("usernames", [])]:
                database["admins"].setdefault("usernames", []).append(default_admin)
    if "approved_users" not in database:
        database["approved_users"] = []
    if "pending_users" not in database:
        database["pending_users"] = {}

def is_admin(user_id=None, username=None):
    admins = database.get("admins", {"usernames": ["mr_radhe_il", "dotshv"], "user_ids": []})
    if user_id and int(user_id) in admins.get("user_ids", []):
        return True
    if username:
        clean_user = username.lower().lstrip("@")
        if clean_user in [u.lower().lstrip("@") for u in admins.get("usernames", [])]:
            if user_id and int(user_id) not in admins.get("user_ids", []):
                admins.setdefault("user_ids", []).append(int(user_id))
                save_database()
            return True
    return False

def is_user_approved(user_id, username=None):
    if is_admin(user_id, username):
        return True
    approved_list = database.get("approved_users", [])
    return int(user_id) in approved_list

async def notify_admins_new_user(user_id, name, username):
    text = (
        "New Access Request\n\n"
        f"Name: {name}\n"
        f"ID: {user_id}\n"
        f"Username: @{username if username else 'None'}\n\n"
        "Choose an action below:"
    )
    kb = [
        [
            btn("Approve", f"admin:approve:{user_id}", "success"),
            btn("Disapprove", f"admin:disapprove:{user_id}", "danger")
        ]
    ]
    admins = database.get("admins", {})
    admin_ids = admins.get("user_ids", [])
    for aid in admin_ids:
        try:
            await send_menu_message(aid, text, kb)
        except Exception:
            pass

def build_admin_keyboard():
    return [
        [
            btn("Add Admin", "admin:add", "success"),
            btn("Remove Admin", "admin:remove_menu", "danger")
        ],
        [
            btn("Manage Users", "admin:users_menu", "primary"),
            btn("Refresh Panel", "admin:panel", "primary")
        ],
        [
            btn("Back to Main Menu", "menu:main")
        ]
    ]

def save_database():
    try:
        with open(DATABASE_FILE, "w", encoding="utf-8") as f:
            json.dump(database, f, indent=2)
    except Exception:
        pass

def get_user_data(user_id):
    uid = str(user_id)
    if uid not in database["users"]:
        database["users"][uid] = {
            "accounts": {},
            "auto_msg": {
                "enabled": False,
                "text": "Auto broadcast message set by bot.",
                "interval": 300,
                "last_sent": 0
            }
        }
        save_database()
    return database["users"][uid]

def btn(text, callback_data, style=None):
    data = {"text": text, "callback_data": callback_data}
    if style:
        data["style"] = style
    return data

def build_main_keyboard():
    return [
        [
            btn("Accounts", "menu:accounts", "primary"),
            btn("Auto Message", "menu:automsg", "primary")
        ],
        [
            btn("VC Status", "menu:vcstatus", "success"),
            btn("Live Progress", "menu:progress", "primary")
        ],
        [
            btn("Refresh Menu", "menu:main")
        ]
    ]

def build_accounts_keyboard():
    return [
        [
            btn("List Accounts", "accounts:list", "primary"),
            btn("Add via Session", "accounts:add_session", "success")
        ],
        [
            btn("Add via Phone OTP", "accounts:add_phone", "success"),
            btn("Remove Account", "accounts:remove_menu", "danger")
        ],
        [
            btn("Back to Main Menu", "menu:main")
        ]
    ]

def build_automsg_keyboard(is_enabled):
    toggle_text = "Turn OFF" if is_enabled else "Turn ON"
    toggle_data = "automsg:toggle_off" if is_enabled else "automsg:toggle_on"
    toggle_style = "danger" if is_enabled else "success"
    return [
        [
            btn(toggle_text, toggle_data, toggle_style)
        ],
        [
            btn("Set Message Text", "automsg:set_text", "primary"),
            btn("Set Time Interval", "automsg:interval_menu", "primary")
        ],
        [
            btn("View Current Message", "automsg:view_text", "primary"),
            btn("Live Progress", "menu:progress", "primary")
        ],
        [
            btn("Back to Main Menu", "menu:main")
        ]
    ]

def build_intervals_keyboard():
    return [
        [
            btn("1 Min", "automsg:interval:60", "primary"),
            btn("5 Min", "automsg:interval:300", "primary"),
            btn("10 Min", "automsg:interval:600", "primary")
        ],
        [
            btn("30 Min", "automsg:interval:1800", "primary"),
            btn("60 Min", "automsg:interval:3600", "primary"),
            btn("Custom Seconds", "automsg:custom_interval", "success")
        ],
        [
            btn("Back to Auto Message", "menu:automsg")
        ]
    ]

def build_cancel_keyboard(back_callback="menu:main"):
    return [
        [btn("Cancel", back_callback, "danger")]
    ]

async def send_menu_message(chat_id, text, keyboard):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {"inline_keyboard": keyboard}
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                return await resp.json()
    except Exception:
        return None

async def edit_menu_message(chat_id, message_id, text, keyboard):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "reply_markup": {"inline_keyboard": keyboard}
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                return await resp.json()
    except Exception:
        return None

async def start_client_account(user_id_str, acc_id, session_str, acc_name):
    try:
        client = Client(
            name=f"cli_{user_id_str}_{acc_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_str,
            in_memory=True
        )
        await client.start()
        print(f"[ACCOUNT] Client started: {acc_name} ({acc_id})")
        call_obj = None
        if PYTGCALLS_AVAILABLE:
            try:
                call_obj = PyTgCalls(client)
                start_res = call_obj.start()
                if asyncio.iscoroutine(start_res):
                    await start_res
                print(f"[ACCOUNT] PyTgCalls started successfully for {acc_name}")
            except Exception as e:
                print(f"[ACCOUNT] PyTgCalls start failed for {acc_name}: {e}")
                call_obj = None
        else:
            print(f"[ACCOUNT] PyTgCalls not installed: {PYTGCALLS_ERROR}")
        if user_id_str not in user_active_clients:
            user_active_clients[user_id_str] = {}
        user_active_clients[user_id_str][acc_id] = {
            "client": client,
            "call": call_obj,
            "name": acc_name,
            "joined_vcs": set(),
            "joined_call_ids": {}
        }
        return True
    except Exception as e:
        print(f"[ACCOUNT] Failed to start {acc_name}: {e}")
        return False

async def stop_client_account(user_id_str, acc_id):
    if user_id_str in user_active_clients and acc_id in user_active_clients[user_id_str]:
        info = user_active_clients[user_id_str][acc_id]
        call_obj = info.get("call")
        client = info.get("client")
        if call_obj:
            try:
                for chat_id in list(info.get("joined_vcs", set())):
                    await leave_vc_call(call_obj, chat_id)
            except Exception:
                pass
            try:
                stop_res = call_obj.stop()
                if asyncio.iscoroutine(stop_res):
                    await stop_res
            except Exception:
                pass
        if client:
            try:
                await client.stop()
            except Exception:
                pass
        del user_active_clients[user_id_str][acc_id]

async def init_saved_accounts():
    load_database()
    for uid, udata in database.get("users", {}).items():
        accs = udata.get("accounts", {})
        for aid, adata in accs.items():
            sess = adata.get("session")
            name = adata.get("name", f"Account_{aid}")
            if sess:
                ok = await start_client_account(uid, aid, sess, name)
                if ok:
                    asyncio.create_task(scan_and_join_account_vcs(uid, aid))

dead_call_ids = set()

async def join_vc_call(call_obj, chat_id):
    silence = ensure_silence_file()
    if not shutil.which("ffprobe"):
        try:
            import static_ffmpeg
            static_ffmpeg.add_paths()
        except Exception:
            pass
    if not shutil.which("ffprobe"):
        print("[VC JOIN ERROR] ffprobe is not installed on this system! Please run: sudo apt install -y ffmpeg")
        return False, "NO_FFPROBE"
    try:
        from pytgcalls.types import MediaStream
        res = call_obj.play(chat_id, MediaStream(silence))
        if asyncio.iscoroutine(res):
            await res
        try:
            m = call_obj.set_is_mute(True)
            if asyncio.iscoroutine(m):
                await m
        except Exception:
            pass
        return True, "OK"
    except Exception as e:
        err_msg = str(e)
        print(f"[VC JOIN ATTEMPT 1 FAILED] {err_msg}")
        if "GROUPCALL_INVALID" in err_msg:
            return False, "GROUPCALL_INVALID"

    try:
        res = call_obj.play(chat_id, silence)
        if asyncio.iscoroutine(res):
            await res
        try:
            m = call_obj.set_is_mute(True)
            if asyncio.iscoroutine(m):
                await m
        except Exception:
            pass
        return True, "OK"
    except Exception as e:
        err_msg = str(e)
        print(f"[VC JOIN ATTEMPT 2 FAILED] {err_msg}")
        if "GROUPCALL_INVALID" in err_msg:
            return False, "GROUPCALL_INVALID"

    return False, "FAILED"

async def leave_vc_call(call_obj, chat_id):
    try:
        res = call_obj.leave_call(chat_id)
        if asyncio.iscoroutine(res):
            await res
        return True
    except Exception:
        pass
    try:
        res = call_obj.leave_group_call(chat_id)
        if asyncio.iscoroutine(res):
            await res
        return True
    except Exception:
        pass
    try:
        res = call_obj.leave(chat_id)
        if asyncio.iscoroutine(res):
            await res
        return True
    except Exception:
        pass
    return False

async def get_active_vc_id(client, chat_id):
    try:
        peer = await client.resolve_peer(chat_id)
        full_chat = None
        if isinstance(peer, (types.InputPeerChannel, types.InputChannel)):
            r = await client.invoke(functions.channels.GetFullChannel(channel=peer))
            full_chat = r.full_chat
        elif isinstance(peer, types.InputPeerChat):
            r = await client.invoke(functions.messages.GetFullChat(chat_id=peer.chat_id))
            full_chat = r.full_chat
        if full_chat and getattr(full_chat, "call", None):
            call_input = full_chat.call
            c_id = getattr(call_input, "id", None)
            if not c_id or c_id in dead_call_ids:
                return None
            try:
                gc = await client.invoke(functions.phone.GetGroupCall(call=call_input, limit=1))
                if hasattr(gc, "call"):
                    if isinstance(gc.call, types.GroupCallDiscarded):
                        dead_call_ids.add(c_id)
                        return None
                    if isinstance(gc.call, types.GroupCall):
                        return c_id
            except Exception as e:
                err_str = str(e)
                if "GROUPCALL_INVALID" in err_str:
                    dead_call_ids.add(c_id)
                    return None
            return c_id
        return None
    except FloodWait as fw:
        await asyncio.sleep(fw.value)
        return None
    except Exception:
        return None

async def is_voice_chat_active(client, chat_id):
    cid = await get_active_vc_id(client, chat_id)
    return cid is not None

async def scan_and_join_account_vcs(user_id_str, acc_id):
    if user_id_str not in user_active_clients:
        return
    acc_info = user_active_clients[user_id_str].get(acc_id)
    if not acc_info:
        return
    if acc_info.get("is_scanning", False):
        return
    acc_info["is_scanning"] = True
    try:
        client = acc_info.get("client")
        call_obj = acc_info.get("call")
        joined_vcs = acc_info.get("joined_vcs")
        joined_call_ids = acc_info.setdefault("joined_call_ids", {})
        acc_name = acc_info.get("name", acc_id)
        if not client or not client.is_connected or not call_obj:
            return

        now = time.time()
        last_refresh = acc_info.get("last_dialog_refresh", 0)
        known_chats = acc_info.get("known_chats", {})

        if now - last_refresh > 30 or not known_chats:
            try:
                async for dialog in client.get_dialogs():
                    chat = dialog.chat
                    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
                        known_chats[chat.id] = chat.title or str(chat.id)
                acc_info["known_chats"] = known_chats
                acc_info["last_dialog_refresh"] = now
            except Exception:
                pass

        if len(joined_vcs) >= 1:
            current_cid = next(iter(joined_vcs))
            active_call_id = await get_active_vc_id(client, current_cid)
            current_call_id = joined_call_ids.get(current_cid)
            if active_call_id is None or (current_call_id is not None and active_call_id != current_call_id):
                ctitle = known_chats.get(current_cid, str(current_cid))
                print(f"[VC ENDED] Active VC ended in: {ctitle}. Account is now free.")
                await leave_vc_call(call_obj, current_cid)
                joined_call_ids.pop(current_cid, None)
                joined_vcs.discard(current_cid)
            return

        for cid, ctitle in list(known_chats.items()):
            active_call_id = await get_active_vc_id(client, cid)
            if active_call_id is not None:
                print(f"[VC DETECTED] Active VC found in: {ctitle} ({cid})")
                print(f"[VC JOINING] Connecting to {ctitle}...")
                ok, err_reason = await join_vc_call(call_obj, cid)
                if ok:
                    joined_call_ids[cid] = active_call_id
                    joined_vcs.add(cid)
                    print(f"[VC JOINED] Successfully joined: {ctitle}")
                    break
                else:
                    if err_reason == "GROUPCALL_INVALID":
                        dead_call_ids.add(active_call_id)
                        print(f"[VC INACTIVE] Voice chat is ended on Telegram: {ctitle}")
                        await leave_vc_call(call_obj, cid)
                        joined_call_ids.pop(cid, None)
                        joined_vcs.discard(cid)
                    else:
                        print(f"[VC JOIN FAILED] Could not join: {ctitle}")
    finally:
        acc_info["is_scanning"] = False

async def background_vc_scanner():
    while True:
        try:
            for uid in list(user_active_clients.keys()):
                accs = list(user_active_clients[uid].keys())
                for aid in accs:
                    await scan_and_join_account_vcs(uid, aid)
        except Exception as e:
            print(f"[BACKGROUND SCAN ERROR] {e}")
        await asyncio.sleep(3)

async def run_broadcast_for_user(user_id_str, msg_text):
    prog = broadcast_progress.get(user_id_str, {})
    prog["is_running"] = True
    prog["status"] = "Broadcasting"
    prog["sent"] = 0
    prog["failed"] = 0
    prog["total_chats"] = 0
    prog["current_account"] = "Preparing"
    prog["current_chat"] = "Preparing"
    broadcast_progress[user_id_str] = prog

    accs = user_active_clients.get(user_id_str, {})
    target_list = []
    for aid, ainfo in accs.items():
        client = ainfo.get("client")
        if not client or not client.is_connected:
            continue
        try:
            async for dialog in client.get_dialogs():
                chat = dialog.chat
                if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
                    target_list.append((aid, ainfo.get("name", aid), client, chat.id, chat.title or str(chat.id)))
        except Exception:
            pass

    prog["total_chats"] = len(target_list)
    for aid, aname, cli, cid, ctitle in target_list:
        udata = database.get("users", {}).get(user_id_str, {})
        if not udata.get("auto_msg", {}).get("enabled", False):
            prog["status"] = "Stopped by user"
            break
        prog["current_account"] = aname
        prog["current_chat"] = ctitle
        try:
            await cli.send_message(cid, msg_text)
            prog["sent"] += 1
        except FloodWait as fw:
            await asyncio.sleep(fw.value)
            try:
                await cli.send_message(cid, msg_text)
                prog["sent"] += 1
            except Exception:
                prog["failed"] += 1
        except Exception:
            prog["failed"] += 1
        await asyncio.sleep(1.5)

    prog["is_running"] = False
    if prog["status"] != "Stopped by user":
        prog["status"] = "Finished"
    prog["last_finished"] = time.time()

async def background_auto_message():
    while True:
        try:
            now = time.time()
            for uid, udata in database.get("users", {}).items():
                auto_cfg = udata.get("auto_msg", {})
                if not auto_cfg.get("enabled", False):
                    continue
                interval = int(auto_cfg.get("interval", 300))
                last_sent = float(auto_cfg.get("last_sent", 0))
                text = auto_cfg.get("text", "")
                if not text:
                    continue
                if now - last_sent >= interval:
                    auto_cfg["last_sent"] = now
                    save_database()
                    asyncio.create_task(run_broadcast_for_user(uid, text))
        except Exception:
            pass
        await asyncio.sleep(5)

bot = Client(
    name="vc_manager_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

@bot.on_message(filters.command(["start", "help"]) & filters.private)
async def handle_start(client: Client, message: Message):
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name or "User"
    get_user_data(user_id)

    if not is_user_approved(user_id, username):
        pending = database.setdefault("pending_users", {})
        pending[str(user_id)] = {
            "name": first_name,
            "username": username or "",
            "time": time.time()
        }
        save_database()
        await notify_admins_new_user(user_id, first_name, username)
        await message.reply_text(
            "Access Request Sent\n\n"
            "Your access request has been sent to the admins. Please wait for approval."
        )
        return

    text = (
        "Voice Chat & Broadcast Manager\n\n"
        "Control panel to manage multiple accounts, 24/7 background voice chat presence, and automated group broadcasts."
    )
    await send_menu_message(user_id, text, build_main_keyboard())

@bot.on_message(filters.command("admin") & filters.private)
async def handle_admin_command(client: Client, message: Message):
    user_id = message.from_user.id
    username = message.from_user.username
    if not is_admin(user_id, username):
        await message.reply_text("Access Denied. You are not authorized to use admin commands.")
        return
    admins = database.get("admins", {})
    u_list = admins.get("usernames", [])
    id_list = admins.get("user_ids", [])
    approved = database.get("approved_users", [])
    pending = database.get("pending_users", {})
    text = (
        "Admin Control Panel\n\n"
        f"Admin Usernames: {', '.join(['@' + u for u in u_list]) if u_list else 'None'}\n"
        f"Admin IDs: {', '.join([str(i) for i in id_list]) if id_list else 'None'}\n"
        f"Approved Users: {len(approved)}\n"
        f"Pending Requests: {len(pending)}"
    )
    await send_menu_message(user_id, text, build_admin_keyboard())

@bot.on_message(filters.command("cancel") & filters.private)
async def handle_cancel(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id in user_states:
        info = user_states[user_id]
        temp_cli = info.get("temp_client")
        if temp_cli:
            try:
                await temp_cli.disconnect()
            except Exception:
                pass
        del user_states[user_id]
        await send_menu_message(user_id, "Current operation cancelled.", build_main_keyboard())
    else:
        await send_menu_message(user_id, "No active operation to cancel.", build_main_keyboard())

@bot.on_callback_query()
async def handle_callbacks(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    username = query.from_user.username
    msg_id = query.message.id
    uid = str(user_id)
    udata = get_user_data(user_id)
    data = query.data

    try:
        await query.answer()
    except Exception:
        pass

    if data.startswith("admin:"):
        if not is_admin(user_id, username):
            await query.answer("Access Denied. Admins only.", show_alert=True)
            return
        if data == "admin:panel":
            admins = database.get("admins", {})
            u_list = admins.get("usernames", [])
            id_list = admins.get("user_ids", [])
            approved = database.get("approved_users", [])
            pending = database.get("pending_users", {})
            text = (
                "Admin Control Panel\n\n"
                f"Admin Usernames: {', '.join(['@' + u for u in u_list]) if u_list else 'None'}\n"
                f"Admin IDs: {', '.join([str(i) for i in id_list]) if id_list else 'None'}\n"
                f"Approved Users: {len(approved)}\n"
                f"Pending Requests: {len(pending)}"
            )
            await edit_menu_message(user_id, msg_id, text, build_admin_keyboard())
            return
        elif data == "admin:add":
            user_states[user_id] = {"state": "AWAIT_ADMIN_INPUT"}
            text = "Send the Telegram username (e.g. @username) or numeric Telegram User ID to add as admin:\nSend /cancel to abort."
            await edit_menu_message(user_id, msg_id, text, build_cancel_keyboard("admin:panel"))
            return
        elif data == "admin:remove_menu":
            admins = database.get("admins", {})
            u_list = admins.get("usernames", [])
            id_list = admins.get("user_ids", [])
            buttons = []
            for u in u_list:
                buttons.append([btn(f"Remove @{u}", f"admin:del_user:{u}", "danger")])
            for i in id_list:
                buttons.append([btn(f"Remove ID {i}", f"admin:del_id:{i}", "danger")])
            buttons.append([btn("Back to Admin Panel", "admin:panel")])
            text = "Select an admin to remove:"
            await edit_menu_message(user_id, msg_id, text, buttons)
            return
        elif data.startswith("admin:del_user:"):
            target_u = data.split("admin:del_user:")[1].lower()
            admins = database.get("admins", {})
            if target_u in [x.lower() for x in admins.get("usernames", [])]:
                admins["usernames"] = [x for x in admins.get("usernames", []) if x.lower() != target_u]
                save_database()
            text = f"Admin @{target_u} has been removed."
            kb = [[btn("Back to Admin Panel", "admin:panel")]]
            await edit_menu_message(user_id, msg_id, text, kb)
            return
        elif data.startswith("admin:del_id:"):
            target_id = int(data.split("admin:del_id:")[1])
            admins = database.get("admins", {})
            if target_id in admins.get("user_ids", []):
                admins["user_ids"].remove(target_id)
                save_database()
            text = f"Admin ID {target_id} has been removed."
            kb = [[btn("Back to Admin Panel", "admin:panel")]]
            await edit_menu_message(user_id, msg_id, text, kb)
            return
        elif data.startswith("admin:approve:"):
            target_uid = int(data.split("admin:approve:")[1])
            approved = database.setdefault("approved_users", [])
            if target_uid not in approved:
                approved.append(target_uid)
            pending = database.setdefault("pending_users", {})
            pending.pop(str(target_uid), None)
            save_database()
            try:
                await send_menu_message(
                    target_uid,
                    "Access Approved\n\nYour request has been approved by admin. Send /start to access the panel.",
                    build_main_keyboard()
                )
            except Exception:
                pass
            text = f"User Approved\n\nUser ID: {target_uid}\nStatus: Approved"
            kb = [
                [btn("Disapprove User", f"admin:disapprove:{target_uid}", "danger")],
                [btn("Admin Panel", "admin:panel")]
            ]
            await edit_menu_message(user_id, msg_id, text, kb)
            return
        elif data.startswith("admin:disapprove:"):
            target_uid = int(data.split("admin:disapprove:")[1])
            approved = database.setdefault("approved_users", [])
            if target_uid in approved:
                approved.remove(target_uid)
            pending = database.setdefault("pending_users", {})
            pending.pop(str(target_uid), None)
            save_database()
            try:
                await send_menu_message(
                    target_uid,
                    "Access Revoked\n\nYour access has been disapproved/revoked by admin.",
                    []
                )
            except Exception:
                pass
            text = f"User Disapproved\n\nUser ID: {target_uid}\nStatus: Disapproved"
            kb = [
                [btn("Approve User", f"admin:approve:{target_uid}", "success")],
                [btn("Admin Panel", "admin:panel")]
            ]
            await edit_menu_message(user_id, msg_id, text, kb)
            return
        elif data == "admin:users_menu":
            approved = database.get("approved_users", [])
            pending = database.get("pending_users", {})
            buttons = []
            for p_id, p_info in list(pending.items())[:10]:
                p_name = p_info.get("name", p_id)
                buttons.append([btn(f"Approve {p_name} ({p_id})", f"admin:approve:{p_id}", "success")])
            for a_id in list(approved)[:10]:
                buttons.append([btn(f"Disapprove ID {a_id}", f"admin:disapprove:{a_id}", "danger")])
            buttons.append([btn("Back to Admin Panel", "admin:panel")])
            text = f"Manage Users\n\nApproved: {len(approved)} | Pending: {len(pending)}"
            await edit_menu_message(user_id, msg_id, text, buttons)
            return

    if not is_user_approved(user_id, username):
        await query.answer("Access Denied. You are not approved.", show_alert=True)
        return

    if data == "menu:main":
        text = (
            "Voice Chat & Broadcast Manager\n\n"
            "Select an option to manage your accounts and automations:"
        )
        await edit_menu_message(user_id, msg_id, text, build_main_keyboard())

    elif data == "menu:accounts":
        accs = udata.get("accounts", {})
        count = len(accs)
        active_count = len(user_active_clients.get(uid, {}))
        text = (
            "Accounts Management\n\n"
            f"Configured Accounts: {count}\n"
            f"Connected Clients: {active_count}"
        )
        await edit_menu_message(user_id, msg_id, text, build_accounts_keyboard())

    elif data == "accounts:list":
        accs = udata.get("accounts", {})
        if not accs:
            text = "No accounts connected yet.\nUse Add via Session or Add via Phone OTP to connect an account."
        else:
            lines = ["Connected Accounts:\n"]
            act_map = user_active_clients.get(uid, {})
            for aid, adata in accs.items():
                name = adata.get("name", "Unknown")
                phone = adata.get("phone", "N/A")
                is_act = aid in act_map
                status_str = "Online" if is_act else "Offline"
                vc_count = len(act_map[aid]["joined_vcs"]) if is_act else 0
                lines.append(f"Name: {name}\nID: {aid}\nPhone: {phone}\nStatus: {status_str}\nActive VCs: {vc_count}\n")
            text = "\n".join(lines)
        kb = [[btn("Back to Accounts", "menu:accounts")]]
        await edit_menu_message(user_id, msg_id, text, kb)

    elif data == "accounts:add_session":
        user_states[user_id] = {"state": "AWAIT_SESSION"}
        text = "Send your Pyrogram Session String now.\nSend /cancel to abort."
        await edit_menu_message(user_id, msg_id, text, build_cancel_keyboard("menu:accounts"))

    elif data == "accounts:add_phone":
        user_states[user_id] = {"state": "AWAIT_PHONE"}
        text = "Send your phone number with country code (e.g. +919876543210):\nSend /cancel to abort."
        await edit_menu_message(user_id, msg_id, text, build_cancel_keyboard("menu:accounts"))

    elif data == "accounts:remove_menu":
        accs = udata.get("accounts", {})
        if not accs:
            text = "No accounts available to remove."
            kb = [[btn("Back to Accounts", "menu:accounts")]]
            await edit_menu_message(user_id, msg_id, text, kb)
            return
        buttons = []
        for aid, adata in accs.items():
            name = adata.get("name", aid)
            buttons.append([btn(f"Remove {name}", f"accounts:delete:{aid}", "danger")])
        buttons.append([btn("Back to Accounts", "menu:accounts")])
        text = "Select an account to remove:"
        await edit_menu_message(user_id, msg_id, text, buttons)

    elif data.startswith("accounts:delete:"):
        aid = data.split("accounts:delete:")[1]
        await stop_client_account(uid, aid)
        if aid in udata.get("accounts", {}):
            del udata["accounts"][aid]
            save_database()
        text = f"Account {aid} has been removed."
        kb = [[btn("Back to Accounts", "menu:accounts")]]
        await edit_menu_message(user_id, msg_id, text, kb)

    elif data == "menu:automsg":
        auto_cfg = udata.get("auto_msg", {})
        is_on = auto_cfg.get("enabled", False)
        interval = auto_cfg.get("interval", 300)
        status_str = "Enabled" if is_on else "Disabled"
        mins = interval // 60
        secs = interval % 60
        time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
        text = (
            "Auto Message Settings\n\n"
            f"Broadcaster: {status_str}\n"
            f"Interval: {interval} seconds ({time_str})"
        )
        await edit_menu_message(user_id, msg_id, text, build_automsg_keyboard(is_on))

    elif data == "automsg:toggle_on":
        udata["auto_msg"]["enabled"] = True
        save_database()
        interval = udata["auto_msg"].get("interval", 300)
        mins = interval // 60
        secs = interval % 60
        time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
        text = (
            "Auto Message Settings\n\n"
            "Broadcaster: Enabled\n"
            f"Interval: {interval} seconds ({time_str})"
        )
        await edit_menu_message(user_id, msg_id, text, build_automsg_keyboard(True))

    elif data == "automsg:toggle_off":
        udata["auto_msg"]["enabled"] = False
        save_database()
        interval = udata["auto_msg"].get("interval", 300)
        mins = interval // 60
        secs = interval % 60
        time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
        text = (
            "Auto Message Settings\n\n"
            "Broadcaster: Disabled\n"
            f"Interval: {interval} seconds ({time_str})"
        )
        await edit_menu_message(user_id, msg_id, text, build_automsg_keyboard(False))

    elif data == "automsg:set_text":
        user_states[user_id] = {"state": "AWAIT_MSG_TEXT"}
        text = "Send the new broadcast message text.\nSend /cancel to abort."
        await edit_menu_message(user_id, msg_id, text, build_cancel_keyboard("menu:automsg"))

    elif data == "automsg:view_text":
        msg_text = udata.get("auto_msg", {}).get("text", "No message set.")
        text = f"Current Broadcast Message:\n\n{msg_text}"
        kb = [[btn("Back to Auto Message", "menu:automsg")]]
        await edit_menu_message(user_id, msg_id, text, kb)

    elif data == "automsg:interval_menu":
        text = "Select a preset interval or enter custom seconds:"
        await edit_menu_message(user_id, msg_id, text, build_intervals_keyboard())

    elif data.startswith("automsg:interval:"):
        sec_val = int(data.split("automsg:interval:")[1])
        udata["auto_msg"]["interval"] = sec_val
        save_database()
        is_on = udata.get("auto_msg", {}).get("enabled", False)
        status_str = "Enabled" if is_on else "Disabled"
        mins = sec_val // 60
        secs = sec_val % 60
        time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
        text = (
            "Auto Message Settings\n\n"
            f"Broadcaster: {status_str}\n"
            f"Interval: {sec_val} seconds ({time_str})"
        )
        await edit_menu_message(user_id, msg_id, text, build_automsg_keyboard(is_on))

    elif data == "automsg:custom_interval":
        user_states[user_id] = {"state": "AWAIT_CUSTOM_INTERVAL"}
        text = "Send the interval time in seconds (minimum 10 seconds):\nExample: 120\nSend /cancel to abort."
        await edit_menu_message(user_id, msg_id, text, build_cancel_keyboard("menu:automsg"))

    elif data == "menu:vcstatus":
        act_map = user_active_clients.get(uid, {})
        total_accs = len(act_map)
        total_vcs = sum(len(x.get("joined_vcs", set())) for x in act_map.values())
        lines = [
            "Voice Chat Scanner Status\n",
            "Auto Scanner: Active 24/7 (1 min refresh)\n",
            f"Connected Accounts: {total_accs}\n",
            f"Joined Voice Chats: {total_vcs}\n"
        ]
        for aid, ainfo in act_map.items():
            name = ainfo.get("name", aid)
            vcs = ainfo.get("joined_vcs", set())
            lines.append(f"Account: {name} | Joined VCs: {len(vcs)}")
        text = "\n".join(lines)
        kb = [
            [btn("Force Scan & Join Now", "vc:force_scan", "success")],
            [btn("Refresh Status", "menu:vcstatus", "primary")],
            [btn("Back to Main Menu", "menu:main")]
        ]
        await edit_menu_message(user_id, msg_id, text, kb)

    elif data == "vc:force_scan":
        act_map = user_active_clients.get(uid, {})
        for aid in list(act_map.keys()):
            asyncio.create_task(scan_and_join_account_vcs(uid, aid))
        total_accs = len(act_map)
        total_vcs = sum(len(x.get("joined_vcs", set())) for x in act_map.values())
        text = (
            "Voice Chat Scan Triggered\n\n"
            f"Scanning groups and channels across {total_accs} accounts in background.\n"
            f"Active joined VCs currently: {total_vcs}\n"
            "Any active voice chat will be joined immediately."
        )
        kb = [
            [btn("Refresh Status", "menu:vcstatus", "primary")],
            [btn("Back to Main Menu", "menu:main")]
        ]
        await edit_menu_message(user_id, msg_id, text, kb)

    elif data == "menu:progress":
        prog = broadcast_progress.get(uid, {})
        is_run = prog.get("is_running", False)
        status_str = prog.get("status", "Idle")
        sent = prog.get("sent", 0)
        failed = prog.get("failed", 0)
        total = prog.get("total_chats", 0)
        curr_acc = prog.get("current_account", "None")
        curr_chat = prog.get("current_chat", "None")
        last_fin = prog.get("last_finished", 0)
        fin_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_fin)) if last_fin else "Never"

        auto_cfg = udata.get("auto_msg", {})
        is_on = auto_cfg.get("enabled", False)
        interval = int(auto_cfg.get("interval", 300))
        last_sent = float(auto_cfg.get("last_sent", 0))
        now = time.time()
        time_until_next = max(0, int(interval - (now - last_sent))) if is_on else 0

        text = (
            "Live Broadcast Progress\n\n"
            f"Status: {status_str}\n"
            f"Active: {'Yes' if is_run else 'No'}\n"
            f"Account: {curr_acc}\n"
            f"Target: {curr_chat}\n"
            f"Sent: {sent} / {total}\n"
            f"Failed: {failed}\n"
            f"Last Run: {fin_str}\n\n"
            f"Broadcaster: {'Enabled' if is_on else 'Disabled'}\n"
            f"Next Scheduled: in {time_until_next}s"
        )
        kb = [
            [btn("Refresh Progress", "menu:progress", "primary")],
            [btn("Back to Main Menu", "menu:main")]
        ]
        await edit_menu_message(user_id, msg_id, text, kb)

@bot.on_message(filters.private & ~filters.command(["start", "help", "cancel", "admin"]))
async def handle_user_inputs(client: Client, message: Message):
    user_id = message.from_user.id
    username = message.from_user.username
    uid = str(user_id)
    udata = get_user_data(user_id)

    if user_id not in user_states:
        return

    state_info = user_states[user_id]
    current_state = state_info.get("state")

    if current_state == "AWAIT_ADMIN_INPUT":
        if not is_admin(user_id, username):
            del user_states[user_id]
            return
        raw_val = message.text.strip()
        admins = database.setdefault("admins", {"usernames": ["mr_radhe_il", "dotshv"], "user_ids": []})
        del user_states[user_id]
        if raw_val.startswith("@") or not raw_val.isdigit():
            clean_name = raw_val.lstrip("@").lower()
            if clean_name not in [u.lower() for u in admins.get("usernames", [])]:
                admins.setdefault("usernames", []).append(clean_name)
                save_database()
                await send_menu_message(user_id, f"Admin @{clean_name} added successfully.", build_admin_keyboard())
            else:
                await send_menu_message(user_id, f"Admin @{clean_name} is already registered.", build_admin_keyboard())
        else:
            try:
                new_id = int(raw_val)
                if new_id not in admins.get("user_ids", []):
                    admins.setdefault("user_ids", []).append(new_id)
                    save_database()
                    await send_menu_message(user_id, f"Admin ID {new_id} added successfully.", build_admin_keyboard())
                else:
                    await send_menu_message(user_id, f"Admin ID {new_id} is already registered.", build_admin_keyboard())
            except ValueError:
                await send_menu_message(user_id, "Invalid input. Send username or numeric ID.", build_admin_keyboard())
        return

    if not is_user_approved(user_id, username):
        del user_states[user_id]
        return

    if current_state == "AWAIT_SESSION":
        session_text = message.text.strip()
        test_cli = Client(
            name=f"test_{user_id}_{int(time.time())}",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_text,
            in_memory=True
        )
        status_resp = await send_menu_message(user_id, "Verifying session string...", [])
        status_id = status_resp.get("result", {}).get("message_id") if status_resp else None
        try:
            await test_cli.start()
            me = await test_cli.get_me()
            acc_id = str(me.id)
            acc_name = me.first_name or f"User_{acc_id}"
            phone = me.phone_number or "N/A"
            await test_cli.stop()

            udata["accounts"][acc_id] = {
                "name": acc_name,
                "phone": phone,
                "session": session_text
            }
            save_database()
            ok = await start_client_account(uid, acc_id, session_text, acc_name)
            if ok:
                asyncio.create_task(scan_and_join_account_vcs(uid, acc_id))
            del user_states[user_id]
            kb = [
                [btn("View Accounts", "accounts:list", "primary")],
                [btn("Back to Menu", "menu:main")]
            ]
            success_text = f"Account connected successfully.\nName: {acc_name}\nID: {acc_id}\nPhone: {phone}"
            if status_id:
                await edit_menu_message(user_id, status_id, success_text, kb)
            else:
                await send_menu_message(user_id, success_text, kb)
        except Exception as e:
            try:
                await test_cli.stop()
            except Exception:
                pass
            del user_states[user_id]
            kb = [
                [btn("Try Again", "accounts:add_session", "primary")],
                [btn("Back to Menu", "menu:main")]
            ]
            err_text = f"Connection failed: {str(e)}"
            if status_id:
                await edit_menu_message(user_id, status_id, err_text, kb)
            else:
                await send_menu_message(user_id, err_text, kb)

    elif current_state == "AWAIT_PHONE":
        phone_number = message.text.strip().replace(" ", "")
        temp_cli = Client(
            name=f"temp_phone_{user_id}_{int(time.time())}",
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True
        )
        status_resp = await send_menu_message(user_id, "Sending login code...", [])
        status_id = status_resp.get("result", {}).get("message_id") if status_resp else None
        try:
            await temp_cli.connect()
            sent_code = await temp_cli.send_code(phone_number)
            state_info["temp_client"] = temp_cli
            state_info["phone"] = phone_number
            state_info["phone_code_hash"] = sent_code.phone_code_hash
            state_info["state"] = "AWAIT_CODE"
            kb = [[btn("Cancel", "menu:accounts", "danger")]]
            prompt_text = f"Code sent to {phone_number}.\nEnter the OTP received on Telegram (e.g. 1 2 3 4 5):\nSend /cancel to abort."
            if status_id:
                await edit_menu_message(user_id, status_id, prompt_text, kb)
            else:
                await send_menu_message(user_id, prompt_text, kb)
        except Exception as e:
            try:
                await temp_cli.disconnect()
            except Exception:
                pass
            del user_states[user_id]
            kb = [
                [btn("Try Again", "accounts:add_phone", "primary")],
                [btn("Back to Menu", "menu:main")]
            ]
            err_text = f"Failed to send code: {str(e)}"
            if status_id:
                await edit_menu_message(user_id, status_id, err_text, kb)
            else:
                await send_menu_message(user_id, err_text, kb)

    elif current_state == "AWAIT_CODE":
        phone_code = message.text.strip().replace(" ", "").replace("-", "")
        temp_cli = state_info.get("temp_client")
        phone_number = state_info.get("phone")
        phone_code_hash = state_info.get("phone_code_hash")
        status_resp = await send_menu_message(user_id, "Verifying OTP code...", [])
        status_id = status_resp.get("result", {}).get("message_id") if status_resp else None
        try:
            signed_in = False
            try:
                await temp_cli.sign_in(phone_number, phone_code_hash, phone_code)
                signed_in = True
            except SessionPasswordNeeded:
                state_info["state"] = "AWAIT_2FA"
                kb = [[btn("Cancel", "menu:accounts", "danger")]]
                twofa_text = "2FA password required.\nEnter your 2FA password:\nSend /cancel to abort."
                if status_id:
                    await edit_menu_message(user_id, status_id, twofa_text, kb)
                else:
                    await send_menu_message(user_id, twofa_text, kb)
                return

            if signed_in:
                session_str = await temp_cli.export_session_string()
                me = await temp_cli.get_me()
                acc_id = str(me.id)
                acc_name = me.first_name or f"User_{acc_id}"
                await temp_cli.disconnect()

                udata["accounts"][acc_id] = {
                    "name": acc_name,
                    "phone": phone_number,
                    "session": session_str
                }
                save_database()
                ok = await start_client_account(uid, acc_id, session_str, acc_name)
                if ok:
                    asyncio.create_task(scan_and_join_account_vcs(uid, acc_id))
                del user_states[user_id]
                kb = [
                    [btn("View Accounts", "accounts:list", "primary")],
                    [btn("Back to Menu", "menu:main")]
                ]
                ok_text = f"Logged in successfully.\nName: {acc_name}\nID: {acc_id}\nPhone: {phone_number}"
                if status_id:
                    await edit_menu_message(user_id, status_id, ok_text, kb)
                else:
                    await send_menu_message(user_id, ok_text, kb)
        except Exception as e:
            try:
                await temp_cli.disconnect()
            except Exception:
                pass
            del user_states[user_id]
            kb = [
                [btn("Try Again", "accounts:add_phone", "primary")],
                [btn("Back to Menu", "menu:main")]
            ]
            err_text = f"Login failed: {str(e)}"
            if status_id:
                await edit_menu_message(user_id, status_id, err_text, kb)
            else:
                await send_menu_message(user_id, err_text, kb)

    elif current_state == "AWAIT_2FA":
        password = message.text.strip()
        temp_cli = state_info.get("temp_client")
        phone_number = state_info.get("phone")
        status_resp = await send_menu_message(user_id, "Verifying 2FA password...", [])
        status_id = status_resp.get("result", {}).get("message_id") if status_resp else None
        try:
            await temp_cli.check_password(password)
            session_str = await temp_cli.export_session_string()
            me = await temp_cli.get_me()
            acc_id = str(me.id)
            acc_name = me.first_name or f"User_{acc_id}"
            await temp_cli.disconnect()

            udata["accounts"][acc_id] = {
                "name": acc_name,
                "phone": phone_number,
                "session": session_str
            }
            save_database()
            ok = await start_client_account(uid, acc_id, session_str, acc_name)
            if ok:
                asyncio.create_task(scan_and_join_account_vcs(uid, acc_id))
            del user_states[user_id]
            kb = [
                [btn("View Accounts", "accounts:list", "primary")],
                [btn("Back to Menu", "menu:main")]
            ]
            ok_text = f"2FA verified and logged in.\nName: {acc_name}\nID: {acc_id}\nPhone: {phone_number}"
            if status_id:
                await edit_menu_message(user_id, status_id, ok_text, kb)
            else:
                await send_menu_message(user_id, ok_text, kb)
        except Exception as e:
            try:
                await temp_cli.disconnect()
            except Exception:
                pass
            del user_states[user_id]
            kb = [
                [btn("Try Again", "accounts:add_phone", "primary")],
                [btn("Back to Menu", "menu:main")]
            ]
            err_text = f"2FA failed: {str(e)}"
            if status_id:
                await edit_menu_message(user_id, status_id, err_text, kb)
            else:
                await send_menu_message(user_id, err_text, kb)

    elif current_state == "AWAIT_MSG_TEXT":
        new_text = message.text
        udata["auto_msg"]["text"] = new_text
        save_database()
        del user_states[user_id]
        kb = [
            [btn("Back to Auto Message", "menu:automsg", "primary")],
            [btn("Back to Main Menu", "menu:main")]
        ]
        await send_menu_message(user_id, "Broadcast message text updated successfully.", kb)

    elif current_state == "AWAIT_CUSTOM_INTERVAL":
        raw_val = message.text.strip()
        try:
            sec_val = int(raw_val)
            if sec_val < 10:
                await send_menu_message(user_id, "Interval must be at least 10 seconds. Enter a valid number:", [])
                return
            udata["auto_msg"]["interval"] = sec_val
            save_database()
            del user_states[user_id]
            kb = [
                [btn("Back to Auto Message", "menu:automsg", "primary")],
                [btn("Back to Main Menu", "menu:main")]
            ]
            await send_menu_message(user_id, f"Broadcast interval set to {sec_val} seconds.", kb)
        except ValueError:
            await send_menu_message(user_id, "Invalid number. Enter numeric seconds (e.g. 180):", [])

async def main():
    load_database()
    await bot.start()
    bot_info = await bot.get_me()
    print(f"Bot started successfully as @{bot_info.username}")
    print(f"PyTgCalls library available: {PYTGCALLS_AVAILABLE}")
    if not PYTGCALLS_AVAILABLE:
        print(f"PyTgCalls error details: {PYTGCALLS_ERROR}")
    if not shutil.which("ffprobe"):
        print("[NOTICE] ffprobe is missing on your system. Run: sudo apt install -y ffmpeg")
    await init_saved_accounts()
    asyncio.create_task(background_vc_scanner())
    asyncio.create_task(background_auto_message())
    print("Background tasks for VC scanning and Auto-Message are active.")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
