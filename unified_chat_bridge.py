import subprocess
import threading
import sys


def _pi_startup_trace(stage):
    """Write Linux-only startup checkpoints to the systemd journal."""
    if sys.platform.startswith("linux"):
        print(f"[PI STARTUP] {stage}", flush=True)


_pi_startup_trace("Bridge-Modulimport gestartet")
CNG_DEFAULT_AVATAR = "https://cng-plattform.com/manus-storage/cng-logo-obs-download_d9182c49.png"


# Cache for creator-profile data. It is queried ONLY after the live-viewer
# endpoint has already identified an entry as a Creator via its ``batch`` field.
from pathlib import Path
_pi_startup_trace("TikTokLive-Import startet")
try:
    from TikTokLive import TikTokLiveClient
    from TikTokLive.events import (
        ConnectEvent, CommentEvent, DisconnectEvent, EmoteChatEvent,
        GiftEvent, LikeEvent, FollowEvent, ShareEvent, SubNotifyEvent, RoomUserSeqEvent,
        WebsocketResponseEvent, UnknownEvent
    )
    TIKTOK_AVAILABLE = True
except ImportError:
    TikTokLiveClient = None
    ConnectEvent = CommentEvent = DisconnectEvent = EmoteChatEvent = None
    GiftEvent = LikeEvent = FollowEvent = ShareEvent = SubNotifyEvent = RoomUserSeqEvent = None
    WebsocketResponseEvent = UnknownEvent = None
    TIKTOK_AVAILABLE = False
_pi_startup_trace(f"TikTokLive-Import beendet (verfuegbar={TIKTOK_AVAILABLE})")

# -*- coding: utf-8 -*-
"""
CNG MultiChat Bridge
CNG + Twitch

No Chromium.
No Firefox.
No browser automation.

CNG:
  Reads the broadcaster's current OBS chat and presence tokens from CNG.
  Polls streamChat.getMessages with obsChatToken and sends presenceHeartbeat;
  the authenticated session-based chat request remains as a fallback.

Twitch:
  Uses Twitch IRC over secure WebSocket.
  The Twitch channel is read from accounts.json.
  Twitch IRC requires a User Access Token with chat:read.
"""

import asyncio
import json
import time
import base64
import builtins as _builtins
import ctypes
import hashlib
import mimetypes
import re
import threading
import ssl
import os
import sys

# Frozen Windows builds may choose cp1252 for redirected console output. Chat
# messages contain emoji, so make stdout/stderr UTF-8 and escape any invalid
# surrogate rather than letting a print failure kill the receiver thread.
def _configure_utf8_output(stream):
    if stream is None:
        return
    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return
    try:
        reconfigure(encoding="utf-8", errors="backslashreplace")
    except (OSError, ValueError):
        pass


for _output_stream in (sys.stdout, sys.stderr):
    _configure_utf8_output(_output_stream)
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urlparse
import urllib.request
import urllib.error
import uuid
_pi_startup_trace("requests-Import startet")
import requests
_pi_startup_trace("requests-Import beendet")
from datetime import datetime, timezone

_pi_startup_trace("websocket-Import startet")
try:
    import websocket
except ImportError:
    websocket = None
_pi_startup_trace(f"websocket-Import beendet (verfuegbar={websocket is not None})")

# Bridge workers log from several threads. Serialize complete print calls so
# unrelated platform messages cannot be spliced into a Kick error line.
_PRINT_LOCK = threading.RLock()
_BUILTIN_PRINT = _builtins.print


def print(*args, **kwargs):
    with _PRINT_LOCK:
        return _BUILTIN_PRINT(*args, **kwargs)

BASE_DIR = Path(
    sys.executable if getattr(sys, "frozen", False) else __file__
).resolve().parent
CONFIG_FILE = str(BASE_DIR / "unified_chat_config.json")
DISPLAY_CONFIG_FILE = str(BASE_DIR / "display_settings.json")
COMMAND_BOT_CONFIG_FILE = str(BASE_DIR / "command_bot.json")
ALERT_CONFIG_FILE = BASE_DIR / "alert_settings.json"
CNG_LOG_DIR = BASE_DIR / "logs"
CNG_LOG_FILE = CNG_LOG_DIR / "cng_chat.log"
CNG_BASE_URL = "https://cng-plattform.com"
CNG_CHAT_POLL_SECONDS = 2.0
CNG_VIEWER_IDENTITY_REFRESH_SECONDS = 10.0

# Chat logging: .\logs\YYYY\MM.json (JSON Lines / NDJSON)
CHAT_LOGGING_ENABLED = True
CHAT_LOGGING_PLATFORMS = {
    "twitch": True,
    "kick": True,
    "youtube": True,
    "tiktok": True,
    "cng": True,
}
_CHAT_LOG_LOCK = threading.Lock()
_CHAT_LOG_SEEN_IDS = set()
_CHAT_LOG_SEEN_MAX = 20000

def _chat_log_timestamp(message):
    raw = message.get("timestamp") or message.get("createdAt") or message.get("time") or message.get("date")
    if isinstance(raw, (int, float)):
        try:
            value = float(raw)
            if value > 100000000000:
                value /= 1000.0
            return datetime.fromtimestamp(value, tz=timezone.utc).astimezone()
        except Exception:
            pass
    if isinstance(raw, str) and raw.strip():
        try:
            dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone()
        except Exception:
            pass
    return datetime.now().astimezone()

def log_chat_message(message):
    if not CHAT_LOGGING_ENABLED or not isinstance(message, dict):
        return
    platform = str(message.get("platform") or message.get("source") or "").strip().lower()
    event = str(message.get("event") or "chat").strip().lower()
    if not platform or not CHAT_LOGGING_PLATFORMS.get(platform, True) or event not in {"chat", "system"}:
        return
    username = str(message.get("username") or message.get("userName") or message.get("displayName") or "").strip()
    content = message.get("message") if message.get("message") is not None else message.get("content")
    content = str(content or "")
    message_id = message.get("id") or message.get("messageId") or message.get("chatId")
    with _CHAT_LOG_LOCK:
        if message_id is not None and str(message_id).strip():
            key = f"{platform}:{message_id}"
            if key in _CHAT_LOG_SEEN_IDS:
                return
            _CHAT_LOG_SEEN_IDS.add(key)
            if len(_CHAT_LOG_SEEN_IDS) > _CHAT_LOG_SEEN_MAX:
                _CHAT_LOG_SEEN_IDS.clear()
                _CHAT_LOG_SEEN_IDS.add(key)
        dt = _chat_log_timestamp(message)
        directory = BASE_DIR / "logs" / dt.strftime("%Y")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{dt.strftime('%m')}.json"
        record = {"timestamp": dt.isoformat(), "platform": platform, "username": username, "message": content, "event": event}
        for key in ("id", "messageId", "userId", "channel", "channelId", "alertType", "amount", "currency", "role"):
            if key in message and message[key] is not None:
                record[key] = message[key]
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            fh.write("\n")

CNG_SSE_RECONNECT_SECONDS = 3.0
HOST, PORT = "0.0.0.0", 8765

clients = []
clients_lock = threading.Lock()

alert_clients = []
alert_clients_lock = threading.Lock()

# CNG stream status (for OBS docks / external overlays).
status_clients = []
status_clients_lock = threading.Lock()

# Combined multi-platform status for /cng-status.
social_status_lock = threading.Lock()
social_status = {
    "event": "multi_platform_status",
    "total": {"viewers": 0, "followers": 0},
    "platforms": {
        "cng": {"viewers": 0, "followers": 0, "available": False},
        "twitch": {"viewers": 0, "followers": 0, "available": False},
        "kick": {"viewers": 0, "followers": 0, "available": False},
        "youtube": {"viewers": 0, "followers": 0, "available": False},
        "tiktok": {"viewers": 0, "followers": 0, "available": False},
    },
    "updatedAt": time.time(),
}

def get_social_status():
    with social_status_lock:
        return {"event": social_status["event"], "total": dict(social_status["total"]), "platforms": {k: dict(v) for k, v in social_status["platforms"].items()}, "updatedAt": social_status["updatedAt"]}


def update_social_status(platform, viewers=None, followers=None, available=True):
    platform = str(platform or "").strip().lower()
    with social_status_lock:
        if platform not in social_status["platforms"]:
            return
        item = social_status["platforms"][platform]
        if viewers is not None:
            try: item["viewers"] = max(0, int(viewers))
            except (TypeError, ValueError): pass
        if followers is not None:
            try: item["followers"] = max(0, int(followers))
            except (TypeError, ValueError): pass
        item["available"] = bool(available)
        social_status["total"]["viewers"] = sum(int(x.get("viewers") or 0) for x in social_status["platforms"].values() if x.get("available"))
        social_status["total"]["followers"] = sum(int(x.get("followers") or 0) for x in social_status["platforms"].values() if x.get("available"))
        social_status["updatedAt"] = time.time()

status_lock = threading.Lock()

config_clients = []
config_clients_lock = threading.Lock()
config_last_signature = None
cng_status = {
    "event": "stream_status",
    "platform": "cng",
    "username": "",
    "isLive": False,
    "viewerCount": 0,
    "followerCount": 0,
    "streamTitle": "",
    "streamGameName": "",
    "streamChannelName": "",
    "streamSessionId": None,
    "streamMode": "",
    "tiktokIsLive": False,
    "updatedAt": None,
    "error": None
}

# Alert types that are sent to the dedicated OBS alerts.html stream.
ALERT_OVERLAY_TYPES = {"follow", "donation", "unfollow", "raid"}

cng_seen = set()
cng_seen_lock = threading.Lock()

# Profile-image cache: user ID -> avatar URL.
avatar_cache = {}
avatar_cache_lock = threading.Lock()

# CNG Beta viewer identity cache: streamer/channel -> displayName -> username.
# The viewer endpoint is creator-authenticated, so every cache belongs to the
# CNG account/session of the streamer whose chat is being bridged.
cng_viewer_identity_cache = {}
cng_viewer_identity_lock = threading.Lock()

# CNG creator-status cache: username -> (is_creator, avatar_url).
# This is intentionally separate from the generic avatar cache because a
# normal watcher may have a user avatar but must never be classified as a creator
# merely because the generic avatar resolver tried the creator endpoint.
CNG_AVATAR_MISSING = object()

# CNG creator cookie used for authenticated creators.myFollowers requests.
# This is deliberately separate from cng_bot_config.json because that cookie
# belongs to the CNG-Bot account, not the creator account.
CNG_CREATOR_COOKIE_FILE = Path(BASE_DIR) / "cng_creator_cookie.json"
CNG_FOLLOWERS_FILE = BASE_DIR / "cng_followers.json"
CNG_FOLLOWERS_URL = "https://cng-plattform.com/api/trpc/creators.myFollowers?batch=1&input=%7B%220%22%3A%7B%22json%22%3Anull%2C%22meta%22%3A%7B%22values%22%3A%5B%22undefined%22%5D%7D%7D%7D"
CNG_FOLLOWERS_INTERVAL = 15.0
# Wird vom CNG-Statusmonitor gesetzt, sobald die Followerzahl sinkt.
# Die Unfollow-Listenprüfung wird dadurch sofort zusätzlich angestoßen.
cng_follower_check_event = threading.Event()


def load_config():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _account_auth_value(account, *keys):
    auth = account.get("auth") if isinstance(account, dict) else None
    if not isinstance(auth, dict):
        auth = {}
    for key in keys:
        value = auth.get(key)
        if value not in (None, ""):
            return str(_obs_dpapi_decrypt(str(value))).strip()
    for key in keys:
        value = account.get(key) if isinstance(account, dict) else None
        if value not in (None, ""):
            return str(value).strip()
    return ""


def apply_accounts_to_config(config):
    """Overlay platform account data from accounts.json onto bridge config.

    accounts.json is the sole source of truth for platform accounts. The
    unified_chat_config.json file contains bridge/runtime settings only.
    """
    cfg = dict(config or {})
    try:
        accounts = load_accounts_file()
    except Exception as exc:
        print("[ACCOUNTS] accounts.json konnte nicht geladen werden:", repr(exc))
        return cfg

    def first(platform):
        for account in accounts:
            if str(account.get("platform") or "").strip().lower() == platform:
                return account
        return None

    twitch = first("twitch")
    if twitch:
        channel = str(twitch.get("channel") or twitch.get("username") or "").strip()
        cfg["twitch_channel"] = channel
        cfg["twitch_login"] = channel
        cfg["twitch_client_id"] = _account_auth_value(twitch, "client_id")
        cfg["twitch_oauth_token"] = _account_auth_value(twitch, "access_token", "oauth_token")
        cfg["twitch_refresh_token"] = _account_auth_value(twitch, "refresh_token")

    tiktok = first("tiktok")
    if tiktok:
        cfg["tiktok_channel"] = str(tiktok.get("channel") or tiktok.get("display_name") or "").strip()

    cng = first("cng")
    if cng:
        cfg["cng_channel"] = str(cng.get("channel") or cng.get("display_name") or "").strip()
        cfg["cng_username"] = cfg["cng_channel"]
        cfg["cng_session"] = _account_auth_value(cng, "cng_session", "session")
        # Legacy CNG tRPC endpoints identify the broadcaster by numeric creatorId.
        # Keep this in runtime config so chat, alerts, followers and status all
        # address exactly the same CNG creator.
        try:
            creator_id = int(cng.get("creator_id"))
            if creator_id > 0:
                cfg["cng_creator_id"] = creator_id
        except (TypeError, ValueError):
            pass
        cfg["cng_ref"] = _account_auth_value(cng, "cng_ref", "ref")

    youtube_accounts = [a for a in accounts if str(a.get("platform") or "").strip().lower() == "youtube"]
    if youtube_accounts:
        cfg["youtube_channel"] = str(youtube_accounts[0].get("channel") or "").strip()
        cfg["youtube_channels"] = [
            {
                "id": str(a.get("id") or a.get("channel") or ""),
                "channel": str(a.get("channel") or ""),
                "display_name": str(a.get("display_name") or a.get("channel") or "YouTube"),
            }
            for a in youtube_accounts
            if str(a.get("channel") or "").strip()
        ]

    # Kick is deliberately not copied into the bridge config. kick_loop()
    # resolves the channel and chatroom directly from accounts.json.
    for key in ("kick_channel", "kick_chatroom_id", "kick_pusher_app_key"):
        cfg.pop(key, None)

    return cfg


def load_display_config():
    """Load only the settings needed by the chat display overlay."""
    defaults = {
        "auto_hide_messages": True,
        "message_lifetime_seconds": 10,
        "chat_layout": "classic",
        "chat_font_size": 14,
        "name_color_mode": "auto",
        "fixed_name_color": "#ffffff",
        "name_color_palette": [
            "#ff6b6b", "#4dabf7", "#51cf66", "#ffd43b",
            "#cc5de8", "#20c997", "#ff922b", "#f06595",
        ],
        "platform_name_colors": {
            "twitch": "#9147ff",
            "kick": "#53e000",
            "cng": "#ff8a00",
            "tiktok": "#25f4ee",
            "youtube": "#ff0000",
        },
        "user_name_colors": {},
        "message_text_color": "#f5f5f7",
    }

    def valid_color(value, fallback):
        value = str(value or "").strip()
        return value.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else fallback

    def normalize_user_colors(value):
        if not isinstance(value, dict):
            return {}
        result = {}
        for username, color in value.items():
            key = str(username or "").strip().lower()
            normalized = valid_color(color, "")
            if key and normalized:
                result[key] = normalized
        return result

    def normalize_config(data):
        raw_auto_hide = data.get("auto_hide_messages", defaults["auto_hide_messages"])
        if isinstance(raw_auto_hide, bool):
            auto_hide = raw_auto_hide
        elif isinstance(raw_auto_hide, str):
            auto_hide = raw_auto_hide.strip().lower() in ("true", "1", "yes", "on")
        else:
            auto_hide = bool(raw_auto_hide)

        try:
            seconds = int(data.get("message_lifetime_seconds", defaults["message_lifetime_seconds"]))
        except (TypeError, ValueError):
            seconds = defaults["message_lifetime_seconds"]

        try:
            font_size = int(data.get("chat_font_size", defaults["chat_font_size"]))
        except (TypeError, ValueError):
            font_size = defaults["chat_font_size"]

        chat_layout = str(data.get("chat_layout", defaults["chat_layout"])).strip().lower()
        allowed_layouts = {"classic", "minimal", "bubble", "compact", "neon", "fancy"}
        if chat_layout not in allowed_layouts:
            chat_layout = defaults["chat_layout"]

        color_mode = str(data.get("name_color_mode", defaults["name_color_mode"])).strip().lower()
        if color_mode not in {"auto", "palette", "platform", "fixed"}:
            color_mode = defaults["name_color_mode"]

        palette = data.get("name_color_palette", defaults["name_color_palette"])
        if isinstance(palette, list):
            palette = [valid_color(color, "") for color in palette]
            palette = [color for color in palette if color][:32]
        else:
            palette = []
        if not palette:
            palette = list(defaults["name_color_palette"])

        platform_colors = dict(defaults["platform_name_colors"])
        raw_platform_colors = data.get("platform_name_colors")
        if isinstance(raw_platform_colors, dict):
            for platform in platform_colors:
                platform_colors[platform] = valid_color(
                    raw_platform_colors.get(platform), platform_colors[platform]
                )

        return {
            "auto_hide_messages": auto_hide,
            "message_lifetime_seconds": max(1, min(300, seconds)),
            "chat_layout": chat_layout,
            "chat_font_size": max(8, min(32, font_size)),
            "name_color_mode": color_mode,
            "fixed_name_color": valid_color(data.get("fixed_name_color"), defaults["fixed_name_color"]),
            "name_color_palette": palette,
            "platform_name_colors": platform_colors,
            "user_name_colors": normalize_user_colors(data.get("user_name_colors")),
            "message_text_color": valid_color(data.get("message_text_color"), defaults["message_text_color"]),
        }

    try:
        with open(DISPLAY_CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError("Display-Konfiguration muss ein JSON-Objekt sein.")
        return normalize_config(data)

    except (OSError, ValueError, json.JSONDecodeError) as e:
        print("[DISPLAY CONFIG] Konnte display_settings.json nicht laden:", repr(e))
        return normalize_config(defaults)


def publish(message):
    try:
        log_chat_message(message)
    except Exception as exc:
        print("[CHAT LOG] Fehler:", repr(exc))
    packet = (
        "data: " +
        json.dumps(message, ensure_ascii=False, separators=(",", ":")) +
        "\n\n"
    ).encode("utf-8")

    dead = []

    with clients_lock:
        for w in list(clients):
            try:
                w.write(packet)
                w.flush()
            except Exception:
                dead.append(w)

        for w in dead:
            if w in clients:
                clients.remove(w)


def publish_sse(message):
    """Publish a non-chat event to the main /events SSE stream without chat logging."""
    packet = (
        "data: " +
        json.dumps(message, ensure_ascii=False, separators=(",", ":")) +
        "\n\n"
    ).encode("utf-8")

    dead = []
    delivered = 0
    with clients_lock:
        for w in list(clients):
            try:
                w.write(packet)
                w.flush()
                delivered += 1
            except Exception:
                dead.append(w)
        for w in dead:
            if w in clients:
                clients.remove(w)
    return delivered


def publish_alert(message):
    """Publish an alert to the dedicated alert SSE stream."""
    packet = (
        "data: " +
        json.dumps(message, ensure_ascii=False, separators=(",", ":")) +
        "\n\n"
    ).encode("utf-8")

    dead = []
    delivered = 0

    with alert_clients_lock:
        for w in list(alert_clients):
            try:
                w.write(packet)
                w.flush()
                delivered += 1
            except Exception:
                dead.append(w)

        for w in dead:
            if w in alert_clients:
                alert_clients.remove(w)

    alert_type = message.get("alertType", "alert") if isinstance(message, dict) else "alert"
    alert_id = message.get("alertId", "") if isinstance(message, dict) else ""
    print(f"[CNG ALERT] SSE gesendet: type={alert_type} | alertId={alert_id} | clients={delivered}")


def get_cng_status():
    with status_lock:
        return dict(cng_status)


def publish_status(status):
    """Update the cached CNG status and publish it only when it changed."""
    global cng_status

    with status_lock:
        old = dict(cng_status)
        cng_status = dict(status)

    # Avoid waking every OBS client every polling cycle when nothing changed.
    comparable_old = dict(old)
    comparable_new = dict(status)
    comparable_old.pop("updatedAt", None)
    comparable_new.pop("updatedAt", None)

    if comparable_old == comparable_new:
        return

    packet = (
        "data: " +
        json.dumps(status, ensure_ascii=False, separators=(",", ":")) +
        "\n\n"
    ).encode("utf-8")

    dead = []
    with status_clients_lock:
        for w in list(status_clients):
            try:
                w.write(packet)
                w.flush()
            except Exception:
                dead.append(w)

        for w in dead:
            if w in status_clients:
                status_clients.remove(w)


# TEST CREDENTIALS ARE STORED IN unified_chat_config_v4.json.\n# Replace/revoke them after testing.\n# ============================================================\n# CNG\n# ============================================================

def display_config_signature(config):
    return json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def publish_display_config(config):
    packet = (
        "data: " +
        json.dumps({"type": "display_config", **config},
                   ensure_ascii=False, separators=(",", ":")) +
        "\n\n"
    ).encode("utf-8")
    dead = []
    with config_clients_lock:
        for w in list(config_clients):
            try:
                w.write(packet)
                w.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                dead.append(w)
        for w in dead:
            if w in config_clients:
                config_clients.remove(w)


def display_config_watch_loop():
    global config_last_signature
    while True:
        try:
            config = load_display_config()
            signature = display_config_signature(config)
            if config_last_signature is None:
                config_last_signature = signature
            elif signature != config_last_signature:
                config_last_signature = signature
                print("[DISPLAY CONFIG] Änderung erkannt:", config)
                publish_display_config(config)
        except Exception as e:
            print("[DISPLAY CONFIG] Watcher-Fehler:", repr(e))
        time.sleep(0.25)








def _cng_json_value(value):
    """Unwrap common CNG response envelopes without assuming one exact shape."""
    for _ in range(8):
        if isinstance(value, list):
            if len(value) == 1:
                value = value[0]
                continue
            return value
        if not isinstance(value, dict):
            return value
        changed = False
        for key in ("data", "json", "result", "payload", "message"):
            child = value.get(key)
            if isinstance(child, (dict, list)) and child is not value:
                value = child
                changed = True
                break
        if not changed:
            return value
    return value








def _cng_sse_data_lines(response):
    """Yield decoded SSE data payloads from a urllib HTTP response."""
    data_lines = []
    while True:
        raw = response.readline()
        if not raw:
            if data_lines:
                yield "\n".join(data_lines)
            return
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line == "":
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())




def cng_legacy_session_value(value):
    """Normalize a CNG session/cookie value for legacy tRPC requests."""
    raw = str(value or "").strip()
    if raw.lower().startswith("cng_session="):
        raw = raw.split("=", 1)[1].split(";", 1)[0].strip()
    elif raw.lower().startswith("__host-cng_session="):
        raw = raw.split("=", 1)[1].split(";", 1)[0].strip()
    return raw


def cng_url(creator_id, obs_chat_token=None, after_id=None, limit=50):
    chat_query = {"creatorId": creator_id, "limit": limit}
    if after_id is not None:
        chat_query["afterId"] = after_id
    if obs_chat_token:
        chat_query["obsChatToken"] = obs_chat_token

    payload = {"0": {"json": chat_query}}
    return (
        f"{CNG_BASE_URL}/api/trpc/streamChat.getMessages?batch=1&input="
        + quote(json.dumps(payload, separators=(",", ":")), safe="")
    )


def _cng_trpc_error_status(data):
    """Extract only the HTTP-like status from a batched tRPC error."""
    row = data[0] if isinstance(data, list) and data else data
    if not isinstance(row, dict) or not isinstance(row.get("error"), dict):
        return None
    error = row["error"]
    error_json = error.get("json") if isinstance(error.get("json"), dict) else error
    metadata = error_json.get("data") if isinstance(error_json, dict) else None
    if not isinstance(metadata, dict):
        return None
    status = metadata.get("httpStatus") or metadata.get("statusCode")
    if status is None:
        status = {"UNAUTHORIZED": 401, "FORBIDDEN": 403}.get(metadata.get("code"))
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def cng_obs_chat_token_url():
    """Return the dashboard tRPC query that reads the current OBS chat tokens."""
    payload = {
        "0": {
            "json": None,
            "meta": {"values": ["undefined"]},
        }
    }
    return (
        f"{CNG_BASE_URL}/api/trpc/broadcaster.getObsChatToken?batch=1&input="
        + quote(json.dumps(payload, separators=(",", ":")), safe="")
    )


def cng_fetch_obs_chat_tokens(cookie, expected_creator_id):
    """Read the current OBS chat and presence tokens without rotating them."""
    headers = {
        "Accept": "*/*",
        "User-Agent": "Mozilla/5.0 CNG-Unified-Chat/8.0",
        "Referer": f"{CNG_BASE_URL}/dashboard",
        "Origin": CNG_BASE_URL,
        "Cookie": cookie,
    }
    req = urllib.request.Request(cng_obs_chat_token_url(), headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"OBS-Chat-Token HTTP {exc.code}") from None

    try:
        value = data[0]["result"]["data"] if isinstance(data, list) else data["result"]["data"]
        if isinstance(value, dict) and "json" in value:
            value = value["json"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("CNG lieferte keine OBS-Chat-Token-Antwort.") from None

    if not isinstance(value, dict):
        raise RuntimeError("CNG lieferte eine ungültige OBS-Chat-Token-Antwort.")

    try:
        creator_id = int(value.get("creatorId"))
    except (TypeError, ValueError):
        raise RuntimeError("CNG-Tokenantwort enthält keine gültige Creator-ID.") from None
    if creator_id != int(expected_creator_id):
        raise RuntimeError("CNG-Tokenantwort gehört zu einer anderen Creator-ID.")

    obs_chat_token = str(value.get("token") or "").strip()
    presence_token = str(value.get("presenceToken") or "").strip()
    if not obs_chat_token:
        raise RuntimeError("CNG-Tokenantwort enthält keinen OBS-Chat-Token.")
    return obs_chat_token, presence_token


def cng_send_presence_heartbeat(creator_id, presence_token):
    """Send the same presence mutation used by CNG's OBS chat popout."""
    url = f"{CNG_BASE_URL}/api/trpc/streamChat.presenceHeartbeat?batch=1"
    payload = {"0": {"json": {"presenceToken": presence_token}}}
    response = requests.post(
        url,
        headers={
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Origin": CNG_BASE_URL,
            "Referer": f"{CNG_BASE_URL}/chat-popout/{creator_id}?mode=obs",
            "User-Agent": "Mozilla/5.0 CNG-Unified-Chat/8.0",
        },
        json=payload,
        timeout=15,
    )
    if response.status_code >= 400:
        error = RuntimeError(f"Presence-Heartbeat HTTP {response.status_code}")
        error.status_code = response.status_code
        raise error
    try:
        result = response.json()
    except ValueError:
        raise RuntimeError("Presence-Heartbeat lieferte eine ungültige Antwort.") from None
    status = _cng_trpc_error_status(result)
    if status is not None:
        error = RuntimeError(f"Presence-Heartbeat HTTP {status}")
        error.status_code = status
        raise error


def cng_alerts_url(creator_id):
    payload = {
        "0": {
            "json": {
                "creatorId": creator_id,
                "limit": 50
            }
        }
    }
    return (
        "https://cng-plattform.com/api/trpc/alerts.getRecentAlerts?batch=1&input="
        + quote(json.dumps(payload, separators=(",", ":")), safe="")
    )


def cng_fetch_alerts(creator_id, session, cng_ref):
    headers = {
        "Accept": "*/*",
        "User-Agent": "Mozilla/5.0 CNG-Unified-Chat/8.0",
        "Referer": f"https://cng-plattform.com/alert-overlay?creatorId={creator_id}",
        "Origin": "https://cng-plattform.com",
        "Cookie": f"cng_session={session}" + (f"; cng_ref={cng_ref}" if cng_ref else "")
    }
    req = urllib.request.Request(cng_alerts_url(creator_id), headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def cng_alert_settings(config, alert_type):
    """Return the configured media settings for one CNG alert type.

    The manager stores alert settings in ``alert_settings.json`` beside the
    bridge executable. They must travel with the SSE event because the browser
    overlay cannot read that local JSON file directly.
    """
    alert_type = str(alert_type or "unknown").strip().lower()
    alerts = config.get("alerts", {}) if isinstance(config, dict) else {}
    settings = alerts.get(alert_type, {}) if isinstance(alerts, dict) else {}
    if not isinstance(settings, dict):
        settings = {}

    # Manager and bridge run from the same package directory, but alert media
    # settings are deliberately stored separately from unified_chat_config.json.
    # Read them on demand so changes saved in the manager apply while the bridge
    # is already running. Retain the runtime-config values as a legacy fallback.
    try:
        with ALERT_CONFIG_FILE.open("r", encoding="utf-8") as f:
            stored_alerts = json.load(f)
        stored_settings = (
            stored_alerts.get(alert_type, {})
            if isinstance(stored_alerts, dict)
            else {}
        )
        if isinstance(stored_settings, dict):
            settings = {**settings, **stored_settings}
    except (OSError, json.JSONDecodeError):
        pass

    try:
        duration = int(settings.get("duration_seconds", 5))
    except (TypeError, ValueError):
        duration = 5

    return {
        "label": str(settings.get("label", alert_type.title()) or alert_type.title()).strip(),
        "gif": str(settings.get("gif", "") or "").strip(),
        "sound": str(settings.get("sound", "") or "").strip(),
        "duration_seconds": max(1, min(300, duration)),
    }


def cng_alert_normalize(alert, creator_id, config):
    avatar = alert.get("fromUserAvatar") or ""
    if avatar.startswith("/"):
        avatar = "https://cng-plattform.com" + avatar
    alert_type = str(alert.get("type") or "unknown").strip().lower()
    media = cng_alert_settings(config, alert_type)
    return {
        "event": "alert",
        "platform": "cng",
        "alertId": alert.get("id"),
        "creatorId": alert.get("creatorId", creator_id),
        "alertType": alert_type,
        "label": media["label"],
        "userId": alert.get("fromUserId"),
        "username": alert.get("fromUserName") or "Unbekannt",
        "amount": alert.get("amount"),
        "message": alert.get("message") or "",
        "shouldPlayTts": bool(alert.get("shouldPlayTts")),
        "isDisplayed": bool(alert.get("isDisplayed")),
        "timestamp": alert.get("createdAt"),
        "avatar": avatar,
        "gif": media["gif"],
        "sound": media["sound"],
        "duration_seconds": media["duration_seconds"],
    }


def cng_status_loop(config):
    """Keep CNG follower stats in sync using the legacy authenticated follower API.

    The old CNG ``creators.getByUsername`` status endpoint no longer returns the
    creator object expected by the bridge.  The legacy follower endpoint is
    authoritative for the follower count and is already used by the follower
    transition watcher, so do not make a second failing creator-status request.
    """
    creator_value = config.get("cng_creator_id")
    try:
        creator = int(creator_value)
        if creator <= 0:
            raise ValueError
    except (ValueError, TypeError):
        print("[CNG STATUS] Ungültige cng_creator_id in der Konfiguration.")
        return

    try:
        cookie = cng_creator_cookie(config)
    except Exception as e:
        print("[CNG STATUS] Deaktiviert:", repr(e))
        return

    print(f"[CNG STATUS] aktiv - creatorId={creator} | Quelle=creators.myFollowers")

    while True:
        try:
            followers = cng_fetch_followers(cookie)
            follower_count = len(followers)

            update_social_status(
                "cng",
                followers=follower_count,
                available=True,
            )

            with status_lock:
                current = dict(cng_status)

            current.update({
                "event": "stream_status",
                "platform": "cng",
                "username": str(config.get("cng_username", "")).strip(),
                "followerCount": follower_count,
                "updatedAt": time.time(),
                "error": None,
            })

            with status_lock:
                cng_status.update(current)

            print(f"[CNG STATUS] {follower_count} Follower")

        except Exception as e:
            print("[CNG STATUS] Fehler:", repr(e))
            with status_lock:
                current = dict(cng_status)
                current["updatedAt"] = time.time()
                current["error"] = str(e)
                cng_status.update(current)

        time.sleep(15.0)


def cng_fetch(
    creator_id,
    session,
    cng_ref,
    obs_chat_token=None,
    after_id=None,
    limit=50,
    creator_cookie=None,
):
    headers = {
        "Accept": "*/*",
        "User-Agent": "Mozilla/5.0 CNG-Unified-Chat/8.0",
        "Referer":
            f"https://cng-plattform.com/chat-popout/{creator_id}?mode=obs",
        "Origin": "https://cng-plattform.com",
    }
    if not obs_chat_token:
        headers["Cookie"] = creator_cookie or (
            f"cng_session={session}" +
            (f"; cng_ref={cng_ref}" if cng_ref else "")
        )

    req = urllib.request.Request(
        cng_url(
            creator_id,
            obs_chat_token=obs_chat_token,
            after_id=after_id,
            limit=limit,
        ),
        headers=headers,
        method="GET"
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
            if obs_chat_token:
                status = _cng_trpc_error_status(data)
                if status is not None:
                    error = RuntimeError(f"OBS-Chat-Abruf HTTP {status}")
                    error.status_code = status
                    raise error
            return data
    except urllib.error.HTTPError as exc:
        if obs_chat_token:
            # Never log an HTTPError directly here: its text includes the URL,
            # and the signed OBS token is part of the query string.
            error = RuntimeError(f"OBS-Chat-Abruf HTTP {exc.code}")
            error.status_code = exc.code
            raise error from None
        raise
    except urllib.error.URLError as exc:
        if obs_chat_token:
            raise RuntimeError(
                f"OBS-Chat-Abruf Netzwerkfehler ({type(exc.reason).__name__})"
            ) from None
        raise


def cng_presence_heartbeat_loop(creator_id, token_state):
    """Keep the CNG presence alive independently of chat polling."""
    logged_success = False
    token_updated = token_state["token_updated"]
    while True:
        with token_state["lock"]:
            token = token_state.get("presence_token") or token_state.get("obs_chat_token")

        if not token:
            token_updated.wait(15.0)
            token_updated.clear()
            continue

        try:
            cng_send_presence_heartbeat(creator_id, token)
            if not logged_success:
                print("[CNG PRESENCE] Heartbeat aktiv (60-Sekunden-Intervall).")
                logged_success = True
        except Exception as exc:
            status = getattr(exc, "code", None)
            if status in (401, 403) or "HTTP 401" in str(exc) or "HTTP 403" in str(exc):
                token_state["refresh_requested"].set()
            print("[CNG PRESENCE] Heartbeat-Fehler:", str(exc))

        token_updated.wait(60.0)
        token_updated.clear()


def cng_extract(data):
    try:
        value = data[0]["result"]["data"]

        if isinstance(value, dict) and "data" in value:
            value = value["data"]

        if isinstance(value, dict) and "json" in value:
            value = value["json"]

        return value if isinstance(value, list) else []

    except (KeyError, IndexError, TypeError):
        print("[CNG] Unerwartete Antwortstruktur")
        return []


def log_cng_message(msg):
    """Append every newly received CNG chat message to the CNG log file."""
    try:
        CNG_LOG_DIR.mkdir(parents=True, exist_ok=True)

        timestamp = msg.get("timestamp")
        if timestamp:
            try:
                dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                timestamp = dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError):
                timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        else:
            timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")

        username = str(msg.get("username") or "Unbekannt").replace("\\r", " ").replace("\\n", " ").strip()
        message = str(msg.get("message") or "").replace("\\r", " ").replace("\\n", " ").strip()

        with open(CNG_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {username}: {message}\\n")
    except Exception as e:
        # Logging must never stop the CNG chat loop.
        print("[CNG LOG] Fehler beim Schreiben:", repr(e))






def cng_normalize(m, creator_id):
    avatar = m.get("userAvatar") or ""

    if avatar.startswith("/"):
        avatar = "https://cng-plattform.com" + avatar

    badges = []

    if m.get("isModerator"):
        badges.append("MOD")

    if m.get("isCngOg"):
        badges.append("CNG OG")

    if m.get("isCngPioneer"):
        badges.append("PIONEER")

    return {
        "platform": "cng",
        "id": m.get("id"),
        "creatorId": m.get("creatorId", creator_id),
        "userId": m.get("userId"),
        "username":
            m.get("userDisplayName") or
            m.get("userLoginName") or
            "Unbekannt",
        "message": m.get("content") or "",
        "avatar": avatar,
        "timestamp": m.get("createdAt"),
        "badges": badges,
        "moderator": bool(m.get("isModerator")),
        "isCngOg": bool(m.get("isCngOg")),
        "isCngPioneer": bool(m.get("isCngPioneer")),
        "donation": bool(m.get("isDonation")),
        "coinAmount": m.get("coinAmount")
    }






# ============================================================
# COMMAND BOT BRIDGE
# ============================================================

LUMIA_TOKEN = ""
LUMIA_API_URL = f"http://localhost:39231/api/send?token={LUMIA_TOKEN}"


def load_lumiastream_settings(config=None):
    """Load Lumia settings from the Manager-owned command_bot.json file."""
    merged = {}
    fallback = (config or {}).get("lumiastream")
    if isinstance(fallback, dict):
        merged.update(fallback)

    try:
        saved = command_bot_config().get("lumiastream")
    except Exception:
        saved = None

    if isinstance(saved, dict):
        for key, value in saved.items():
            if value not in (None, ""):
                merged[key] = value
    return merged


def lumiastream_websocket_url(settings):
    """Derive Lumia's WebSocket endpoint from the configured REST API URL."""
    api_url = str(
        (settings or {}).get("api_url")
        or "http://localhost:39231/api/send"
    ).strip()
    parsed = urlparse(api_url)
    scheme = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}.get(
        parsed.scheme.lower()
    )
    if not scheme or not parsed.netloc:
        raise ValueError("Lumia API URL muss eine gültige HTTP(S)-Adresse sein.")

    path = parsed.path.rstrip("/")
    if path.endswith("/send"):
        path = path[:-len("/send")]
    if not path:
        path = "/api"

    token = str((settings or {}).get("token") or LUMIA_TOKEN).strip()
    return f"{scheme}://{parsed.netloc}{path}?token={quote(token, safe='')}"


def _find_lumia_variable(value, wanted_name="cng_bot_response"):
    """Best-effort recursive search for a Lumia variable update/value in an event."""
    if isinstance(value, dict):
        # Common explicit variable shapes. Keep this generic because Lumia event
        # payloads can vary by event type/version.
        name = value.get("name")
        if name == wanted_name:
            for key in ("value", "message", "data"):
                if key in value and not isinstance(value[key], (dict, list)):
                    return value[key]
            return value

        # Also support nested variable objects/maps such as {variables: {name: value}}.
        variables = value.get("variables")
        if isinstance(variables, dict) and wanted_name in variables:
            return variables[wanted_name]

        for child in value.values():
            found = _find_lumia_variable(child, wanted_name)
            if found is not None:
                return found

    elif isinstance(value, list):
        for child in value:
            found = _find_lumia_variable(child, wanted_name)
            if found is not None:
                return found

    return None


class LumiaWebSocketClient:
    """Persistent Lumia WebSocket client for commands and socket responses."""

    def __init__(self, config):
        self.config = config or {}
        self.ws = None
        self.lock = threading.RLock()
        self.receiver_thread = None
        self.stop_event = threading.Event()
        self.pending = {}
        self.pending_lock = threading.Lock()
        self.connected_event = threading.Event()

    def _settings(self):
        return load_lumiastream_settings(self.config)

    def _token(self):
        return str(self._settings().get("token") or LUMIA_TOKEN).strip()

    def _url(self):
        settings = self._settings()
        settings["token"] = self._token()
        return lumiastream_websocket_url(settings)

    def _complete_pending(self, context, response):
        with self.pending_lock:
            item = self.pending.get(str(context))
        if not item:
            return False
        item["response"] = response
        item["event"].set()
        return True

    def _receiver_loop(self):
        while not self.stop_event.is_set():
            try:
                with self.lock:
                    ws = self.ws
                if ws is None:
                    self.connected_event.wait(1.0)
                    continue

                raw = ws.recv()
                if raw is None:
                    raise RuntimeError("Lumia WebSocket beendet")

                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    print("[LUMIA WS] Nicht-JSON Event:", raw)
                    continue

                # Always show the raw event while we are testing the integration.
                print(
                    "[LUMIA WS EVENT]",
                    json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                )

                # socketapi:response belongs to a request sent with a context.
                if event.get("event") == "socketapi:response":
                    context = event.get("context")
                    if context is not None and self._complete_pending(context, event):
                        continue

                # Keep this generic diagnostic for any event that explicitly
                # contains the target variable.
                value = _find_lumia_variable(event, "cng_bot_response")
                if value is not None:
                    print("[LUMIA WS VARIABLE] cng_bot_response =", value)

            except Exception as e:
                if self.stop_event.is_set():
                    break
                print("[LUMIA WS] Verbindung/Fehler:", repr(e))
                with self.lock:
                    ws = self.ws
                    self.ws = None
                    self.connected_event.clear()
                if ws:
                    try:
                        ws.close()
                    except Exception:
                        pass
                time.sleep(5)

    def ensure_connected(self):
        if websocket is None:
            raise RuntimeError("websocket-client fehlt.")

        with self.lock:
            if self.ws and getattr(self.ws, "connected", False):
                return

            url = self._url()
            print("[LUMIA WS] Verbinde ...")
            ws = websocket.create_connection(url, timeout=30)
            self.ws = ws
            self.connected_event.set()
            print("[LUMIA WS] Verbunden")

            if self.receiver_thread is None or not self.receiver_thread.is_alive():
                self.receiver_thread = threading.Thread(
                    target=self._receiver_loop,
                    name="LumiaWebSocketReceiver",
                    daemon=True,
                )
                self.receiver_thread.start()

    def connect_forever(self):
        """Keep the Lumia socket alive and retry failed handshakes."""
        while not self.stop_event.is_set():
            try:
                self.ensure_connected()
            except Exception as exc:
                print("[LUMIA WS] Verbindung/Fehler:", repr(exc))
                self.stop_event.wait(5)
                continue

            while self.connected_event.is_set() and not self.stop_event.is_set():
                self.stop_event.wait(1)

    def request(self, command_type, params, timeout=10):
        self.ensure_connected()

        context = f"cng-lumia-{time.time_ns()}"
        waiter = threading.Event()
        entry = {"event": waiter, "response": None}

        with self.pending_lock:
            self.pending[context] = entry

        try:
            payload = {
                "type": command_type,
                "params": params,
                "context": context,
            }
            with self.lock:
                ws = self.ws
                if ws is None or not getattr(ws, "connected", False):
                    raise RuntimeError("Lumia WebSocket nicht verbunden")
                ws.send(json.dumps(payload, ensure_ascii=False))

            if not waiter.wait(timeout):
                raise TimeoutError(
                    f"Keine Lumia-Response für {command_type} innerhalb von {timeout}s"
                )

            return entry["response"]
        finally:
            with self.pending_lock:
                self.pending.pop(context, None)

    def get_variable_value(self, name, timeout=10):
        response = self.request(
            "get-variable-value",
            {"name": name},
            timeout=timeout,
        )
        if not isinstance(response, dict):
            return None

        # Lumia's documented response uses the "message" field for the result.
        value = response.get("message")
        print(f"[LUMIA WS RESPONSE] {name} = {value!r}")
        return value

    def set_variable_value(self, name, value, timeout=10):
        response = self.request(
            "update-variable-value",
            {"name": name, "value": value},
            timeout=timeout,
        )
        print(f"[LUMIA WS RESPONSE] {name} <- {value!r}")
        return response


lumia_ws_client = None
lumia_ws_client_lock = threading.Lock()


def get_lumia_ws_client(config):
    global lumia_ws_client
    with lumia_ws_client_lock:
        if lumia_ws_client is None:
            lumia_ws_client = LumiaWebSocketClient(config)
        else:
            lumia_ws_client.config = config
        return lumia_ws_client


def _obs_dpapi_decrypt(value):
    """Decrypt a Windows DPAPI value used by obs_settings.json."""
    if not value or not isinstance(value, str):
        return value
    if os.name != "nt":
        return value
    try:
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

        raw = base64.b64decode(value)
        buf = ctypes.create_string_buffer(raw)
        inp = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        out = DATA_BLOB()
        crypt32 = ctypes.windll.crypt32
        if not crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
            return value
        try:
            data = ctypes.string_at(out.pbData, out.cbData)
            return data.decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)
    except Exception:
        return value


class OBSWebSocketClient:
    """Persistent OBS Studio obs-websocket 5.x client.

    The Manager supplies the OBS connection settings in the ``obs`` section
    of the runtime configuration. The client connects independently from the
    chat platform loops and reconnects automatically when OBS is restarted or
    the WebSocket connection is lost.
    """

    def __init__(self, config):
        self.config = config or {}
        self.ws = None
        self.lock = threading.RLock()
        self.stop_event = threading.Event()

    def _settings(self):
        """Load OBS settings from the dedicated obs_settings.json."""
        settings_path = os.path.join(BASE_DIR, "obs_settings.json")

        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
            if isinstance(settings, dict):
                # obs_settings.json is the source of truth for host/port/enabled.
                # The Manager stores the OBS password there encrypted via DPAPI,
                # so prefer the already decrypted password from the runtime config
                # when available. Never print the password.
                runtime_obs = (self.config or {}).get("obs") or {}
                file_password = settings.get("password", "")
                runtime_password = runtime_obs.get("password", "")
                # Prefer the runtime value when it is already plaintext.
                # Otherwise decrypt the DPAPI value directly from obs_settings.json.
                if runtime_password:
                    runtime_password = _obs_dpapi_decrypt(str(runtime_password))
                    settings["password"] = runtime_password
                elif file_password:
                    settings["password"] = _obs_dpapi_decrypt(str(file_password))
                return settings
        except Exception as exc:
            print("[OBS WS] obs_settings.json konnte nicht gelesen werden:", repr(exc))

        # Backwards-compatible fallback.
        return (self.config or {}).get("obs") or {}

    def _uri(self):
        s = self._settings()
        host = str(s.get("host", "127.0.0.1")).strip() or "127.0.0.1"
        try:
            port = int(s.get("port", 4455))
        except (TypeError, ValueError):
            port = 4455
        return f"ws://{host}:{port}"

    def close(self):
        with self.lock:
            ws = self.ws
            self.ws = None
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass

    @staticmethod
    def _authentication(password, salt, challenge):
        secret = base64.b64encode(
            hashlib.sha256((password + salt).encode("utf-8")).digest()
        ).decode("ascii")
        return base64.b64encode(
            hashlib.sha256((secret + challenge).encode("utf-8")).digest()
        ).decode("ascii")

    def _request(self, request_type, request_data=None, timeout=10):
        request_id = f"cng-obs-{time.time_ns()}"
        payload = {
            "op": 6,
            "d": {
                "requestType": request_type,
                "requestId": request_id,
            }
        }
        if request_data is not None:
            payload["d"]["requestData"] = request_data

        with self.lock:
            ws = self.ws
            if not ws or not getattr(ws, "connected", False):
                raise RuntimeError("OBS WebSocket ist nicht verbunden.")
            ws.send(json.dumps(payload))

            deadline = time.time() + timeout
            while time.time() < deadline:
                raw = ws.recv()
                if not raw:
                    continue
                obj = json.loads(raw)
                if obj.get("op") != 7:
                    continue
                data = obj.get("d") or {}
                if str(data.get("requestId")) != request_id:
                    continue
                status = data.get("requestStatus") or {}
                if not status.get("result", False):
                    raise RuntimeError(
                        "OBS Request fehlgeschlagen: "
                        + str(status.get("comment") or status)
                    )
                return data.get("responseData") or {}

        raise TimeoutError(f"OBS Timeout bei {request_type}.")

    def connect(self):
        if websocket is None:
            raise RuntimeError(
                "websocket-client fehlt. Bitte websocket-client installieren."
            )

        s = self._settings()
        if not bool(s.get("enabled", True)):
            return False

        with self.lock:
            if self.ws and getattr(self.ws, "connected", False):
                return True

            self.close()
            uri = self._uri()
            print(f"[OBS WS] Verbinde mit {uri} ...")
            ws = websocket.create_connection(
                uri,
                timeout=10,
            )
            ws.settimeout(10)

            raw = ws.recv()
            if not raw:
                ws.close()
                raise RuntimeError("OBS: keine Hello-Nachricht erhalten.")

            hello = json.loads(raw)
            if hello.get("op") != 0:
                ws.close()
                raise RuntimeError(
                    "OBS: unerwartete erste Nachricht: "
                    + str(hello.get("op"))
                )

            hello_data = hello.get("d") or {}
            identify = {
                "op": 1,
                "d": {
                    "rpcVersion": int(hello_data.get("rpcVersion", 1))
                }
            }

            auth = hello_data.get("authentication")
            if auth:
                password = str(s.get("password", ""))
                if not password:
                    ws.close()
                    raise RuntimeError(
                        "OBS verlangt Authentifizierung, aber kein Passwort ist konfiguriert."
                    )
                identify["d"]["authentication"] = self._authentication(
                    password,
                    str(auth.get("salt", "")),
                    str(auth.get("challenge", "")),
                )

            rpc_version = identify["d"].get("rpcVersion")
            authentication_configured = bool(identify["d"].get("authentication"))
            authentication_status = "ja" if authentication_configured else "nein"
            print(
                "[OBS WS] Identify gesendet | "
                f"rpcVersion={rpc_version} | "
                f"Authentifizierung={authentication_status}"
            )
            ws.send(json.dumps(identify))

            identify_raw = ws.recv()
            if not identify_raw:
                close_code = getattr(ws, "close_status", None)
                close_reason = getattr(ws, "close_reason", None)
                ws.close()
                raise RuntimeError(
                    "OBS: Verbindung wurde nach Identify geschlossen "
                    f"(close_code={close_code!r}, close_reason={close_reason!r}, "
                    f"password_configured={authentication_configured})."
                )

            try:
                identified = json.loads(identify_raw)
            except Exception as exc:
                ws.close()
                raise RuntimeError(
                    "OBS: Ungültige Identify-Antwort: " + repr(identify_raw)
                ) from exc

            if identified.get("op") != 2:
                ws.close()
                raise RuntimeError(
                    "OBS: Identify fehlgeschlagen: " + json.dumps(identified, ensure_ascii=False)
                )

            self.ws = ws

            version = self._request("GetVersion", timeout=10)
            print(
                "[OBS WS] Verbunden"
                + (
                    f" | OBS {version.get('obsVersion')}"
                    if version.get("obsVersion") else ""
                )
                + (
                    f" | obs-websocket {version.get('obsWebSocketVersion')}"
                    if version.get("obsWebSocketVersion") else ""
                )
            )
            return True

    def get_current_program_scene(self):
        """Return the name of the currently active OBS program scene."""
        data = self._request("GetCurrentProgramScene", timeout=10)
        scene_name = str(data.get("currentProgramSceneName") or "").strip()
        if not scene_name:
            raise RuntimeError("OBS: keine aktuelle Program-Szene erhalten.")
        return scene_name

    def is_streaming(self):
        """Return whether OBS currently has an active stream output."""
        data = self._request("GetStreamStatus", timeout=10)
        return bool(data.get("outputActive", False))

    def trigger_key_sequence(self, key_id, *, alt=False, control=False, shift=False, command=False):
        """Trigger an OBS hotkey by its key sequence (obs-websocket 5.x)."""
        return self._request(
            "TriggerHotkeyByKeySequence",
            {
                "keyId": str(key_id),
                "keyModifiers": {
                    "alt": bool(alt),
                    "control": bool(control),
                    "shift": bool(shift),
                    "command": bool(command),
                },
            },
            timeout=10,
        )

    def run_forever(self):
        """Keep the OBS connection alive and reconnect after disconnects."""
        if websocket is None:
            print("[OBS WS] nicht gestartet - websocket-client fehlt.")
            return

        if not bool(self._settings().get("enabled", True)):
            print("[OBS WS] deaktiviert.")
            return

        while not self.stop_event.is_set():
            try:
                self.connect()
                print("[OBS WS] Verbindung aktiv.")

                # Keep the socket alive. Screenshot capture is intentionally not
                # handled by the Bridge; the platform provides its own thumbnail.
                while not self.stop_event.is_set():
                    with self.lock:
                        ws = self.ws
                    if not ws or not getattr(ws, "connected", False):
                        raise RuntimeError("OBS WebSocket wurde beendet.")
                    try:
                        ws.ping()
                    except Exception as exc:
                        raise RuntimeError("OBS Ping fehlgeschlagen: " + str(exc))
                    self.stop_event.wait(15.0)

            except Exception as exc:
                print("[OBS WS] Verbindung/Fehler:", repr(exc))
                self.close()
                if not self.stop_event.is_set():
                    self.stop_event.wait(5.0)

    def stop(self):
        self.stop_event.set()
        self.close()


obs_ws_client = None
obs_ws_client_lock = threading.Lock()


def get_obs_ws_client(config):
    global obs_ws_client
    with obs_ws_client_lock:
        if obs_ws_client is None:
            obs_ws_client = OBSWebSocketClient(config)
        else:
            obs_ws_client.config = config
        return obs_ws_client


class StreamerBotClient:
    """Small synchronous Streamer.bot WebSocket client for CNG commands."""

    def __init__(self, config):
        self.config = config or {}
        self.ws = None
        self.lock = threading.RLock()

    def _settings(self):
        return self.config.get("streamerbot") or {}

    def _uri(self):
        s = self._settings()
        scheme = str(s.get("scheme", "ws")).strip() or "ws"
        host = str(s.get("host", "127.0.0.1")).strip() or "127.0.0.1"
        port = int(s.get("port", 8080))
        endpoint = str(s.get("endpoint", "/")).strip() or "/"
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        return f"{scheme}://{host}:{port}{endpoint}"

    def close(self):
        with self.lock:
            ws = self.ws
            self.ws = None
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass

    @staticmethod
    def _auth_value(password, salt, challenge):
        secret = base64.b64encode(
            hashlib.sha256(
                (password + salt).encode("utf-8")
            ).digest()
        ).decode("ascii")

        return base64.b64encode(
            hashlib.sha256(
                (secret + challenge).encode("utf-8")
            ).digest()
        ).decode("ascii")

    def connect(self):
        if websocket is None:
            raise RuntimeError(
                "websocket-client fehlt. Bitte websocket-client installieren."
            )

        s = self._settings()

        with self.lock:
            if self.ws and getattr(self.ws, "connected", False):
                return True

            self.close()

            uri = self._uri()
            timeout = float(s.get("timeout", 10))

            print(f"[STREAMER.BOT] Verbinde mit {uri} ...")

            ws = websocket.create_connection(
                uri,
                timeout=timeout
            )

            # Streamer.bot sends Hello immediately after connecting.
            raw = ws.recv()
            if not raw:
                ws.close()
                raise RuntimeError("Streamer.bot: keine Hello-Nachricht erhalten.")

            hello = json.loads(raw)

            if hello.get("request") != "Hello":
                ws.close()
                raise RuntimeError(
                    "Streamer.bot: unerwartete erste Nachricht: "
                    + str(hello.get("request"))
                )

            auth = hello.get("authentication")
            password = str(s.get("password", ""))

            if auth:
                if not password:
                    ws.close()
                    raise RuntimeError(
                        "Streamer.bot verlangt Authentifizierung, aber kein Passwort ist konfiguriert."
                    )

                auth_value = self._auth_value(
                    password,
                    str(auth.get("salt", "")),
                    str(auth.get("challenge", ""))
                )

                request_id = f"cng-auth-{time.time_ns()}"

                ws.send(json.dumps({
                    "request": "Authenticate",
                    "id": request_id,
                    "authentication": auth_value
                }))

                while True:
                    response = json.loads(ws.recv())

                    if response.get("id") == request_id:
                        if response.get("status") != "ok":
                            ws.close()
                            raise RuntimeError(
                                "Streamer.bot Authentifizierung fehlgeschlagen: "
                                + str(response)
                            )
                        break

            self.ws = ws

            info = hello.get("info") or {}
            print(
                "[STREAMER.BOT] Verbunden"
                + (
                    f" | Version {info.get('version')}"
                    if info.get("version") else ""
                )
            )

            return True

    def do_action(self, command, message, username, user_id=None):
        s = self._settings()
        action_name = str(
            s.get("action_name", "CNG Command Router")
        ).strip()

        if not action_name:
            raise RuntimeError(
                "Keine Streamer.bot Action konfiguriert."
            )

        with self.lock:
            try:
                self.connect()

                request_id = f"cng-action-{time.time_ns()}"

                args = {
                    "command": command,
                    "message": message or "",
                    "username": username or "",
                    "userName": username or "",
                    "platform": "cng",
                    "userId": str(user_id or "")
                }

                self.ws.send(json.dumps({
                    "request": "DoAction",
                    "id": request_id,
                    "action": {
                        "name": action_name
                    },
                    "args": args
                }))

                # DoAction normally returns a response. Read until ours arrives.
                deadline = time.time() + float(s.get("timeout", 10))

                while time.time() < deadline:
                    raw = self.ws.recv()
                    if raw is None:
                        raise RuntimeError(
                            "Streamer.bot WebSocket wurde beendet."
                        )

                    response = json.loads(raw)

                    if response.get("id") != request_id:
                        continue

                    if response.get("status") != "ok":
                        raise RuntimeError(
                            "Streamer.bot DoAction fehlgeschlagen: "
                            + str(response)
                        )

                    print(
                        f"[STREAMER.BOT] Action '{action_name}' ausgeführt"
                        f" | command=!{command}"
                        + (
                            f" | message={message}"
                            if message else ""
                        )
                        + f" | user={username}"
                    )
                    return True

                raise TimeoutError(
                    "Timeout beim Warten auf Streamer.bot DoAction."
                )

            except Exception:
                # Drop the connection so the next command reconnects cleanly.
                ws = self.ws
                self.ws = None
                if ws:
                    try:
                        ws.close()
                    except Exception:
                        pass
                raise


streamerbot_client = None
streamerbot_client_lock = threading.Lock()


def get_streamerbot_client(config):
    global streamerbot_client

    with streamerbot_client_lock:
        if streamerbot_client is None:
            streamerbot_client = StreamerBotClient(config)
        else:
            streamerbot_client.config = config

        return streamerbot_client


def load_accounts_file():
    """Load the shared accounts.json used by the Manager/Bridge."""
    accounts_file = BASE_DIR / "accounts.json"
    with open(accounts_file, encoding="utf-8") as f:
        data = json.load(f)
    accounts = data.get("accounts", [])
    return accounts if isinstance(accounts, list) else []


def find_cng_account(accounts, display_name=None, channel=None):
    """Find a CNG account by display name or channel."""
    wanted_display = str(display_name or "").strip().lower()
    wanted_channel = str(channel or "").strip().lower()
    for account in accounts:
        if str(account.get("platform", "")).strip().lower() != "cng":
            continue
        if wanted_display and str(account.get("display_name", "")).strip().lower() == wanted_display:
            return account
        if wanted_channel and str(account.get("channel", "")).strip().lower() == wanted_channel:
            return account
        # No filter supplied: return the first CNG account.
        if not wanted_display and not wanted_channel:
            return account
    return None


def _cng_account_session(account):
    """Return the decrypted CNG session from an accounts.json entry.

    CNG credentials are stored under auth.cng_session and are DPAPI-encrypted.
    The Beta authenticated APIs require the decrypted value as the
    __Host-cng_session cookie.
    """
    if not isinstance(account, dict):
        return ""
    auth = account.get("auth")
    if isinstance(auth, dict):
        value = auth.get("cng_session")
        if value:
            return str(_obs_dpapi_decrypt(str(value))).strip()
    # Compatibility with older/plaintext account layouts.
    for key in ("cng_session", "session"):
        value = account.get(key)
        if value:
            return str(value).strip()
    return ""


def find_tiktok_account(accounts):
    """Find the TikTok account and username from the shared accounts.json."""
    for account in accounts:
        if str(account.get("platform", "")).strip().lower() != "tiktok":
            continue

        channel = str(account.get("channel", "")).strip()
        display_name = str(account.get("display_name", "")).strip()
        unique_id = channel or display_name

        if unique_id:
            return account, unique_id

    return None, None


def command_bot_config():
    """Load command-bot settings exclusively from command_bot.json."""
    try:
        with open(COMMAND_BOT_CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise RuntimeError("command_bot.json fehlt.")
    except (OSError, ValueError, json.JSONDecodeError) as e:
        raise RuntimeError(f"command_bot.json konnte nicht geladen werden: {e}")

    if not isinstance(data, dict):
        raise RuntimeError("command_bot.json muss ein JSON-Objekt enthalten.")
    return data


def load_selected_command_bot():
    """Return the selected command bot; missing/invalid values default to intern."""
    data = command_bot_config()
    bot = str(data.get("command_bot") or "").strip().lower()
    if bot not in {"intern", "lumiastream", "streamerbot"}:
        bot = "intern"
    return bot


def cng_bot_credentials(config=None):
    """Read CNG-Bot credentials exclusively from command_bot.json/intern."""
    data = command_bot_config()
    bot = data.get("intern") or {}
    if not isinstance(bot, dict):
        raise RuntimeError("command_bot.json: 'intern' muss ein Objekt sein.")

    cookie = str(bot.get("cookie") or "").strip()
    if not cookie:
        session = str(bot.get("cng_session") or "").strip()
        if session:
            cookie = session if session.lower().startswith("cng_session=") else f"cng_session={session}"

    if not cookie:
        raise RuntimeError("command_bot.json: Intern-Bot enthält keine CNG-Session.")

    user_id = bot.get("user_id")
    display_name = str(bot.get("account_name") or "CNG-Bot").strip()
    return {"display_name": display_name, "user_id": user_id}, cookie, user_id


def cng_target_creator_id(config):
    """Resolve the current CNG chatroom from the CNG account in accounts.json."""
    accounts = load_accounts_file()
    configured_channel = str((config or {}).get("cng_channel") or (config or {}).get("cng_username") or "").strip()
    target = find_cng_account(accounts, channel=configured_channel) if configured_channel else None
    if not target:
        configured_creator = (config or {}).get("cng_creator_id")
        try:
            creator = int(configured_creator)
        except (TypeError, ValueError):
            creator = None
        if creator is not None:
            for account in accounts:
                if str(account.get("platform", "")).strip().lower() != "cng":
                    continue
                try:
                    if int(account.get("creator_id")) == creator:
                        target = account
                        break
                except (TypeError, ValueError):
                    continue
    if not target:
        raise RuntimeError("Kein CNG-Zielaccount in accounts.json gefunden.")

    try:
        creator_id = int(target.get("creator_id"))
    except (TypeError, ValueError):
        raise RuntimeError("CNG-Zielaccount enthält keine gültige creator_id.")
    if creator_id <= 0:
        raise RuntimeError("CNG-Zielaccount enthält keine gültige creator_id.")
    return creator_id, target


def cng_bot_send_message(content, config=None):
    """Send text to the configured CNG chat as the CNG-Bot account."""
    text = str(content or "").strip()
    if not text:
        return False

    bot, cookie, user_id = cng_bot_credentials(config or {})
    creator_id, target = cng_target_creator_id(config or {})

    payload = {
        "0": {
            "json": {
                "creatorId": creator_id,
                "content": text,
                "obsChatToken": None,
            },
            "meta": {
                "values": {
                    "obsChatToken": ["undefined"]
                }
            }
        }
    }

    url = "https://cng-plattform.com/api/trpc/streamChat.sendMessage?batch=1"
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": "https://cng-plattform.com",
        "Referer": f"https://cng-plattform.com/chat-popout/{creator_id}?mode=obs",
        "User-Agent": "Mozilla/5.0 CNG-Unified-Chat/8.0",
        "Cookie": cookie,
    }

    response = requests.post(url, headers=headers, json=payload, timeout=10)
    if response.status_code >= 400:
        raise RuntimeError(
            f"CNG-Bot sendMessage HTTP {response.status_code}: {response.text[:500]}"
        )

    print(
        f"[CNG BOT] Nachricht gesendet | bot={bot.get('display_name')}"
        f" | userId={user_id} | creatorId={creator_id}"
        f" | channel={target.get('display_name') or target.get('channel')}"
        f" | HTTP {response.status_code} | {text!r}"
    )
    return True


def send_cng_command_to_lumia(message, username, config=None):
    """Forward only CNG messages beginning with ! to LumiaStream."""

    if not isinstance(message, str):
        return

    message = message.strip()

    if not message.startswith("!"):
        return

    command_text = message[1:].strip()

    if not command_text:
        return

    parts = command_text.split(None, 1)
    command = parts[0]
    optional_message = parts[1] if len(parts) > 1 else ""

    params = {
        "value": command,
        "username": username
    }

    if optional_message:
        params["extraSettings"] = {
            "message": optional_message
        }

    lumiastream = load_lumiastream_settings(config)
    api_url = str(
        lumiastream.get("api_url") or
        "http://localhost:39231/api/send"
    ).strip()
    token = str(
        lumiastream.get("token") or
        LUMIA_TOKEN
    ).strip()

    separator = "&" if "?" in api_url else "?"
    url = f"{api_url}{separator}token={quote(token, safe='')}"

    try:
        response = requests.post(
            url,
            json={
                "type": "chat-command",
                "params": params
            },
            timeout=10
        )

        if not response.ok:
            response_body = str(response.text or "").strip()
            if token:
                response_body = response_body.replace(token, "<redacted>")
            response_body = response_body[:1000]
            print(
                f"[LUMIA] CNG Command abgelehnt: !{command}"
                f" | HTTP {response.status_code}"
                f" | Antwort: {response_body!r}"
            )
            return

        print(
            f"[LUMIA] CNG Command gesendet: !{command}"
            + (
                f" | Message: {optional_message}"
                if optional_message else ""
            )
            + f" | HTTP {response.status_code}"
        )

        # Lumia schreibt die fertige Chatbot-Antwort in diese Variable.
        # Nach dem Auslesen wird sie wieder geleert, damit keine alte Antwort
        # beim nächsten CNG-Befehl erneut verwendet werden kann.
        time.sleep(0.2)
        client = None
        bot_response = None
        try:
            client = get_lumia_ws_client(config)
            bot_response = client.get_variable_value("cng_bot_response", timeout=5)

            if bot_response is not None and str(bot_response).strip():
                try:
                    cng_bot_send_message(bot_response, config)
                except Exception as e:
                    print("[CNG BOT] Nachricht konnte nicht an CNG gesendet werden:", repr(e))
            else:
                print("[CNG BOT] cng_bot_response ist leer - keine Nachricht an CNG.")

        except Exception as e:
            print("[CNG BOT] Lumia-Response konnte nicht ausgelesen werden:", repr(e))
        finally:
            # Die Variable wird IMMER nach dem Auslesen geleert - auch wenn
            # das Senden an CNG mit einem Fehler (z.B. HTTP 401) scheitert.
            if client is not None:
                try:
                    client.set_variable_value("cng_bot_response", "", timeout=5)
                except Exception as e:
                    print("[CNG BOT] cng_bot_response konnte NICHT geleert werden:", repr(e))

    except Exception as e:
        print("[LUMIA] CNG Command Fehler:", repr(e))



def send_cng_command_to_streamerbot(message, username, user_id, config):
    """Forward a CNG !command to the configured Streamer.bot action."""

    if not isinstance(message, str):
        return

    message = message.strip()

    if not message.startswith("!"):
        return

    command_text = message[1:].strip()

    if not command_text:
        return

    parts = command_text.split(None, 1)
    command = parts[0]
    optional_message = parts[1] if len(parts) > 1 else ""

    try:
        client = get_streamerbot_client(config)
        client.do_action(
            command=command,
            message=optional_message,
            username=username,
            user_id=user_id
        )
    except Exception as e:
        print("[STREAMER.BOT] CNG Command Fehler:", repr(e))


def cng_format_followage(followed_at, now=None):
    """Return a human-readable German follow duration from an ISO timestamp."""
    raw = str(followed_at or "").strip()
    if not raw:
        return None

    try:
        followed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None

    if followed.tzinfo is None:
        followed = followed.replace(tzinfo=timezone.utc)

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    if followed > current:
        # Protect against bad/future snapshot data.
        return "seit dem " + followed.astimezone().strftime("%d.%m.%Y")

    years = current.year - followed.year
    anniversary = followed.replace(year=current.year)
    if anniversary > current:
        years -= 1

    months = current.month - followed.month
    if current.day < followed.day:
        months -= 1
    months %= 12

    try:
        if months:
            month_anchor = anniversary.replace(month=((anniversary.month - 1 + months) % 12) + 1)
            if month_anchor > current:
                months -= 1
                month_anchor = anniversary
        else:
            month_anchor = anniversary
    except ValueError:
        # Handle dates such as February 29 safely.
        month_anchor = current

    # Calculate remaining days from the last complete year/month boundary.
    base = followed
    try:
        base = followed.replace(year=followed.year + years)
    except ValueError:
        base = followed.replace(year=followed.year + years, day=28)

    target_month = base.month + months
    target_year = base.year + (target_month - 1) // 12
    target_month = ((target_month - 1) % 12) + 1
    try:
        base = base.replace(year=target_year, month=target_month)
    except ValueError:
        # End-of-month fallback (e.g. 31st -> shorter month).
        import calendar
        base = base.replace(
            year=target_year,
            month=target_month,
            day=min(base.day, calendar.monthrange(target_year, target_month)[1])
        )

    days = max(0, (current.date() - base.date()).days)

    parts = []
    if years:
        year_label = "Jahr" if years == 1 else "Jahre"
        parts.append(f"{years} {year_label}")
    if months:
        month_label = "Monat" if months == 1 else "Monate"
        parts.append(f"{months} {month_label}")
    if days or not parts:
        day_label = "Tag" if days == 1 else "Tage"
        parts.append(f"{days} {day_label}")

    return ", ".join(parts)


def cng_get_socials():
    """Build the social-links response from accounts.json.

    Only platforms with a configured non-empty channel/username are included.
    """
    accounts = load_accounts_file()

    platform_order = ("kick", "twitch", "tiktok", "youtube", "cng")
    labels = {
        "kick": "Kick",
        "twitch": "Twitch",
        "tiktok": "TikTok",
        "youtube": "YouTube",
        "cng": "CNG",
    }

    urls = {}
    for account in accounts:
        if not isinstance(account, dict):
            continue

        platform = str(account.get("platform") or "").strip().lower()
        if platform not in platform_order or platform in urls:
            continue

        channel = str(account.get("channel") or "").strip()
        if not channel:
            channel = str(account.get("display_name") or "").strip()
        if not channel:
            continue

        clean = channel.lstrip("@").strip()

        if platform == "kick":
            url = channel if channel.lower().startswith(("http://", "https://")) else f"https://kick.com/{clean}"
        elif platform == "twitch":
            url = channel if channel.lower().startswith(("http://", "https://")) else f"https://twitch.tv/{clean}"
        elif platform == "tiktok":
            url = channel if channel.lower().startswith(("http://", "https://")) else f"https://www.tiktok.com/@{clean}"
        elif platform == "youtube":
            if channel.lower().startswith(("http://", "https://")):
                url = channel
            elif channel.startswith("@"):
                url = f"https://www.youtube.com/{channel}"
            else:
                url = f"https://www.youtube.com/@{clean}"
        else:  # cng
            url = channel if channel.lower().startswith(("http://", "https://")) else f"https://cng-plattform.com/{clean}"

        urls[platform] = url

    return [(labels[p], urls[p]) for p in platform_order if p in urls]


def cng_get_followage(username):
    """Look up a CNG user's follow timestamp from the local follower snapshot."""
    wanted = str(username or "").strip().lstrip("@").casefold()
    if not wanted:
        return None

    if not CNG_FOLLOWERS_FILE.exists():
        print(f"[INTERN BOT] Follower-Datei fehlt: {CNG_FOLLOWERS_FILE}")
        return None

    try:
        with open(CNG_FOLLOWERS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print("[INTERN BOT] cng_followers.json konnte nicht geladen werden:", repr(e))
        return None

    followers = data.get("followers") if isinstance(data, dict) else data
    if not isinstance(followers, list):
        print("[INTERN BOT] cng_followers.json enthält keine Follower-Liste.")
        return None

    for follower in followers:
        if not isinstance(follower, dict):
            continue
        names = (
            follower.get("name"),
            follower.get("displayName"),
        )
        if any(str(name or "").strip().casefold() == wanted for name in names):
            followed_at = follower.get("followedAt")
            duration = cng_format_followage(followed_at)
            return {
                "name": str(follower.get("displayName") or follower.get("name") or username).strip(),
                "followed_at": followed_at,
                "duration": duration,
            }

    return None


# Internal command cooldowns: each user gets an independent cooldown per command.
INTERN_COMMAND_COOLDOWNS = {
    "lurk": 10.0,
    "followage": 15.0,
    "socials": 30.0,
}

_intern_command_cooldowns = {}
_intern_command_cooldown_lock = threading.Lock()


def intern_command_on_cooldown(username, command):
    """Return remaining cooldown seconds for user+command, or 0.0."""
    cooldown = float(INTERN_COMMAND_COOLDOWNS.get(command, 0.0) or 0.0)
    if cooldown <= 0:
        return 0.0

    key = (str(username or "User").strip().lower(), str(command or "").strip().lower())
    now = time.monotonic()

    with _intern_command_cooldown_lock:
        expires_at = _intern_command_cooldowns.get(key, 0.0)
        if expires_at > now:
            return expires_at - now
        _intern_command_cooldowns.pop(key, None)
        return 0.0


def intern_command_start_cooldown(username, command):
    """Start the configured cooldown for user+command."""
    cooldown = float(INTERN_COMMAND_COOLDOWNS.get(command, 0.0) or 0.0)
    if cooldown <= 0:
        return

    key = (str(username or "User").strip().lower(), str(command or "").strip().lower())
    with _intern_command_cooldown_lock:
        _intern_command_cooldowns[key] = time.monotonic() + cooldown


def send_cng_command_to_intern(message, username, config=None):
    """Handle internal CNG commands without LumiaStream or Streamer.bot."""
    if not isinstance(message, str):
        return
    message = message.strip()
    if not message.startswith("!"):
        return

    command = message[1:].strip().split(None, 1)[0].lower() if message[1:].strip() else ""
    username = str(username or "User").strip() or "User"

    if command not in INTERN_COMMAND_COOLDOWNS:
        return

    remaining = intern_command_on_cooldown(username, command)
    if remaining > 0:
        print(
            f"[INTERN BOT] Cooldown aktiv | command=!{command} | "
            f"user=@{username} | noch {remaining:.1f}s"
        )
        return

    if command == "lurk":
        response = f"Alles klar, @{username}! Wir wissen Bescheid! Danke für deinen Lurk! 👀"
    elif command == "socials":
        socials = cng_get_socials()
        if socials:
            response = " | ".join(f"{label}: {url}" for label, url in socials)
        else:
            response = "Aktuell sind keine Social-Media-Konten in accounts.json hinterlegt."
    elif command == "followage":
        follower = cng_get_followage(username)
        if follower is None:
            response = (
                f"@{username}, du musst dem Kanal erst folgen, "
                f"bevor ich deine Followage anzeigen kann. ❤️"
            )
        elif not follower.get("duration"):
            response = (
                f"@{username}, du folgst dem Kanal seit dem "
                f"{str(follower.get('followed_at')).replace('T', ' ')[:19]}."
            )
        else:
            followed_dt = datetime.fromisoformat(
                str(follower["followed_at"]).replace("Z", "+00:00")
            )
            response = (
                f"@{username}, du folgst dem Kanal seit dem "
                f"{followed_dt.astimezone().strftime('%d.%m.%Y')} "
                f"({follower['duration']}). ❤️"
            )
    else:
        return

    print(f"[INTERN BOT] Antwort: {response}")

    # Start the cooldown as soon as the command is accepted, not only after
    # a successful CNG send. This also protects CNG from command spam while
    # the platform is timing out or returning HTTP errors.
    intern_command_start_cooldown(username, command)

    try:
        cng_bot_send_message(response, config)
    except Exception as e:
        print("[INTERN BOT] CNG Command Fehler:", repr(e))


def send_cng_command_to_bot(msg, config):
    """Dispatch CNG commands using only the selection from command_bot.json."""
    message = msg.get("message", "")
    username = msg.get("username", "")
    user_id = msg.get("userId")

    if not isinstance(message, str) or not message.strip().startswith("!"):
        return

    try:
        bot = load_selected_command_bot()
    except Exception as e:
        print("[COMMAND BOT] command_bot.json konnte nicht gelesen werden:", repr(e))
        bot = "intern"

    print(f"[COMMAND BOT] Ausgewählter Bot: {bot}")

    if bot == "intern":
        send_cng_command_to_intern(message, username, config)
    elif bot == "streamerbot":
        send_cng_command_to_streamerbot(message, username, user_id, config)
    else:
        send_cng_command_to_lumia(message, username, config)


def cng_creator_cookie(config=None):
    """Return a legacy-compatible CNG creator cookie.

    The old CNG tRPC endpoints expect the legacy ``cng_session`` cookie.
    The current Manager may store either a raw session value or the newer
    ``__Host-cng_session`` representation, so normalize both forms here.
    The dedicated cookie file remains preferred for compatibility with the
    old bridge; accounts.json is used as a fallback.
    """
    raw_cookie = ""

    if CNG_CREATOR_COOKIE_FILE.exists():
        try:
            with open(CNG_CREATOR_COOKIE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            raise RuntimeError(
                f"{CNG_CREATOR_COOKIE_FILE.name} konnte nicht geladen werden: {e}"
            )
        if isinstance(data, dict):
            raw_cookie = str(data.get("cookie") or "").strip()

    if not raw_cookie:
        raw_session = str((config or {}).get("cng_session") or "").strip()
        if not raw_session:
            raise RuntimeError(
                "Keine CNG Creator-Session verfügbar (cng_creator_cookie.json / accounts.json)."
            )
        raw_cookie = raw_session

    # Already a complete legacy cookie.
    if raw_cookie.lower().startswith("cng_session="):
        return raw_cookie

    # Convert the newer host-only representation to the legacy cookie name.
    if raw_cookie.lower().startswith("__host-cng_session="):
        raw_cookie = raw_cookie.split("=", 1)[1].strip()

    # If a cookie string contains additional attributes, extract the session
    # value and retain an optional cng_ref from the runtime config.
    if "=" in raw_cookie and ";" in raw_cookie:
        parts = [x.strip() for x in raw_cookie.split(";") if x.strip()]
        values = {}
        for part in parts:
            if "=" in part:
                key, value = part.split("=", 1)
                values[key.strip().lower()] = value.strip()
        session = values.get("cng_session") or values.get("__host-cng_session")
        if session:
            ref = values.get("cng_ref") or str((config or {}).get("cng_ref") or "").strip()
            return "cng_session=" + session + ("; cng_ref=" + ref if ref else "")

    ref = str((config or {}).get("cng_ref") or "").strip()
    return "cng_session=" + raw_cookie + ("; cng_ref=" + ref if ref else "")


def cng_fetch_followers(cookie):
    """Fetch the authenticated creator's follower list from CNG."""
    headers = {
        "Accept": "*/*",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
        "Referer": "https://cng-plattform.com/profile",
        "Origin": "https://cng-plattform.com",
        "Cookie": cookie,
    }
    req = urllib.request.Request(CNG_FOLLOWERS_URL, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"CNG-Follower-API HTTP {exc.code}: {body}") from exc

    value = data
    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, dict) and "result" in value:
        value = value["result"].get("data")
    if isinstance(value, dict) and "data" in value:
        value = value["data"]
    if isinstance(value, dict) and "json" in value:
        value = value["json"]
    if not isinstance(value, list):
        raise RuntimeError("CNG-Followerantwort enthält keine Liste.")
    return value

def _cng_follower_identity_key(follower):
    """Return a stable identity for one authoritative CNG follower entry.

    userId is preferred because display names can change. Username is the
    fallback used by the follower API when no userId is present.
    """
    if not isinstance(follower, dict):
        return ""

    for field in ("userId", "user_id", "id"):
        value = follower.get(field)
        if value not in (None, ""):
            return "id:" + str(value).strip()

    for field in ("username", "name"):
        value = follower.get(field)
        if value not in (None, ""):
            return "user:" + str(value).strip().lstrip("@").lower()

    return ""


def _cng_history_timestamp(value=None):
    """Convert a CNG timestamp to an ISO-8601 UTC timestamp."""
    if value not in (None, ""):
        try:
            if isinstance(value, (int, float)):
                number = float(value)
                if number > 100000000000:
                    number /= 1000.0
                return datetime.fromtimestamp(number, tz=timezone.utc).isoformat().replace("+00:00", "Z")

            raw = str(value).strip()
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError, OverflowError):
            pass

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def cng_load_follower_snapshot():
    """Load the last successfully fetched CNG follower snapshot."""
    if not CNG_FOLLOWERS_FILE.exists():
        return None
    try:
        with open(CNG_FOLLOWERS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print("[CNG FOLLOWERS] Snapshot konnte nicht geladen werden:", repr(e))
        return None
    if isinstance(data, dict):
        followers = data.get("followers")
    elif isinstance(data, list):
        followers = data
    else:
        followers = None
    return followers if isinstance(followers, list) else None


def cng_save_follower_snapshot(followers):
    """Atomically save the current CNG follower snapshot."""
    CNG_FOLLOWERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "followers": followers,
    }
    tmp_file = CNG_FOLLOWERS_FILE.with_suffix(CNG_FOLLOWERS_FILE.suffix + ".tmp")
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, CNG_FOLLOWERS_FILE)
    finally:
        try:
            if tmp_file.exists():
                tmp_file.unlink()
        except OSError:
            pass


def cng_follow_event(follower, creator_id, config):
    """Build a native CNG follow event from the authoritative follower list."""
    media = cng_alert_settings(config, "follow")
    avatar = str(follower.get("avatarUrl") or "")
    if avatar.startswith("/"):
        avatar = "https://cng-plattform.com" + avatar
    username = (
        str(follower.get("displayName") or "").strip()
        or str(follower.get("name") or "").strip()
        or "Unbekannt"
    )
    return {
        "event": "alert",
        "platform": "cng",
        "alertId": "follow:" + str(follower.get("userId")),
        "creatorId": creator_id,
        "alertType": "follow",
        "label": media["label"],
        "userId": follower.get("userId"),
        "username": username,
        "amount": None,
        "message": f"{username} folgt dir!",
        "shouldPlayTts": False,
        "isDisplayed": False,
        "timestamp": follower.get("followedAt") or time.time(),
        "avatar": avatar,
        "gif": media["gif"],
        "sound": media["sound"],
        "duration_seconds": media["duration_seconds"],
    }


def cng_unfollow_event(follower, creator_id, config):
    """Build the same alert schema used by native CNG alerts."""
    media = cng_alert_settings(config, "unfollow")
    avatar = str(follower.get("avatarUrl") or "")
    if avatar.startswith("/"):
        avatar = "https://cng-plattform.com" + avatar
    username = (
        str(follower.get("displayName") or "").strip()
        or str(follower.get("name") or "").strip()
        or "Unbekannt"
    )
    return {
        "event": "alert",
        "platform": "cng",
        "alertId": "unfollow:" + str(follower.get("userId")),
        "creatorId": creator_id,
        "alertType": "unfollow",
        "label": media["label"],
        "userId": follower.get("userId"),
        "username": username,
        "amount": None,
        "message": f"{username} folgt dir nicht mehr!",
        "shouldPlayTts": False,
        "isDisplayed": False,
        "timestamp": time.time(),
        "avatar": avatar,
        "gif": media["gif"],
        "sound": media["sound"],
        "duration_seconds": media["duration_seconds"],
    }


# Follower history is intentionally built ONLY from changes observed between
# two authoritative CNG follower-list snapshots.  Historical notification data
# is never imported.  The first API read establishes a baseline and creates no
# history entries.































def cng_follower_loop(config):
    """Poll creators.myFollowers and emit real unfollow alerts."""
    creator_value = config.get("cng_creator_id")
    try:
        creator = int(creator_value)
        if creator <= 0:
            raise ValueError
    except (ValueError, TypeError):
        print("[CNG FOLLOWERS] Ungültige cng_creator_id in der Konfiguration.")
        return

    try:
        cookie = cng_creator_cookie(config)
    except Exception as e:
        print("[CNG FOLLOWERS] Deaktiviert:", repr(e))
        return

    print(
        f"[CNG FOLLOWERS] aktiv - creatorId={creator} | "
        f"Cookie={CNG_CREATOR_COOKIE_FILE.name} | "
        f"Snapshot={CNG_FOLLOWERS_FILE}"
    )

    previous = cng_load_follower_snapshot()
    first_success = previous is None

    while True:
        try:
            followers = cng_fetch_followers(cookie)
            update_social_status("cng", followers=len(followers), available=True)
            current_by_id = {
                str(f.get("userId")): f
                for f in followers
                if f.get("userId") not in (None, "")
            }

            if first_success:
                cng_save_follower_snapshot(followers)
                previous = followers
                first_success = False
                print(
                    f"[CNG FOLLOWERS] Initialbestand gespeichert: "
                    f"{len(followers)} Follower"
                )
            else:
                previous_by_id = {
                    str(f.get("userId")): f
                    for f in (previous or [])
                    if f.get("userId") not in (None, "")
                }
                removed_ids = sorted(set(previous_by_id) - set(current_by_id))
                added_ids = sorted(set(current_by_id) - set(previous_by_id))

                # Only a successful API response may replace the snapshot.
                cng_save_follower_snapshot(followers)
                previous = followers

                # Process either direction independently. The previous version
                # accidentally gated BOTH follows and unfollows behind removed_ids,
                # so a new follow by itself was silently ignored.
                if added_ids or removed_ids:
                    try:
                        alert_config = load_config()
                    except (OSError, ValueError, json.JSONDecodeError):
                        alert_config = config

                    for user_id in added_ids:
                        event = cng_follow_event(
                            current_by_id[user_id], creator, alert_config
                        )
                        print(
                            f"[CNG FOLLOW] {event['username']} | userId={event['userId']}"
                        )
                        # The chat/event stream and the dedicated OBS alert
                        # stream both receive this real follower-list change.
                        publish_alert(event)
                        publish(event)

                    for user_id in removed_ids:
                        event = cng_unfollow_event(
                            previous_by_id[user_id], creator, alert_config
                        )
                        print(
                            f"[CNG UNFOLLOW] {event['username']} | userId={event['userId']}"
                        )
                        publish_alert(event)
                        publish(event)

                print(
                    f"[CNG FOLLOWERS] OK | {len(followers)} Follower"
                    + (f" | {len(added_ids)} Follow(s)" if added_ids else "")
                    + (f" | {len(removed_ids)} Unfollow(s)" if removed_ids else "")
                )

        except Exception as e:
            # Never overwrite the last good snapshot on an API/auth failure.
            print("[CNG FOLLOWERS] Fehler:", repr(e))

        time.sleep(CNG_FOLLOWERS_INTERVAL)


def cng_alert_loop(config):
    creator_value = config.get("cng_creator_id")
    try:
        creator = int(creator_value)
        if creator <= 0:
            raise ValueError
    except (ValueError, TypeError):
        print("[CNG ALERTS] Ungültige cng_creator_id in der Konfiguration.")
        return

    session = str(config.get("cng_session", "")).strip()
    cng_ref = str(config.get("cng_ref", "")).strip()
    if not session or session.startswith("HIER_"):
        print("[CNG ALERTS] Deaktiviert: cng_session fehlt.")
        return

    print(f"[CNG ALERTS] aktiv - creatorId={creator}")
    seen = set()
    published_alert_ids = set()
    first = True

    while True:
        try:
            data = cng_fetch_alerts(creator, session, cng_ref)
            alerts = cng_extract(data)
            ids = {a.get("id") for a in alerts if a.get("id") is not None}

            new_alerts = []
            if first:
                seen.update(ids)
                first = False
                print(f"[CNG ALERTS] Initialbestand übernommen: {len(ids)}")
            else:
                for alert in alerts:
                    alert_id = alert.get("id")
                    if alert_id is not None and alert_id not in seen:
                        seen.add(alert_id)
                        new_alerts.append(alert)
                if len(seen) > 5000:
                    seen.intersection_update(ids)

            new_alerts.sort(key=lambda a: (a.get("createdAt") or "", a.get("id") or 0))
            for alert in new_alerts:
                # The manager can save new alert media while the bridge is
                # running.  Reload just before publishing so the next alert
                # uses the updated GIF, sound and duration without a restart.
                try:
                    alert_config = load_config()
                except (OSError, ValueError, json.JSONDecodeError) as e:
                    print("[CNG ALERTS] Runtime-Konfiguration konnte nicht neu geladen werden:", repr(e))
                    alert_config = config

                event = cng_alert_normalize(alert, creator, alert_config)

                # Follow events are handled through creators.myFollowers. This
                # ensures a follow is shown in chat as well as in the alert overlay,
                # even when alerts.getRecentAlerts omits it. Avoid duplicates here.
                if event.get("alertType") == "follow":
                    print(
                        f"[CNG ALERT] Follow wird über creators.myFollowers verarbeitet: "
                        f"{event.get('username')}"
                    )
                    time.sleep(5.0)
                    continue

                # Some CNG alert records can have an empty "message".
                # Build the visible message from the actual alert data, never
                # from a normal chat message.
                if not str(event.get("message") or "").strip():
                    if event["alertType"] == "follow":
                        event["message"] = (
                            f"{event['username']} folgt dir!"
                        )
                    elif event["alertType"] == "donation":
                        amount = event.get("amount")
                        if amount is not None:
                            event["message"] = (
                                f"{event['username']} spendet "
                                f"{amount} Coins!"
                            )
                    elif event["alertType"] == "unfollow":
                        event["message"] = (
                            f"{event['username']} folgt dir nicht mehr!"
                        )
                print(
                    f"[CNG ALERT] {event['alertType']} | {event['username']}"
                    + (f" | {event['amount']} Coins" if event["amount"] is not None else "")
                    + f" | {event['message']}"
                )
                # The normal event stream receives the real CNG alert.
                publish(event)

                # IMPORTANT:
                # The OBS alert is triggered ONLY by this real CNG alert event.
                # NEVER derive an alert from a chat message. A user can write
                # arbitrary text such as "Blanko1804 folgt dir!" in chat.
                #
                # Therefore chat processing below is completely unrelated to
                # the alert trigger.
                if event["alertType"] in ALERT_OVERLAY_TYPES:
                    alert_id = event.get("alertId")

                    if alert_id is not None and alert_id in published_alert_ids:
                        print(
                            f"[CNG ALERT] Alert bereits verarbeitet: {alert_id}"
                        )
                        continue

                    if alert_id is not None:
                        published_alert_ids.add(alert_id)

                    publish_alert(event)
                else:
                    print(
                        f"[CNG ALERT] Nicht für OBS-Alert-Overlay: {event['alertType']}"
                    )
        except Exception as e:
            print("[CNG ALERTS] Fehler:", repr(e))

        time.sleep(5.0)





























def cng_loop(config):
    # The CNG creator ID is account-specific. Never fall back to another
    # user's ID. If it is missing, ask once at startup and persist it.
    creator_value = config.get("cng_creator_id")

    if creator_value in (None, "", 0, "0"):
        while True:
            try:
                entered = input(
                    "[CNG] Bitte deine CNG Creator-ID eingeben "
                    "(z.B. 50): "
                ).strip()
                creator = int(entered)
                if creator <= 0:
                    raise ValueError
                break
            except (ValueError, TypeError):
                print("[CNG] Ungültige Creator-ID. Bitte eine positive Zahl eingeben.")

        config["cng_creator_id"] = creator
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            print(f"[CNG] Creator-ID {creator} in {CONFIG_FILE} gespeichert.")
        except Exception as e:
            print("[CNG] Creator-ID konnte nicht gespeichert werden:", repr(e))
    else:
        try:
            creator = int(creator_value)
            if creator <= 0:
                raise ValueError
        except (ValueError, TypeError):
            print("[CNG] Ungültige cng_creator_id in der Konfiguration.")
            print("[CNG] Bitte eine positive Creator-ID eintragen.")
            return

    session = str(config.get("cng_session", "")).strip()
    cng_ref = str(config.get("cng_ref", "")).strip()
    try:
        creator_cookie = cng_creator_cookie(config)
    except Exception as exc:
        print("[CNG] Deaktiviert: CNG Creator-Session fehlt.", str(exc))
        return
    session_value = cng_legacy_session_value(creator_cookie)
    if not session_value or session_value.startswith("HIER_"):
        print("[CNG] Deaktiviert: cng_session fehlt.")
        return

    token_state = {
        "lock": threading.Lock(),
        "obs_chat_token": None,
        "presence_token": None,
        "refresh_requested": threading.Event(),
        "token_updated": threading.Event(),
    }

    def refresh_tokens():
        try:
            obs_chat_token, presence_token = cng_fetch_obs_chat_tokens(
                creator_cookie,
                creator,
            )
        except Exception as exc:
            print("[CNG] OBS-Chat-Token nicht verfügbar:", str(exc))
            return False

        with token_state["lock"]:
            token_state["obs_chat_token"] = obs_chat_token
            token_state["presence_token"] = presence_token
        token_state["token_updated"].set()
        print("[CNG] OBS-Chat-Token aktiv; Nachrichten werden tokenbasiert abgerufen.")
        return True

    token_available = refresh_tokens()
    next_token_retry = time.monotonic() + (3600.0 if token_available else 60.0)
    threading.Thread(
        target=cng_presence_heartbeat_loop,
        args=(creator, token_state),
        daemon=True,
    ).start()
    print(f"[CNG] aktiv - creatorId={creator}")

    # The first CNG getMessages response is history, not live chat.
    # Seed the seen-set and NEVER publish that first batch.
    first = True
    after_id = None
    obs_api_retry_at = 0.0
    obs_api_error_logged = False

    while True:
        try:
            now = time.monotonic()
            if (
                token_state["refresh_requested"].is_set()
                or now >= next_token_retry
            ):
                token_state["refresh_requested"].clear()
                if refresh_tokens():
                    next_token_retry = now + 3600.0
                    obs_api_retry_at = 0.0
                    obs_api_error_logged = False
                else:
                    next_token_retry = now + 60.0

            with token_state["lock"]:
                obs_chat_token = token_state.get("obs_chat_token")

            use_obs_api = bool(obs_chat_token) and now >= obs_api_retry_at
            if use_obs_api:
                try:
                    data = cng_fetch(
                        creator,
                        session,
                        cng_ref,
                        obs_chat_token=obs_chat_token,
                        after_id=after_id,
                        limit=20 if after_id is not None else 50,
                    )
                    obs_api_error_logged = False
                except Exception as exc:
                    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
                    if status in (401, 403):
                        token_state["refresh_requested"].set()
                    obs_api_retry_at = now + 30.0
                    if not obs_api_error_logged:
                        print("[CNG] OBS-Chat-Token-Abruf fehlgeschlagen; verwende Session-Fallback:", str(exc))
                        obs_api_error_logged = True
                    data = cng_fetch(
                        creator,
                        session,
                        cng_ref,
                        creator_cookie=creator_cookie,
                    )
            else:
                data = cng_fetch(
                    creator,
                    session,
                    cng_ref,
                    creator_cookie=creator_cookie,
                )

            messages = cng_extract(data)
            if use_obs_api and messages and messages[-1].get("id") is not None:
                after_id = messages[-1]["id"]

            new = []

            with cng_seen_lock:
                ids = {
                    m.get("id")
                    for m in messages
                    if m.get("id") is not None
                }

                if first:
                    cng_seen.update(ids)
                    first = False
                else:
                    for m in messages:
                        mid = m.get("id")

                        if mid is not None and mid not in cng_seen:
                            cng_seen.add(mid)
                            new.append(m)

                    if len(cng_seen) > 5000:
                        cng_seen.intersection_update(ids)

            new.sort(
                key=lambda m:
                (m.get("createdAt") or "", m.get("id") or 0)
            )

            for m in new:
                msg = cng_normalize(m, creator)
                print(
                    f"[CNG] {msg['username']}: {msg['message']}"
                )

                # The chat overlay always receives the message.
                publish(msg)

                # Chat messages are ONLY chat messages.
                # They must NEVER trigger CNG follow/donation alerts.
                # Only cng_alert_loop() above may call publish_alert().
                send_cng_command_to_bot(msg, config)

        except Exception as e:
            print("[CNG] Fehler:", repr(e))

        time.sleep(CNG_CHAT_POLL_SECONDS)


# ============================================================
# MULTI-PLATFORM SOCIAL STATUS
# ============================================================

SOCIAL_STATUS_INTERVAL = 15.0


def _status_http_json(url, headers=None, timeout=10):
    req = urllib.request.Request(
        url,
        headers=headers or {
            "User-Agent": "Mozilla/5.0 CNG-Unified-Chat/8.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _status_twitch(config):
    client_id = str(config.get("twitch_client_id", "")).strip()
    token = str(config.get("twitch_oauth_token", "")).strip()
    channel = str(config.get("twitch_channel", "")).strip().lower()
    if not client_id or not token or not channel or token.startswith("HIER_"):
        return None

    headers = {
        "Client-ID": client_id,
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
    }

    users = _status_http_json(
        "https://api.twitch.tv/helix/users?login=" + quote(channel, safe=""),
        headers=headers,
    ).get("data") or []
    if not users:
        return None

    broadcaster_id = str(users[0].get("id") or "")
    if not broadcaster_id:
        return None

    streams = _status_http_json(
        "https://api.twitch.tv/helix/streams?user_id=" + quote(broadcaster_id, safe=""),
        headers=headers,
    ).get("data") or []
    viewers = int((streams[0].get("viewer_count") if streams else 0) or 0)

    # Twitch returns the total follower count even when the token does not
    # have moderator:read:followers; the scope is only required for follower
    # identities. This keeps the status poll compatible with the existing
    # EventSub token.
    follower_url = (
        "https://api.twitch.tv/helix/channels/followers?broadcaster_id="
        + quote(broadcaster_id, safe="")
    )
    follower_data = _status_http_json(follower_url, headers=headers)
    followers = int(follower_data.get("total") or 0)

    return viewers, followers, bool(streams)


def _status_kick(config):
    try:
        _account, channel, _chatroom_id, _app_key = kick_account_settings()
    except Exception:
        return None
    if not channel:
        return None

    url = "https://kick.com/api/v2/channels/" + quote(channel, safe="")
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Origin": "https://kick.com",
        "Referer": "https://kick.com/",
    }
    data = _status_http_json(url, headers=headers, timeout=15)
    live = data.get("livestream") or {}
    viewers = int(
        live.get("viewer_count", live.get("viewers", 0)) or 0
    ) if live else 0
    followers = int(
        data.get("followers_count", data.get("followersCount", 0)) or 0
    )
    return viewers, followers, bool(live and live.get("is_live", True))


def _status_number_from_text(value):
    if isinstance(value, dict):
        value = value.get("simpleText") or youtube_text(value)
    if value is None:
        return None

    text = str(value).strip().lower().replace(" ", " ")
    # YouTube commonly abbreviates public subscriber counts (e.g. 1.23K,
    # 12,3 Mio.). Preserve the suffix instead of simply stripping punctuation.
    match = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*(k|m|b|tsd\.?|mio\.?|mrd\.?)?", text)
    if not match:
        return None

    number_text = match.group(1)
    suffix = (match.group(2) or "").strip()
    try:
        if "," in number_text and "." in number_text:
            # German thousands + decimal form, e.g. 1.234,5
            if number_text.rfind(",") > number_text.rfind("."):
                number_text = number_text.replace(".", "").replace(",", ".")
            else:
                number_text = number_text.replace(",", "")
        elif "," in number_text:
            number_text = number_text.replace(",", ".")
        number = float(number_text)
    except ValueError:
        return None

    multiplier = {
        "k": 1_000,
        "tsd": 1_000,
        "tsd.": 1_000,
        "m": 1_000_000,
        "mio": 1_000_000,
        "mio.": 1_000_000,
        "b": 1_000_000_000,
        "mrd": 1_000_000_000,
        "mrd.": 1_000_000_000,
    }.get(suffix, 1)
    return int(number * multiplier)



# YouTube's public HTML has become increasingly inconsistent for channel
# statistics. Use a small fallback list of maintained Invidious instances for
# the public subscriber count. No login or YouTube API key is required.
INVIDIOUS_INSTANCES = (
    "https://yewtu.be",
    "https://yt.artemislena.eu",
    "https://invidious.flokinet.to",
    "https://invidious.privacydev.net",
    "https://inv.nadeko.net",
)


def _youtube_invidious_request(path, params=None):
    """Try maintained public Invidious instances until one responds."""
    query = ""
    if params:
        query = "?" + urllib.parse.urlencode(params)

    last_error = None

    for instance in INVIDIOUS_INSTANCES:
        url = instance.rstrip("/") + path + query
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": YOUTUBE_UA,
                    "Accept": "application/json",
                    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
                },
            )
            with urllib.request.urlopen(req, timeout=12) as r:
                body = r.read().decode("utf-8", "replace")
            data = json.loads(body)
            if isinstance(data, dict):
                return data, instance
        except Exception as ex:
            last_error = ex
            continue

    if last_error:
        raise RuntimeError(
            f"Keine Invidious-Instanz erreichbar: {last_error}"
        )
    raise RuntimeError("Keine Invidious-Instanz erreichbar.")


def youtube_invidious_live_viewers(video_id):
    """Read the current live viewer count from Invidious' video endpoint."""
    video_id = str(video_id or "").strip()
    if not video_id:
        return 0
    try:
        data, instance = _youtube_invidious_request(
            "/api/v1/videos/" + urllib.parse.quote(video_id, safe="")
        )
        if not isinstance(data, dict):
            return 0
        live_now = data.get("liveNow")
        value = data.get("viewCount")
        if live_now and isinstance(value, (int, float)):
            return max(0, int(value))
        # Some instances may return the live flag as a string.
        if str(live_now).lower() == "true" and str(value or "").isdigit():
            return max(0, int(value))
        return 0
    except Exception:
        return 0


def youtube_invidious_subscriber_count(channel):
    """
    Resolve a YouTube @handle to a channel ID and read subCount.

    This is only a fallback. The normal direct YouTube parser is attempted
    first, so the bridge remains independent of Invidious when YouTube exposes
    the data normally.
    """
    channel = str(channel or "").strip()
    if not channel:
        return 0

    if channel.startswith(("http://", "https://")):
        source_url = channel
    elif channel.startswith("@"):
        source_url = "https://www.youtube.com/" + channel
    elif channel.startswith(("UC", "HC")) and len(channel) >= 20:
        source_url = "https://www.youtube.com/channel/" + channel
    else:
        source_url = "https://www.youtube.com/@" + channel

    # First try the exact URL resolver.
    try:
        resolved, instance = _youtube_invidious_request(
            "/api/v1/resolveurl",
            {"url": source_url},
        )
        ucid = (
            resolved.get("ucid")
            or resolved.get("browseId")
            or resolved.get("channelId")
        )

        if ucid:
            channel_data, _ = _youtube_invidious_request(
                "/api/v1/channels/" + quote(str(ucid), safe="")
            )
            count = channel_data.get("subCount")
            if isinstance(count, (int, float)):
                count = int(count)
                print(
                    f"[YOUTUBE STATUS] Abonnenten über {instance}: {count}"
                )
                return max(0, count)
    except Exception as ex:
        print("[YOUTUBE STATUS] Invidious Resolve fehlgeschlagen:", repr(ex))

    # Search fallback for instances where resolveurl is disabled.
    try:
        query = channel.lstrip("@")
        result, instance = _youtube_invidious_request(
            "/api/v1/search",
            {"q": query, "type": "channel"},
        )

        if isinstance(result, list):
            for item in result:
                if not isinstance(item, dict):
                    continue

                ucid = item.get("authorId") or item.get("channelId")
                if not ucid:
                    continue

                author = str(item.get("author") or "").strip().lower()
                handle = query.lower()

                # Prefer an exact author/handle match, but accept the first
                # channel result when YouTube has no handle in the result.
                if author == handle or not author:
                    channel_data, _ = _youtube_invidious_request(
                        "/api/v1/channels/" + quote(str(ucid), safe="")
                    )
                    count = channel_data.get("subCount")
                    if isinstance(count, (int, float)):
                        count = int(count)
                        print(
                            f"[YOUTUBE STATUS] Abonnenten über {instance}: {count}"
                        )
                        return max(0, count)

            # Last search fallback: use first channel result.
            for item in result:
                if not isinstance(item, dict):
                    continue
                ucid = item.get("authorId") or item.get("channelId")
                if ucid:
                    channel_data, _ = _youtube_invidious_request(
                        "/api/v1/channels/" + quote(str(ucid), safe="")
                    )
                    count = channel_data.get("subCount")
                    if isinstance(count, (int, float)):
                        count = int(count)
                        print(
                            f"[YOUTUBE STATUS] Abonnenten über {instance}: {count}"
                        )
                        return max(0, count)
    except Exception as ex:
        print("[YOUTUBE STATUS] Invidious Suche fehlgeschlagen:", repr(ex))

    return 0



def youtube_extract_live_viewers_from_html(html):
    """Extract the live viewer count directly from YouTube channel/live HTML."""
    candidates = []

    # Current public live-page payloads can expose the live count under
    # concurrentViewCount/originalViewCount. Keep these as fallbacks because
    # YouTube changes which field is emitted between player variants.
    for pattern in (
        r'"concurrentViewCount"\s*:\s*"(\d+)"',
        r'"concurrentViewCount"\s*:\s*(\d+)',
        r'"originalViewCount"\s*:\s*"(\d+)"',
        r'"originalViewCount"\s*:\s*(\d+)',
    ):
        for value in re.findall(pattern, html, re.I):
            try:
                candidates.append(int(value))
            except Exception:
                pass

    # Also inspect the player microformat, where YouTube can expose the
    # documented liveBroadcastDetails.concurrentViewers value.
    try:
        player = youtube_extract_player_response(html)
        microformat = player.get("microformat", {}) if isinstance(player, dict) else {}
        renderer = microformat.get("playerMicroformatRenderer", {}) if isinstance(microformat, dict) else {}
        details = renderer.get("liveBroadcastDetails", {}) if isinstance(renderer, dict) else {}
        value = details.get("concurrentViewers") if isinstance(details, dict) else None
        parsed = _status_number_from_text(value) or 0
        if parsed > 0:
            candidates.append(parsed)
    except Exception:
        pass

    return max(candidates, default=0)

def youtube_extract_public_subscriber_count(html):
    """Extract the channel's own public subscriber count from YouTube HTML.

    YouTube's newer channel pages no longer consistently expose the legacy
    ``subscriberCountText`` key. The public About page still contains the
    channel's displayed subscriber text inside the channel metadata model.
    Prefer that scoped data before falling back to legacy keys/regexes.
    """
    if not html:
        return 0

    candidates = []

    def add(value):
        if isinstance(value, str):
            value = value.replace("\\u00a0", " ").strip()
            if re.search(r"(?:subscriber|subscribers|abonnent|abonnenten)", value, re.I):
                parsed = _status_number_from_text(value)
                if parsed is not None:
                    candidates.append((parsed, value))

    def walk(obj, scoped=False):
        if isinstance(obj, dict):
            # The current channel About page commonly stores the channel's
            # own metadata below aboutChannelViewModel. Keep this subtree
            # separate from unrelated/recommended-channel metadata.
            for key, value in obj.items():
                child_scoped = scoped or key in (
                    "aboutChannelViewModel",
                    "pageHeaderViewModel",
                    "c4TabbedHeaderRenderer",
                    "metadataRows",
                    "metadataRowContainer",
                    "contentMetadataViewModel",
                )
                if isinstance(value, str):
                    if child_scoped:
                        add(value)
                else:
                    walk(value, child_scoped)
        elif isinstance(obj, list):
            for item in obj:
                walk(item, scoped)

    try:
        data = youtube_extract_initial_data(html)
        walk(data, False)
    except Exception:
        pass

    # Legacy/current explicit subscriber keys are still useful and are much
    # safer than taking the first arbitrary number from the page.
    if not candidates:
        for pattern in (
            r'"subscriberCountText"\s*:\s*\{\s*"simpleText"\s*:\s*"([^"]+)"',
            r'"subscriberCountText"\s*:\s*\{\s*"runs"\s*:\s*\[\s*\{\s*"text"\s*:\s*"([^"]+)"',
            r'"subscriberCountText"\s*:\s*"([^"]+)"',
            r'"subscriberCount"\s*:\s*"?([0-9][0-9.,]*\s*(?:K|M|B|Tsd\.?|Mio\.?|Mrd\.?)?)',
        ):
            for match in re.finditer(pattern, html, re.I | re.S):
                parsed = _status_number_from_text(match.group(1))
                if parsed is not None:
                    candidates.append((parsed, match.group(1)))
                    break
            if candidates:
                break

    if candidates:
        # Prefer the largest scoped candidate only when multiple metadata
        # strings exist; this avoids accidentally returning a navigation
        # counter such as "0 subscribers" when the real channel count is also
        # present on the About page.
        positive = [item for item in candidates if item[0] > 0]
        value, text = max(positive or candidates, key=lambda item: item[0])
        print(f"[YOUTUBE STATUS] Abonnenten direkt aus YouTube HTML: {value} | {text!r}")
        return max(0, int(value))

    return 0
def _status_youtube_account(account):
    """
    Read public YouTube channel status.

    The live viewer count is first read with the same yt-dlp method that was
    verified independently against the active livestream:
        concurrent_view_count

    If yt-dlp is unavailable or fails, keep the existing direct YouTube
    parsing as fallback. No Invidious viewCount is used for live viewers,
    because that is the total video view count.
    """
    base = youtube_channel_base(account.get("channel"))
    if not base:
        return None

    headers = {
        "User-Agent": YOUTUBE_UA,
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    }

    channel_html = youtube_http_get(base)

    # -------------------------
    # Subscriber count
    # -------------------------
    followers = 0

    # The /about page is the most reliable public location for the channel's
    # displayed subscriber count. This also handles YouTube's newer
    # pageHeaderViewModel/aboutChannelViewModel layout.
    try:
        about_html = youtube_http_get(base + "/about")
        followers = youtube_extract_public_subscriber_count(about_html) or 0
    except Exception as ex:
        print("[YOUTUBE STATUS] About-Seite für Abonnenten fehlgeschlagen:", repr(ex))

    # Fall back to the channel landing page for older YouTube layouts.
    if followers <= 0:
        try:
            followers = youtube_extract_public_subscriber_count(channel_html) or 0
        except Exception:
            pass

    # yt-dlp can expose the same public field when the installed extractor
    # recognizes the channel tab. It is a fallback only; live detection below
    # continues to use the already working video extractor.
    if followers <= 0:
        try:
            import yt_dlp
            opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(base + "/about", download=False)
            candidate = info.get("channel_follower_count") or info.get("follower_count")
            if candidate is not None:
                followers = max(0, int(candidate))
                print(f"[YOUTUBE STATUS] Abonnenten über yt-dlp: {followers}")
        except Exception as ex:
            print("[YOUTUBE STATUS] yt-dlp Subscriber-Fallback fehlgeschlagen:", repr(ex))

    # Invidious is only the last fallback. 403s from public instances therefore
    # no longer block the normal YouTube subscriber lookup.
    if followers <= 0:
        try:
            followers = youtube_invidious_subscriber_count(
                account.get("channel")
            )
        except Exception as ex:
            print("[SOCIAL STATUS] YouTube Subscriber-Fallback Fehler:", repr(ex))

    # -------------------------
    # Discover current live video
    # -------------------------
    live_video_ids = []

    try:
        live_video_ids = youtube_discover_live_videos(account["channel"])
    except Exception as ex:
        print(
            f"[SOCIAL STATUS] YouTube Live-Erkennung "
            f"{account.get('display_name', '')} Fehler:",
            repr(ex),
        )

    if not live_video_ids:
        print(
            f"[SOCIAL STATUS] YOUTUBE DEBUG | "
            f"{account.get('display_name', '')} | "
            f"keine aktive Live-Video-ID gefunden"
        )
        return 0, followers, False

    viewers = 0
    is_live = True

    # The standalone test proved that yt-dlp returns the exact current
    # livestream viewer count through concurrent_view_count.
    try:
        import yt_dlp
    except ImportError:
        yt_dlp = None

    for video_id in live_video_ids:
        found_viewers = 0

        if yt_dlp is not None:
            try:
                url = "https://www.youtube.com/watch?v=" + video_id
                opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "skip_download": True,
                    "extract_flat": False,
                }

                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)

                live_status = info.get("live_status")
                concurrent = info.get("concurrent_view_count")

                if live_status in ("is_live", "is_upcoming"):
                    try:
                        if concurrent is not None:
                            found_viewers = max(0, int(concurrent))
                    except (TypeError, ValueError):
                        pass

                print(
                    f"[SOCIAL STATUS] YOUTUBE DEBUG | "
                    f"{account.get('display_name', '')} | "
                    f"Live-Video {video_id} | "
                    f"yt-dlp live_status={live_status!r} | "
                    f"concurrent_view_count={concurrent!r} | "
                    f"Viewer gefunden: {found_viewers}"
                )

            except Exception as ex:
                print(
                    f"[SOCIAL STATUS] YouTube yt-dlp "
                    f"{account.get('display_name', '')} / {video_id} Fehler:",
                    repr(ex),
                )

        # Direct YouTube fallback. This is only used if yt-dlp did not return
        # a value, and preserves the existing parser behavior.
        if found_viewers <= 0:
            try:
                watch_html = youtube_http_get(
                    "https://www.youtube.com/watch?v=" + video_id
                )

                viewer_patterns = (
                    r'"concurrentViewers"\s*:\s*"(\d+)"',
                    r'"concurrentViewers"\s*:\s*(\d+)',
                    r'"concurrentViewers"\s*:\s*\{\s*"simpleText"\s*:\s*"([^"]+)"',
                    r'"concurrentViewers"\s*:\s*\{\s*"runs"\s*:\s*\[\s*\{\s*"text"\s*:\s*"([^"]+)"',
                )

                for pattern in viewer_patterns:
                    for value in re.findall(pattern, watch_html, re.I | re.S):
                        parsed = _status_number_from_text(value) or 0
                        found_viewers = max(found_viewers, parsed)

                try:
                    player_data = youtube_extract_initial_data(watch_html)
                    structured = youtube_find_key(
                        player_data, "concurrentViewers"
                    )
                    found_viewers = max(
                        found_viewers,
                        _status_number_from_text(structured) or 0,
                    )
                except Exception:
                    pass

                try:
                    player_response = youtube_extract_player_response(watch_html)

                    live_details = youtube_find_key(
                        player_response, "liveBroadcastDetails"
                    )
                    structured = youtube_find_key(
                        live_details, "concurrentViewers"
                    )
                    found_viewers = max(
                        found_viewers,
                        _status_number_from_text(structured) or 0,
                    )

                    live_stream_data = youtube_find_key(
                        player_response, "liveStreamData"
                    )
                    structured = youtube_find_key(
                        live_stream_data, "concurrentViewers"
                    )
                    found_viewers = max(
                        found_viewers,
                        _status_number_from_text(structured) or 0,
                    )
                except Exception:
                    pass

                print(
                    f"[SOCIAL STATUS] YOUTUBE DEBUG | "
                    f"{account.get('display_name', '')} | "
                    f"Live-Video {video_id} | "
                    f"Direct-Fallback Viewer: {found_viewers}"
                )

            except Exception as ex:
                print(
                    f"[SOCIAL STATUS] YouTube Watchpage "
                    f"{account.get('display_name', '')} Fehler:",
                    repr(ex),
                )

        viewers = max(viewers, found_viewers)

    return viewers, followers, is_live

def _status_tiktok_account(account):
    """
    Fetch the public TikTok profile follower count.

    The existing TikTokLive connection remains the source of truth for live
    viewer_count. This function only supplies the persistent follower count,
    so it also works while the account is offline.
    """
    channel = str(
        account.get("channel")
        or account.get("username")
        or account.get("unique_id")
        or ""
    ).strip().lstrip("@")

    if not channel:
        return None

    url = f"https://www.tiktok.com/@{channel}"

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/140.0 Safari/537.36"
                ),
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml",
            },
        )

        with urllib.request.urlopen(req, timeout=20) as response:
            html = response.read().decode("utf-8", "replace")

        followers = 0

        # TikTok currently exposes profile stats in several JSON page-state
        # structures. Check all known representations and take the largest
        # valid value found.
        patterns = (
            r'"followerCount"\s*:\s*(\d+)',
            r'"follower_count"\s*:\s*(\d+)',
            r'"followersCount"\s*:\s*(\d+)',
            r'"followers_count"\s*:\s*(\d+)',
        )

        for pattern in patterns:
            for value in re.findall(pattern, html, re.I):
                try:
                    followers = max(followers, int(value))
                except (TypeError, ValueError):
                    pass

        # Some page variants serialize numeric values as strings.
        if followers <= 0:
            for pattern in (
                r'"followerCount"\s*:\s*"([\d\.,]+)"',
                r'"follower_count"\s*:\s*"([\d\.,]+)"',
            ):
                for value in re.findall(pattern, html, re.I):
                    followers = max(
                        followers,
                        _status_number_from_text(value) or 0,
                    )

        if followers > 0:
            print(
                f"[SOCIAL STATUS] TIKTOK | Profil | "
                f"{followers} Follower"
            )
            return followers

        print(
            f"[SOCIAL STATUS] TIKTOK | Keine öffentliche "
            f"Followerzahl für @{channel} gefunden."
        )
        return 0

    except Exception as ex:
        print(
            f"[SOCIAL STATUS] TikTok Profilabfrage @{channel} Fehler:",
            repr(ex),
        )
        return None


def social_status_loop(config):
    """Poll public/account APIs and keep /stream-status platform totals fresh."""
    print("[SOCIAL STATUS] aktiv - Twitch/Kick/YouTube + TikTok live cache")
    while True:
        # Twitch
        try:
            result = _status_twitch(config)
            if result is None:
                update_social_status("twitch", viewers=0, followers=0, available=False)
            else:
                viewers, followers, live = result
                update_social_status("twitch", viewers=viewers, followers=followers, available=True)
                print(f"[SOCIAL STATUS] TWITCH | {'LIVE' if live else 'OFFLINE'} | {viewers} Zuschauer | {followers} Follower")
        except Exception as e:
            print("[SOCIAL STATUS] Twitch Fehler:", repr(e))

        # Kick
        try:
            result = _status_kick(config)
            if result is None:
                update_social_status("kick", viewers=0, followers=0, available=False)
            else:
                viewers, followers, live = result
                update_social_status("kick", viewers=viewers, followers=followers, available=True)
                print(f"[SOCIAL STATUS] KICK | {'LIVE' if live else 'OFFLINE'} | {viewers} Zuschauer | {followers} Follower")
        except Exception as e:
            print("[SOCIAL STATUS] Kick Fehler:", repr(e))

        # YouTube - supports multiple configured channels and sums them.
        try:
            accounts = youtube_accounts_from_config(config)
            total_viewers = 0
            total_followers = 0
            any_account = False
            for account in accounts:
                try:
                    result = _status_youtube_account(account)
                    if result is None:
                        continue
                    viewers, followers, live = result
                    total_viewers += viewers
                    total_followers += followers
                    any_account = True
                    print(
                        f"[SOCIAL STATUS] YOUTUBE | {account['display_name']} | "
                        f"{'LIVE' if live else 'OFFLINE'} | "
                        f"{viewers} Zuschauer | {followers} Abos"
                    )
                    if followers <= 0:
                        print(
                            f"[SOCIAL STATUS] YOUTUBE | Keine öffentliche "
                            f"Abonnentenzahl für {account['display_name']} gefunden."
                        )
                except Exception as e:
                    print(
                        f"[SOCIAL STATUS] YouTube {account.get('display_name', '')} Fehler:",
                        repr(e),
                    )
            update_social_status(
                "youtube",
                viewers=total_viewers,
                followers=total_followers,
                available=any_account,
            )
        except Exception as e:
            print("[SOCIAL STATUS] YouTube Fehler:", repr(e))

        # TikTok
        # Follower count comes from the public profile and therefore works
        # even when TikTok is currently offline. Live viewer count remains
        # owned by the existing TikTokLive connection.
        try:
            accounts = load_accounts_file()
            account, unique_id = find_tiktok_account(accounts)

            if account and unique_id:
                followers = _status_tiktok_account(account)

                if followers is not None:
                    current = get_social_status()["platforms"]["tiktok"]
                    viewers = int(current.get("viewers", 0) or 0)

                    update_social_status(
                        "tiktok",
                        viewers=viewers,
                        followers=followers,
                        available=True,
                    )

                    print(
                        f"[SOCIAL STATUS] TIKTOK | "
                        f"{viewers} Zuschauer | {followers} Follower"
                    )
        except Exception as e:
            print("[SOCIAL STATUS] TikTok Fehler:", repr(e))

        time.sleep(SOCIAL_STATUS_INTERVAL)


# ============================================================
# PROFILE AVATARS
# ============================================================

def cached_avatar(key):
    with avatar_cache_lock:
        return avatar_cache.get(str(key), "")


def set_cached_avatar(key, url):
    if not key or not url:
        return
    with avatar_cache_lock:
        avatar_cache[str(key)] = url


def twitch_avatar(config, user_id):
    """
    Resolve a Twitch profile image using Twitch Helix.
    Uses the existing Client-ID + User Access Token.
    """
    if not user_id:
        return ""

    cached=cached_avatar("twitch:" + str(user_id))
    if cached:
        return cached

    client_id=str(config.get("twitch_client_id","")).strip()
    token=str(config.get("twitch_oauth_token","")).strip()

    if not client_id or not token:
        return ""

    try:
        req=urllib.request.Request(
            "https://api.twitch.tv/helix/users?id=" +
            quote(str(user_id), safe=""),
            headers={
                "Client-ID":client_id,
                "Authorization":"Bearer " + token,
                "Accept":"application/json"
            }
        )

        with urllib.request.urlopen(req, timeout=5) as r:
            data=json.loads(r.read().decode("utf-8"))

        users=data.get("data") or []
        if users:
            url=users[0].get("profile_image_url") or ""
            if url:
                set_cached_avatar("twitch:" + str(user_id), url)
                return url

    except Exception as e:
        print("[TWITCH] Avatar konnte nicht geladen werden:", repr(e))

    return ""


def kick_avatar_from_sender(sender):
    """
    Kick chat payloads can contain the user's profile picture in the
    identity/user object. Check the common shapes without depending
    on one exact payload version.
    """
    identity=sender.get("identity") or {}
    user=sender.get("user") or {}

    candidates=[
        sender.get("profile_picture"),
        sender.get("profile_image"),
        sender.get("avatar"),
        sender.get("profile_pic"),
        identity.get("profile_picture"),
        identity.get("profile_image"),
        identity.get("avatar"),
        user.get("profile_picture"),
        user.get("profile_image"),
        user.get("avatar")
    ]

    for value in candidates:
        if isinstance(value,str) and value.startswith(("http://","https://")):
            return value

    return ""


# ============================================================
# TWITCH IRC
# ============================================================

def twitch_parse_tags(raw):
    tags = {}

    if not raw.startswith("@"):
        return tags

    tag_text = raw[1:].split(" ", 1)[0]

    for item in tag_text.split(";"):
        if "=" in item:
            key, value = item.split("=", 1)
            tags[key] = value.replace("\\s", " ").replace("\\:", ":")
        else:
            tags[item] = ""

    return tags


def twitch_badges(tags):
    result = []

    badges = tags.get("badges", "")

    for badge in badges.split(","):
        if not badge:
            continue

        name = badge.split("/", 1)[0]

        mapping = {
            "broadcaster": "BROADCASTER",
            "moderator": "MOD",
            "vip": "VIP",
            "subscriber": "SUB",
            "founder": "FOUNDER"
        }

        result.append(mapping.get(name, name.upper()))

    return result


def twitch_parse_message(line, channel):
    """
    Parse Twitch IRC chat and system events.

    Normal PRIVMSG stays compatible with the existing message schema.
    Additional Twitch events are normalized as event="system" messages so
    the web overlay can display them separately and use the short "tts"
    field for speech.
    """
    tags = twitch_parse_tags(line)

    # ------------------------------------------------------------
    # Twitch USERNOTICE
    # ------------------------------------------------------------
    # USERNOTICE carries subscription, gift-sub and raid notifications.
    if " USERNOTICE #" in line:
        marker = " USERNOTICE #"
        try:
            before, after = line.split(marker, 1)
            if " :" in after:
                channel_part, trailing = after.split(" :", 1)
            else:
                channel_part, trailing = after, ""

            msg_channel = channel_part.strip().lower()
            if msg_channel != channel.lower():
                return None
        except ValueError:
            return None

        username = (
            tags.get("display-name")
            or tags.get("login")
            or "Unbekannt"
        )

        event_type = tags.get("msg-id", "").strip().lower()
        system_msg = tags.get("system-msg", "").strip()

        # Prefer Twitch's localized system message where available,
        # but create a stable German fallback for the overlay/TTS.
        sub_plan = tags.get("msg-param-sub-plan", "")
        months = tags.get("msg-param-cumulative-months", "")
        recipient = (
            tags.get("msg-param-recipient-display-name")
            or tags.get("msg-param-recipient-user-name")
            or ""
        )
        total_gifted = tags.get("msg-param-sender-count", "")
        viewers = tags.get("msg-param-viewerCount", "")

        if event_type == "sub":
            visible = f"{username} hat ein neues Twitch-Abo abgeschlossen!"
            tts = f"{username} hat ein neues Abo abgeschlossen."

        elif event_type == "resub":
            visible = f"{username} hat sein Twitch-Abo verlängert!"
            if months:
                visible = (
                    f"{username} hat sein Twitch-Abo verlängert "
                    f"({months} Monate)!"
                )
            tts = f"{username} hat sein Abo verlängert."

        elif event_type == "subgift":
            if recipient:
                visible = (
                    f"{username} hat {recipient} ein Twitch-Abo geschenkt!"
                )
                tts = f"{username} hat {recipient} ein Abo geschenkt."
            else:
                visible = f"{username} hat ein Twitch-Abo verschenkt!"
                tts = f"{username} hat ein Abo verschenkt."

        elif event_type == "submysterygift":
            count = tags.get("msg-param-mass-gift-count", "")
            if count:
                visible = (
                    f"{username} hat {count} Twitch-Abos verschenkt!"
                )
                tts = f"{username} hat {count} Abos verschenkt."
            else:
                visible = f"{username} hat mehrere Twitch-Abos verschenkt!"
                tts = f"{username} hat mehrere Abos verschenkt."

        elif event_type == "raid":
            visible = f"{username} raidet den Kanal"
            if viewers:
                visible += f" mit {viewers} Zuschauern"
            visible += "!"
            tts = f"{username} raidet den Kanal"
            if viewers:
                tts += f" mit {viewers} Zuschauern"
            tts += "."

        elif event_type == "announcement":
            visible = f"{username}: {trailing}".strip()
            tts = visible

        else:
            # Keep unknown USERNOTICE events visible rather than silently
            # dropping them. This is useful when Twitch introduces another
            # USERNOTICE type.
            visible = system_msg or trailing or f"{username}: Twitch-Ereignis"
            tts = visible

        return {
            "event": "system",
            "platform": "twitch",
            "eventType": event_type or "usernotice",
            "id": tags.get("id"),
            "userId": tags.get("user-id"),
            "username": username,
            "message": visible,
            "tts": tts,
            "systemMessage": system_msg,
            "amount": None,
            "avatar": "",
            "timestamp": tags.get("tmi-sent-ts"),
            "badges": twitch_badges(tags),
            "twitchEmotes": tags.get("emotes", ""),
            "moderator": tags.get("mod") == "1",
            "subscriber": tags.get("subscriber") == "1"
        }

    # ------------------------------------------------------------
    # Twitch PRIVMSG
    # ------------------------------------------------------------
    marker = " PRIVMSG #"
    if marker not in line:
        return None

    try:
        before, after = line.split(marker, 1)
    except ValueError:
        return None

    if " :" not in after:
        return None

    channel_part, message = after.split(" :", 1)
    msg_channel = channel_part.strip().lower()

    if msg_channel != channel.lower():
        return None

    username = (
        tags.get("display-name")
        or tags.get("login")
        or "Unbekannt"
    )

    # Bits are delivered as a normal PRIVMSG with a "bits" tag.
    bits_raw = tags.get("bits")
    try:
        bits = int(bits_raw) if bits_raw else 0
    except (TypeError, ValueError):
        bits = 0

    if bits > 0:
        visible = f"{username} hat {bits} Bits gesendet!"
        if message.strip():
            visible += f"  {message.strip()}"

        tts = f"{username} hat {bits} Bits gesendet."
        return {
            "event": "system",
            "platform": "twitch",
            "eventType": "bits",
            "id": tags.get("id"),
            "userId": tags.get("user-id"),
            "username": username,
            "message": visible,
            "tts": tts,
            "systemMessage": "",
            "amount": bits,
            "avatar": "",
            "timestamp": tags.get("tmi-sent-ts"),
            "badges": twitch_badges(tags),
            "twitchEmotes": tags.get("emotes", ""),
            "moderator": tags.get("mod") == "1",
            "subscriber": tags.get("subscriber") == "1"
        }

    # Channel Point reward redemption is represented on PRIVMSG by
    # custom-reward-id. The reward name itself is not always present in IRC,
    # so keep the event generic unless the reward title is available.
    reward_id = tags.get("custom-reward-id", "").strip()
    if reward_id:
        visible = f"{username} hat eine Kanalbelohnung eingelöst!"
        if message.strip():
            visible += f"  {message.strip()}"

        tts = f"{username} hat eine Kanalbelohnung eingelöst."
        return {
            "event": "system",
            "platform": "twitch",
            "eventType": "channel_point_reward",
            "id": tags.get("id"),
            "userId": tags.get("user-id"),
            "username": username,
            "message": visible,
            "tts": tts,
            "systemMessage": "",
            "amount": None,
            "rewardId": reward_id,
            "avatar": "",
            "timestamp": tags.get("tmi-sent-ts"),
            "badges": twitch_badges(tags),
            "twitchEmotes": tags.get("emotes", ""),
            "moderator": tags.get("mod") == "1",
            "subscriber": tags.get("subscriber") == "1"
        }

    return {
        "platform": "twitch",
        "id": tags.get("id"),
        "userId": tags.get("user-id"),
        "username": username,
        "message": message,
        "avatar": "",
        "timestamp": tags.get("tmi-sent-ts"),
        "badges": twitch_badges(tags),
        "twitchEmotes": tags.get("emotes", ""),
        "moderator": tags.get("mod") == "1",
        "subscriber": tags.get("subscriber") == "1"
    }


def twitch_eventsub_user_id(config, channel):
    """Resolve the broadcaster user ID needed by Twitch EventSub."""
    client_id = str(config.get("twitch_client_id", "")).strip()
    token = str(config.get("twitch_oauth_token", "")).strip()
    if not client_id or not token:
        return None

    try:
        req = urllib.request.Request(
            "https://api.twitch.tv/helix/users?login=" + quote(channel, safe=""),
            headers={
                "Client-ID": client_id,
                "Authorization": "Bearer " + token,
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        users = data.get("data") or []
        if users:
            return str(users[0].get("id"))
    except Exception as e:
        print("[TWITCH EVENTSUB] Broadcaster-ID konnte nicht ermittelt werden:", repr(e))
    return None


def twitch_is_reconnect_command(line):
    """Return True for Twitch IRC's documented server RECONNECT command."""
    parts = str(line or "").strip().split()
    return len(parts) >= 2 and parts[-2:] == [":tmi.twitch.tv", "RECONNECT"]


def twitch_eventsub_emit(event_type, event_data, config):
    """Normalize one Twitch EventSub notification into the bridge schema."""
    username = str(
        event_data.get("user_name")
        or event_data.get("from_broadcaster_user_name")
        or "Unbekannt"
    )
    user_id = event_data.get("user_id") or event_data.get("from_broadcaster_user_id")
    avatar = twitch_avatar(config, user_id) if user_id else ""

    if event_type == "channel.follow":
        message = f"{username} folgt dir!"
        tts = f"{username} folgt dir."
        extra = {}

    elif event_type == "channel.subscribe":
        tier = str(event_data.get("tier") or "").strip()
        tier_text = f" Tier {tier}" if tier else ""
        message = f"{username} hat abonniert!{tier_text}"
        tts = f"{username} hat abonniert."
        extra = {"tier": tier, "isGift": bool(event_data.get("is_gift"))}

    elif event_type == "channel.subscription.gift":
        total = event_data.get("total")
        if total is not None:
            message = f"{username} hat {total} Geschenk-Abos verteilt!"
            tts = f"{username} hat {total} Geschenk-Abos verteilt."
        else:
            message = f"{username} hat Geschenk-Abos verteilt!"
            tts = f"{username} hat Geschenk-Abos verteilt."
        extra = {"total": total, "cumulativeTotal": event_data.get("cumulative_total")}

    elif event_type == "channel.subscription.message":
        months = event_data.get("cumulative_months") or event_data.get("duration_months")
        message = f"{username} hat sein Abo verlängert"
        if months:
            message += f" ({months} Monate)"
        tts = f"{username} hat sein Abo verlängert."
        extra = {"months": months, "message": event_data.get("message", {}).get("text", "") if isinstance(event_data.get("message"), dict) else ""}

    elif event_type == "channel.cheer":
        bits = event_data.get("bits")
        message = f"{username} hat {bits} Bits gesendet!" if bits is not None else f"{username} hat Bits gesendet!"
        tts = message
        extra = {"bits": bits}

    elif event_type == "channel.channel_points_custom_reward_redemption.add":
        reward = event_data.get("reward") or {}
        title = str(reward.get("title") or "Kanalbelohnung")
        cost = reward.get("cost")
        user_input = str(event_data.get("user_input") or "").strip()
        message = f"{username} hat {title} eingelöst"
        if user_input:
            message += f": {user_input}"
        tts = f"{username} hat {title} eingelöst."
        extra = {"rewardId": reward.get("id"), "rewardTitle": title, "cost": cost, "userInput": user_input}

    elif event_type == "channel.raid":
        viewers = event_data.get("viewers")
        message = f"Raid von {username}!"
        if viewers is not None:
            message += f" {viewers} Zuschauer."
        tts = message
        extra = {"viewers": viewers}

    else:
        message = f"Twitch Event: {event_type}"
        tts = message
        extra = {}

    return {
        "event": "system",
        "platform": "twitch",
        "eventType": event_type,
        "id": str(event_data.get("id") or f"{event_type}:{time.time_ns()}"),
        "userId": user_id,
        "username": username,
        "message": message,
        "tts": tts,
        "systemMessage": "",
        "avatar": avatar,
        "timestamp": event_data.get("followed_at") or event_data.get("started_at") or event_data.get("redeemed_at") or event_data.get("timestamp"),
        "badges": [],
        "moderator": False,
        "subscriber": event_type.startswith("channel.subscription"),
        **extra,
    }


def twitch_validate_token(config):
    """Validate the Twitch user token and return its metadata.

    Twitch exposes the granted scopes here. We use this before creating
    EventSub subscriptions so missing permissions are reported explicitly
    instead of generating a wall of opaque HTTP 403 errors.
    """
    token = str(config.get("twitch_oauth_token", "")).strip()
    client_id = str(config.get("twitch_client_id", "")).strip()

    if not token or token.startswith("HIER_"):
        raise RuntimeError("twitch_oauth_token fehlt")
    if not client_id or client_id.startswith("HIER_"):
        raise RuntimeError("twitch_client_id fehlt")

    req = urllib.request.Request(
        "https://id.twitch.tv/oauth2/validate",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(
            f"Twitch OAuth Validate HTTP {e.code}: {body or e.reason}"
        ) from e

    actual_client_id = str(data.get("client_id") or "")
    if actual_client_id and actual_client_id != client_id:
        raise RuntimeError(
            "Twitch OAuth Token gehört zu einer anderen Client-ID "
            f"({actual_client_id} != {client_id})"
        )

    scopes = {
        str(scope).strip()
        for scope in (data.get("scopes") or [])
        if str(scope).strip()
    }

    return {
        "client_id": actual_client_id or client_id,
        "login": str(data.get("login") or ""),
        "user_id": str(data.get("user_id") or ""),
        "scopes": scopes,
        "expires_in": data.get("expires_in"),
    }


def twitch_eventsub_http_error(e):
    """Return Twitch's actual EventSub error body when available."""
    if not isinstance(e, urllib.error.HTTPError):
        return str(e)

    body = ""
    try:
        body = e.read().decode("utf-8", errors="replace")
    except Exception:
        pass

    if body:
        try:
            obj = json.loads(body)
            message = obj.get("message") or obj.get("error")
            if message:
                return f"HTTP {e.code}: {message}"
        except Exception:
            pass
        return f"HTTP {e.code}: {body}"

    return f"HTTP {e.code}: {e.reason}"



TWITCH_EVENTSUB_SCOPES = [
    "moderator:read:followers",
    "channel:read:subscriptions",
    "bits:read",
    "channel:read:redemptions",
]


def twitch_device_auth(config):
    """Authorize the Twitch user with all scopes needed by EventSub.

    Uses Twitch's device authorization flow so no client secret is stored
    in the bridge. The resulting access/refresh tokens are written back to
    the runtime configuration.
    """
    client_id = str(config.get("twitch_client_id", "")).strip()
    if not client_id or client_id.startswith("HIER_"):
        raise RuntimeError("twitch_client_id fehlt in unified_chat_config.json")

    scopes = " ".join(TWITCH_EVENTSUB_SCOPES)

    req = urllib.request.Request(
        "https://id.twitch.tv/oauth2/device",
        data=urllib.parse.urlencode({
            "client_id": client_id,
            "scopes": scopes,
        }).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(
            f"Twitch Device Authorization HTTP {e.code}: {body or e.reason}"
        ) from e

    device_code = data.get("device_code")
    user_code = data.get("user_code")
    verification_uri = (
        data.get("verification_uri")
        or data.get("verification_uri_complete")
    )
    interval = int(data.get("interval") or 5)
    expires_in = int(data.get("expires_in") or 1800)

    if not device_code or not user_code or not verification_uri:
        raise RuntimeError(
            "Twitch lieferte keinen vollständigen Device-Code. "
            + json.dumps(data, ensure_ascii=False)
        )

    print()
    print("============================================================")
    print(" TWITCH NEUAUTORISIERUNG")
    print("============================================================")
    print("Öffne diese Adresse im Browser:")
    print(verification_uri)
    print()
    print("Oder gehe zu:")
    print(verification_uri)
    print()
    print("Code:", user_code)
    print()
    print("Erforderliche Berechtigungen:")
    for scope in TWITCH_EVENTSUB_SCOPES:
        print("  -", scope)
    print("============================================================")
    print()

    deadline = time.time() + expires_in
    current_interval = max(5, interval)

    while time.time() < deadline:
        time.sleep(current_interval)

        token_req = urllib.request.Request(
            "https://id.twitch.tv/oauth2/token",
            data=urllib.parse.urlencode({
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(token_req, timeout=15) as r:
                token_data = json.loads(r.read().decode("utf-8"))

            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")
            if not access_token:
                raise RuntimeError(
                    "Twitch hat keinen access_token zurückgegeben: "
                    + json.dumps(token_data, ensure_ascii=False)
                )

            config["twitch_oauth_token"] = access_token
            if refresh_token:
                config["twitch_refresh_token"] = refresh_token

            granted = token_data.get("scope") or []
            config["twitch_oauth_scopes"] = granted

            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)

            print()
            print("[TWITCH AUTH] NEUER TOKEN GESPEICHERT")
            print("[TWITCH AUTH] Scopes:", ", ".join(granted))
            print("[TWITCH AUTH] unified_chat_config.json wurde aktualisiert.")
            print("[TWITCH AUTH] Bridge kann jetzt normal gestartet werden.")
            return True

        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass

            # Twitch uses authorization_pending / slow_down while waiting.
            try:
                err = json.loads(body).get("message") or json.loads(body).get("error")
            except Exception:
                err = body or ""

            if err in {"authorization_pending", "authorization pending"}:
                print("[TWITCH AUTH] Warte auf Autorisierung ...")
                continue

            if err in {"slow_down", "slow down"}:
                current_interval += 5
                print(
                    f"[TWITCH AUTH] Twitch verlangt langsameres Polling "
                    f"({current_interval}s) ..."
                )
                continue

            if e.code in {400, 401} and "authorization_pending" in body:
                print("[TWITCH AUTH] Warte auf Autorisierung ...")
                continue

            raise RuntimeError(
                f"Twitch Token-Abruf HTTP {e.code}: {body or e.reason}"
            ) from e

    raise RuntimeError("Twitch Device Authorization ist abgelaufen.")


def twitch_print_auth_status(config):
    """Print the current token identity/scopes without exposing the token."""
    auth = twitch_validate_token(config)
    print(
        "[TWITCH AUTH] Token gültig | "
        f"login={auth['login']} | user_id={auth['user_id']} | "
        f"expires_in={auth['expires_in']}s"
    )
    print(
        "[TWITCH AUTH] Scopes: "
        + (", ".join(sorted(auth["scopes"])) if auth["scopes"] else "KEINE")
    )
    return auth


def twitch_eventsub_loop(config):
    """Native Twitch EventSub WebSocket client; completely independent of Lumia."""
    if websocket is None:
        print("[TWITCH EVENTSUB] websocket-client fehlt.")
        return

    channel = str(config.get("twitch_channel", "")).strip().lower()
    client_id = str(config.get("twitch_client_id", "")).strip()
    token = str(config.get("twitch_oauth_token", "")).strip()

    if not client_id or not token or token.startswith("HIER_"):
        print("[TWITCH EVENTSUB] Deaktiviert: twitch_client_id oder twitch_oauth_token fehlt.")
        return

    # Validate the actual user token before opening EventSub.
    try:
        auth = twitch_validate_token(config)
    except Exception as e:
        print("[TWITCH AUTH] Tokenprüfung fehlgeschlagen:", repr(e))
        return

    granted = set(auth["scopes"])
    print(
        "[TWITCH AUTH] Token gültig | "
        f"login={auth['login']} | user_id={auth['user_id']} | "
        f"expires_in={auth['expires_in']}s"
    )
    print(
        "[TWITCH AUTH] Scopes: " +
        (", ".join(sorted(granted)) if granted else "KEINE")
    )

    broadcaster_id = twitch_eventsub_user_id(config, channel)
    if not broadcaster_id:
        print("[TWITCH EVENTSUB] Deaktiviert: Broadcaster-ID konnte nicht ermittelt werden.")
        return

    # Twitch's current EventSub authorization requirements.
    # Source: Twitch EventSub Subscription Types documentation.
    event_specs = [
        ("channel.follow", "2", {"moderator:read:followers"},
         {"broadcaster_user_id": broadcaster_id,
          "moderator_user_id": auth["user_id"]}),
        ("channel.subscribe", "1", {"channel:read:subscriptions"},
         {"broadcaster_user_id": broadcaster_id}),
        ("channel.subscription.gift", "1", {"channel:read:subscriptions"},
         {"broadcaster_user_id": broadcaster_id}),
        ("channel.subscription.message", "1", {"channel:read:subscriptions"},
         {"broadcaster_user_id": broadcaster_id}),
        ("channel.cheer", "1", {"bits:read"},
         {"broadcaster_user_id": broadcaster_id}),
        ("channel.channel_points_custom_reward_redemption.add", "1",
         {"channel:read:redemptions", "channel:manage:redemptions"},
         {"broadcaster_user_id": broadcaster_id}),
        ("channel.raid", "1", set(),
         {"to_broadcaster_user_id": broadcaster_id}),
    ]

    print(f"[TWITCH EVENTSUB] aktiv | broadcaster_id={broadcaster_id}")

    while True:
        ws = None
        try:
            ws = websocket.create_connection(
                "wss://eventsub.wss.twitch.tv/ws",
                timeout=15,
                origin="https://www.twitch.tv",
            )
            ws.settimeout(15)

            session_id = None
            reconnect_url = None
            deadline = time.time() + 20

            while time.time() < deadline and not session_id:
                raw = ws.recv()
                if not raw:
                    continue
                obj = json.loads(raw)
                message_type = ((obj.get("metadata") or {}).get("message_type") or "")

                if message_type == "session_welcome":
                    session = (obj.get("payload") or {}).get("session") or {}
                    session_id = session.get("id")
                    print(f"[TWITCH EVENTSUB] Session verbunden: {session_id}")
                    break

                if message_type == "session_reconnect":
                    reconnect_url = (
                        (obj.get("payload") or {}).get("session", {})
                        .get("reconnect_url")
                    )
                    break

                print(
                    "[TWITCH EVENTSUB RAW]",
                    json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
                )

            if not session_id:
                if reconnect_url:
                    ws.close()
                    ws = websocket.create_connection(
                        reconnect_url,
                        timeout=15,
                        origin="https://www.twitch.tv",
                    )
                    raw = ws.recv()
                    obj = json.loads(raw)
                    session_id = (
                        (obj.get("payload") or {}).get("session") or {}
                    ).get("id")
                if not session_id:
                    raise RuntimeError("Keine EventSub-Session-ID erhalten")

            for event_type, version, required_scopes, condition in event_specs:
                # For the custom redemption event Twitch accepts either of the
                # two listed scopes. All other entries require every listed scope.
                if event_type == "channel.channel_points_custom_reward_redemption.add":
                    has_scope = bool(granted & required_scopes)
                else:
                    has_scope = required_scopes.issubset(granted)

                if not has_scope:
                    if event_type == "channel.channel_points_custom_reward_redemption.add":
                        needed = "channel:read:redemptions ODER channel:manage:redemptions"
                    else:
                        needed = " + ".join(sorted(required_scopes)) or "keinen speziellen Scope"
                    print(
                        f"[TWITCH EVENTSUB] {event_type} übersprungen: "
                        f"Scope fehlt ({needed})"
                    )
                    continue

                # channel.follow requires moderator_user_id to match the user
                # access token when using WebSocket transport.
                if event_type == "channel.follow" and auth["user_id"] != broadcaster_id:
                    print(
                        "[TWITCH EVENTSUB] channel.follow übersprungen: "
                        "Der OAuth-Token gehört nicht zum Broadcaster/Moderator."
                    )
                    continue

                body = {
                    "type": event_type,
                    "version": version,
                    "condition": condition,
                    "transport": {
                        "method": "websocket",
                        "session_id": session_id,
                    },
                }

                try:
                    req = urllib.request.Request(
                        "https://api.twitch.tv/helix/eventsub/subscriptions",
                        data=json.dumps(body).encode("utf-8"),
                        headers={
                            "Client-ID": client_id,
                            "Authorization": "Bearer " + token,
                            "Content-Type": "application/json",
                        },
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=10) as r:
                        response = r.read().decode("utf-8")

                    try:
                        response_obj = json.loads(response)
                        created = (response_obj.get("data") or [{}])[0]
                        status = created.get("status")
                        cost = created.get("cost")
                        print(
                            f"[TWITCH EVENTSUB] abonniert: {event_type} "
                            f"| status={status} | cost={cost}"
                        )
                    except Exception:
                        print(f"[TWITCH EVENTSUB] abonniert: {event_type}")

                except urllib.error.HTTPError as e:
                    print(
                        f"[TWITCH EVENTSUB] {event_type} konnte nicht abonniert werden: "
                        f"{twitch_eventsub_http_error(e)}"
                    )
                except Exception as e:
                    print(
                        f"[TWITCH EVENTSUB] {event_type} konnte nicht abonniert werden: "
                        f"{repr(e)}"
                    )

            while True:
                raw = ws.recv()
                if not raw:
                    continue
                obj = json.loads(raw)
                metadata = obj.get("metadata") or {}
                message_type = metadata.get("message_type") or ""

                if message_type == "session_keepalive":
                    continue

                if message_type == "session_reconnect":
                    print("[TWITCH EVENTSUB] Twitch verlangt Reconnect.")
                    break

                if message_type == "revocation":
                    payload = obj.get("payload") or {}
                    print(
                        "[TWITCH EVENTSUB] Subscription widerrufen:",
                        json.dumps(payload, ensure_ascii=False)
                    )
                    continue

                if message_type == "notification":
                    payload = obj.get("payload") or {}
                    subscription = payload.get("subscription") or {}
                    event_data = payload.get("event") or {}
                    event_type = subscription.get("type") or "unknown"
                    print("[TWITCH EVENTSUB] EVENT", event_type)
                    msg = twitch_eventsub_emit(event_type, event_data, config)
                    print(
                        "[TWITCH EVENT]",
                        json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
                    )
                    publish(msg)
                    continue

                print(
                    "[TWITCH EVENTSUB RAW]",
                    json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
                )

        except Exception as e:
            print("[TWITCH EVENTSUB] Verbindung/Fehler:", repr(e))
        finally:
            try:
                if ws:
                    ws.close()
            except Exception:
                pass
        time.sleep(5)


def twitch_loop(config):
    if websocket is None:
        print(
            "[TWITCH] websocket-client fehlt. "
            "Installieren: py -m pip install websocket-client"
        )
        return

    channel = str(
        config.get("twitch_channel", "")
    ).strip().lower()

    token = str(
        config.get("twitch_oauth_token", "")
    ).strip()

    if not token or token.startswith("HIER_"):
        print(
            "[TWITCH] Deaktiviert: twitch_oauth_token fehlt."
        )
        return

    nickname = str(
        config.get("twitch_login", "")
    ).strip().lower()

    if not nickname:
        print(
            "[TWITCH] Deaktiviert: twitch_login fehlt. "
            "Das ist der Twitch-Login des Accounts, der den OAuth-Token besitzt."
        )
        return

    while True:
        ws = None

        try:
            print(
                f"[TWITCH] Verbinde mit #{channel} ..."
            )

            ws = websocket.create_connection(
                "wss://irc-ws.chat.twitch.tv:443",
                timeout=10
            )

            ws.send(
                "CAP REQ :twitch.tv/membership "
                "twitch.tv/tags twitch.tv/commands\r\n"
            )
            ws.send(f"PASS oauth:{token}\r\n")
            ws.send(f"NICK {nickname}\r\n")
            ws.send(f"JOIN #{channel}\r\n")

            print(
                f"[TWITCH] Verbunden mit #{channel}"
            )

            buffer = ""

            reconnect_requested = False

            while True:
                try:
                    data = ws.recv()
                except websocket.WebSocketTimeoutException:
                    # Idle Twitch chat is normal. Keep the connection alive.
                    continue

                if data is None:
                    raise RuntimeError("Twitch Verbindung beendet")

                buffer += data

                while "\r\n" in buffer:
                    line, buffer = buffer.split("\r\n", 1)

                    if not line:
                        continue

                    # Twitch may ask IRC clients to reconnect for server
                    # maintenance. Leave this socket immediately; the outer
                    # loop reconnects and rejoins the configured channel.
                    if twitch_is_reconnect_command(line):
                        print(
                            "[TWITCH] Server fordert eine neue Verbindung an. "
                            "Verbinde erneut ..."
                        )
                        reconnect_requested = True
                        break

                    # Twitch IRC keepalive.
                    if line.startswith("PING"):
                        ws.send("PONG :tmi.twitch.tv\r\n")
                        continue

                    # Diagnostic: system events must never disappear silently.
                    # USERNOTICE contains subscriptions, gift subs, raids, etc.
                    # PRIVMSG may contain Bits via the "bits" tag.
                    if " USERNOTICE #" in line:
                        print("[TWITCH RAW USERNOTICE]", line)
                    elif " PRIVMSG #" in line:
                        twitch_tags_preview = twitch_parse_tags(line)
                        if twitch_tags_preview.get("bits"):
                            print("[TWITCH RAW BITS]", line)

                    msg = twitch_parse_message(line, channel)

                    if msg:
                        if not msg.get("avatar") and msg.get("userId"):
                            msg["avatar"] = twitch_avatar(
                                config,
                                msg.get("userId")
                            )

                        if msg.get("event") == "system":
                            print(
                                f"[TWITCH EVENT] {msg.get('eventType', 'system')} | "
                                f"{msg.get('message', '')}"
                            )
                        else:
                            print(
                                f"[TWITCH] {msg['username']}: "
                                f"{msg['message']}"
                            )

                        publish(msg)

                if reconnect_requested:
                    break

        except Exception as e:
            print(
                "[TWITCH] Verbindung/Fehler:",
                repr(e)
            )

        finally:
            try:
                if ws:
                    ws.close()
            except Exception:
                pass

        print("[TWITCH] Neuer Verbindungsversuch in 5 Sekunden ...")
        time.sleep(5)


# ============================================================
# KICK
# ============================================================

KICK_PUSHER_URL_TEMPLATE = (
    "wss://ws-us2.pusher.com/app/"
    "{app_key}?protocol=7&client=js&version=8.4.0&flash=false"
)
KICK_REALTIME_BASE = "https://web.kick.com/api/v1/realtime"

def kick_http_json(url, timeout=15):
    """Fetch JSON from Kick without external curl/browser dependencies."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": "https://kick.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def kick_resolve_chatroom(slug):
    """Resolve the public Kick chatroom ID directly with Python HTTP."""
    endpoints = [
        f"https://kick.com/api/v1/channels/{slug}/chatroom",
        f"https://kick.com/api/v2/channels/{slug}",
        f"https://kick.com/api/v1/channels/{slug}",
    ]

    for url in endpoints:
        try:
            data = kick_http_json(url)
            if isinstance(data, dict):
                if isinstance(data.get("chatroom"), dict):
                    cid = data["chatroom"].get("id")
                    if cid:
                        return int(cid)
                if url.endswith("/chatroom") and data.get("id"):
                    return int(data["id"])
        except Exception as e:
            print(f"[KICK] Auflösung fehlgeschlagen ({url}): {repr(e)}")

    return None


def kick_resolve_channel_id(slug):
    """Resolve Kick's channel ID, which differs from its chatroom ID."""
    url = f"https://kick.com/api/v2/channels/{quote(slug, safe='')}"
    try:
        data = kick_http_json(url)
    except Exception as exc:
        print("[KICK] Channel-ID konnte nicht aufgelöst werden:", repr(exc))
        return None

    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    if not isinstance(data, dict):
        return None
    try:
        value = data.get("id")
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def kick_realtime_json(path, payload, timeout=15):
    """Make one anonymous JSON request to Kick's negotiated realtime API."""
    if not isinstance(path, str) or not path.startswith("/") or ".." in path:
        raise ValueError("Ungültiger Kick-Realtime-Pfad")
    request = urllib.request.Request(
        KICK_REALTIME_BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://kick.com",
            "Referer": "https://kick.com/",
            "User-Agent": "Mozilla/5.0",
            "x-app-platform": "web",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Kick-Realtime-HTTP-Fehler ({exc.code})") from None
    if not isinstance(result, dict):
        raise RuntimeError("Kick-Realtime-Antwort ist kein JSON-Objekt")
    return result


def kick_negotiate_chat_connection(client_id, channel_id):
    """Request the channel-chat transport and accept only a Kick Centrifugo URL."""
    payload = {
        "client": {"id": str(client_id), "type": "web"},
        "capabilities": {
            "accepted_providers": [
                {"provider": "pusher"},
                {"provider": "centrifugo"},
            ]
        },
    }
    response = kick_realtime_json(
        f"/channels/{int(channel_id)}/chat/connection", payload
    )
    data = response.get("data")
    if not isinstance(data, dict) or data.get("mode") != "websocket":
        raise RuntimeError("Kick hat keine WebSocket-Verbindung ausgehandelt")
    connections = data.get("connections", []) if isinstance(data, dict) else []
    for connection in connections:
        if not isinstance(connection, dict) or connection.get("provider") != "centrifugo":
            continue
        credentials = connection.get("credentials")
        url = credentials.get("url") if isinstance(credentials, dict) else None
        if not isinstance(url, str):
            continue
        try:
            parsed = urlparse(url)
        except ValueError:
            continue
        host = parsed.hostname or ""
        if (
            parsed.scheme == "wss"
            and host.startswith("realtime.")
            and host.endswith(".platform.kick.com")
            and parsed.netloc == host
            and parsed.path == "/connection/websocket"
            and not parsed.query
            and not parsed.fragment
        ):
            return url
    raise RuntimeError("Kick lieferte keine gültige Centrifugo-Chatverbindung")


def kick_connection_token(client_id):
    """Fetch a short-lived anonymous token; never print or persist its value."""
    response = kick_realtime_json("/auth/connection", {"client_id": str(client_id)})
    data = response.get("data")
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token.strip():
        raise RuntimeError("Kick lieferte kein anonymes Realtime-Token")
    return token


def _kick_centrifugo_lines(raw):
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return []
    return [line for line in raw.splitlines() if line.strip()]


def _kick_centrifugo_wait_ack(
    ws, command_id, command_name, timeout=10, pong_required=False,
    activity_callback=None,
):
    """Wait for a Centrifugo command reply while retaining intervening pushes."""
    deadline = time.monotonic() + timeout
    pending = []
    while time.monotonic() < deadline:
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            continue
        if raw is None:
            raise RuntimeError("Kick Centrifugo-WebSocket wurde beendet")
        if activity_callback is not None:
            activity_callback()
        lines = _kick_centrifugo_lines(raw)
        if not lines:
            if pong_required:
                ws.send("{}")
            continue
        for index, line in enumerate(lines):
            try:
                frame = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(frame, dict):
                continue
            if not frame:
                if pong_required:
                    ws.send("{}")
                continue
            push = frame.get("push")
            if isinstance(push, dict) and ("disconnect" in push or "unsubscribe" in push):
                raise RuntimeError("Kick Centrifugo hat die Verbindung geschlossen")
            if frame.get("id") == command_id:
                error = frame.get("error")
                if isinstance(error, dict):
                    code = error.get("code", "?")
                    message = error.get("message", "command rejected")
                    raise RuntimeError(
                        f"Kick Centrifugo {command_name}-Fehler {code}: {message}"
                    )
                result = frame.get(command_name)
                if not isinstance(result, dict):
                    raise RuntimeError(
                        f"Kick Centrifugo bestätigte {command_name} nicht"
                    )
                pending.extend(lines[index + 1:])
                return result, pending
            if push is not None or frame.get("id") in (None, 0):
                pending.append(line)
    raise RuntimeError(f"Kick Centrifugo bestätigte {command_name} nicht rechtzeitig")


def _kick_centrifugo_refresh_at(reply):
    if not reply.get("expires"):
        return float("inf")
    try:
        ttl = int(reply.get("ttl", 0))
    except (TypeError, ValueError):
        ttl = 0
    if ttl < 1:
        raise RuntimeError("Kick Centrifugo meldete ein abgelaufenes Token")
    return time.monotonic() + max(ttl * 0.8, 0.1)

def kick_fetch_avatar(sender):
    """
    Kick chat messages do not contain a profile-image URL.
    Resolve it from Kick's public web API and cache it by user id.
    """
    user_id=sender.get("id")
    username=(sender.get("username") or sender.get("slug") or "").strip()

    if not user_id and not username:
        return ""

    cache_key="kick:" + str(user_id or username)
    cached=cached_avatar(cache_key)
    if cached:
        return cached

    candidates=[]

    if user_id:
        candidates.extend([
            f"https://kick.com/api/v2/users/{user_id}",
            f"https://kick.com/api/v1/users/{user_id}",
        ])

    if username:
        candidates.extend([
            f"https://kick.com/api/v2/channels/{quote(username, safe='')}",
            f"https://kick.com/api/v1/channels/{quote(username, safe='')}",
        ])

    for url in candidates:
        try:
            data = kick_http_json(url, timeout=10)

            # Search only known avatar/profile-image fields recursively.
            def find_avatar(obj):
                if isinstance(obj, dict):
                    for key in (
                        "profile_picture",
                        "profile_pic",
                        "profile_image",
                        "profile_image_url",
                        "avatar",
                        "avatar_url",
                        "profile_picture_url"
                    ):
                        value=obj.get(key)
                        if isinstance(value,str) and value.startswith(("http://","https://")):
                            return value

                    for value in obj.values():
                        found=find_avatar(value)
                        if found:
                            return found

                elif isinstance(obj,list):
                    for value in obj:
                        found=find_avatar(value)
                        if found:
                            return found

                return ""

            avatar=find_avatar(data)

            if avatar:
                set_cached_avatar(cache_key, avatar)
                return avatar

        except Exception:
            continue

    return ""


def _kick_json_payload(outer):
    payload = outer.get("data", {}) if isinstance(outer, dict) else {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return {}
    return payload if isinstance(payload, dict) else {}


def _kick_find_first(obj, keys):
    """Find the first useful value for one of the supplied keys recursively."""
    if isinstance(obj, dict):
        for key in keys:
            value = obj.get(key)
            if value not in (None, ""):
                return value
        for value in obj.values():
            found = _kick_find_first(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _kick_find_first(value, keys)
            if found not in (None, ""):
                return found
    return None


def _kick_system_message(outer):
    """Normalize non-chat Kick realtime events for the common overlay schema."""
    payload = _kick_json_payload(outer)
    event_name = str(outer.get("event", ""))
    lower_event = event_name.lower()

    # Ignore Pusher protocol/control messages.
    if lower_event.startswith("pusher:") or lower_event.startswith("pusher_internal:"):
        return None

    # The current Kick public event API documents reward redemptions and KICKs
    # as channel events. The legacy chat websocket can expose additional app
    # events as well. We intentionally use payload-shape detection here because
    # Kick has changed the exact websocket event names over time.
    reward = payload.get("reward")
    redeemer = payload.get("redeemer") or payload.get("user") or payload.get("sender")
    gift = payload.get("gift")

    is_reward = (
        "reward" in lower_event
        or isinstance(reward, dict)
        or "redemption" in lower_event
        or (
            isinstance(payload, dict)
            and any(k in payload for k in ("user_input", "redeemed_at"))
            and isinstance(reward, dict)
        )
    )

    is_kicks = (
        "kicks" in lower_event
        or "kickgift" in lower_event
        or isinstance(gift, dict) and any(
            k in gift for k in ("amount", "name", "type", "tier")
        )
    )

    is_sub = (
        "subscription" in lower_event
        or "subscriber" in lower_event
        or "subscribed" in lower_event
    )

    if not (is_reward or is_kicks or is_sub):
        return None

    if isinstance(redeemer, dict):
        username = (
            redeemer.get("username")
            or redeemer.get("slug")
            or "Unbekannt"
        )
        user_id = redeemer.get("user_id") or redeemer.get("id")
        avatar = (
            redeemer.get("profile_picture")
            or redeemer.get("profile_image")
            or redeemer.get("avatar")
            or ""
        )
    else:
        username = str(
            _kick_find_first(payload, ("username", "user_name", "slug"))
            or "Unbekannt"
        )
        user_id = _kick_find_first(payload, ("user_id", "userId", "id"))
        avatar = str(
            _kick_find_first(payload, ("profile_picture", "profile_image", "avatar"))
            or ""
        )

    if is_reward:
        event_type = "reward"
        reward_title = ""
        cost = None
        status = payload.get("status") or ""
        user_input = payload.get("user_input") or ""

        if isinstance(reward, dict):
            reward_title = str(reward.get("title") or reward.get("name") or "")
            cost = reward.get("cost")

        if reward_title:
            message = f"{username} hat {reward_title} eingelöst"
        else:
            message = f"{username} hat eine Belohnung eingelöst"

        if user_input:
            message += f": {user_input}"

        tts = f"{username} hat {reward_title or 'eine Belohnung'} eingelöst."
        if cost is not None:
            tts = f"{username} hat {reward_title or 'eine Belohnung'} für {cost} Punkte eingelöst."

        extra = {
            "rewardTitle": reward_title,
            "cost": cost,
            "status": status,
            "userInput": user_input,
        }

    elif is_kicks:
        event_type = "kicks"
        amount = None
        gift_name = "KICKs"
        gift_message = ""
        if isinstance(gift, dict):
            amount = gift.get("amount")
            gift_name = str(gift.get("name") or "KICKs")
            gift_message = str(gift.get("message") or "")
        else:
            amount = _kick_find_first(payload, ("amount", "quantity", "count"))

        if amount is not None:
            message = f"{username} hat {amount} KICKs gesendet"
            tts = f"{username} hat {amount} KICKs gesendet."
        else:
            message = f"{username} hat {gift_name} gesendet"
            tts = f"{username} hat {gift_name} gesendet."

        if gift_message:
            message += f": {gift_message}"
        extra = {
            "amount": amount,
            "giftName": gift_name,
        }

    else:
        event_type = "subscription"
        duration = _kick_find_first(payload, ("duration", "months"))
        if duration:
            message = f"{username} hat abonniert ({duration} Monat(e))"
        else:
            message = f"{username} hat abonniert"
        tts = f"{username} hat abonniert."
        extra = {"duration": duration}

    event_id = (
        payload.get("id")
        or payload.get("redemption_id")
        or payload.get("message_id")
        or f"{event_name}:{time.time_ns()}"
    )

    return {
        "event": "system",
        "platform": "kick",
        "eventType": event_type,
        "id": str(event_id),
        "userId": user_id,
        "username": username,
        "message": message,
        "tts": tts,
        "avatar": avatar,
        "timestamp": payload.get("created_at") or payload.get("redeemed_at"),
        "badges": [],
        "moderator": False,
        "subscriber": event_type == "subscription",
        "kickEventName": event_name,
        **extra,
    }


def kick_parse_event(raw):
    try:
        outer = json.loads(raw)
    except Exception:
        print("[KICK RAW] Ungültiges JSON:", repr(raw))
        return ("control", None, None)

    if not isinstance(outer, dict):
        return ("control", None, None)

    if not outer:
        # Centrifugo uses an empty JSON object as a heartbeat ping.
        return ("pong", None, None)

    push = outer.get("push")
    if isinstance(push, dict):
        if "disconnect" in push or "unsubscribe" in push:
            return ("disconnect", None, push)
        publication = push.get("pub")
        if not isinstance(publication, dict):
            return ("control", None, {"outer": outer})
        event_data = publication.get("data")
        if isinstance(event_data, str):
            try:
                event_data = json.loads(event_data)
            except Exception:
                return ("control", None, {"outer": outer})
        if not isinstance(event_data, dict):
            return ("control", None, {"outer": outer})
        outer = event_data

    event=outer.get("event", "")

    if not event:
        # Centrifugo command acknowledgements and unrelated pushes are controls.
        return ("control", None, {"outer": outer})

    if event == "pusher:ping":
        return ("pong", None, None)

    if event in (
        "pusher:connection_established",
        "pusher_internal:subscription_succeeded",
        "pusher:subscription_succeeded"
    ):
        return ("control", None, None)

    # IMPORTANT: Do not silently throw away non-chat Kick events anymore.
    # This is what the previous parser did and why reward/system events were
    # completely invisible in the console.
    if event not in (
        "App\\Events\\ChatMessageEvent",
        "App\\Events\\ChatMessageSentEvent"
    ):
        system_event = _kick_system_message(outer)
        if system_event:
            return ("system", system_event, {"outer": outer, "payload": _kick_json_payload(outer)})

        print(
            "[KICK RAW EVENT]",
            json.dumps(outer, ensure_ascii=False, separators=(",", ":"))
        )
        return ("control", None, {"outer": outer, "payload": _kick_json_payload(outer)})

    payload=_kick_json_payload(outer)
    if not isinstance(payload, dict):
        return ("control", None, None)

    sender=payload.get("sender") or payload.get("user") or {}
    identity=sender.get("identity") or {}

    badges=identity.get("badges") or []
    badge_names=[]

    for badge in badges:
        if isinstance(badge, dict):
            name=badge.get("type") or badge.get("text")
        else:
            name=str(badge)
        if name:
            badge_names.append(name.upper())

    username=(
        sender.get("username")
        or sender.get("slug")
        or "Unbekannt"
    )

    message_id=payload.get("id") or payload.get("message_id")

    message={
        "platform":"kick",
        "id":message_id,
        "userId":sender.get("id"),
        "username":username,
        "message":payload.get("content") or payload.get("message") or "",
        "avatar": (
            kick_avatar_from_sender(sender)
            or kick_fetch_avatar(sender)
        ),
        "timestamp":payload.get("created_at"),
        "badges":badge_names,
        "moderator":any(
            "MOD" in b for b in badge_names
        ),
        "subscriber":any(
            "SUB" in b or "SUBSCRIB" in b for b in badge_names
        )
    }

    return ("message", message, {"outer": outer, "payload": payload, "sender": sender})


def find_kick_account(accounts, channel=None):
    """Find the Kick account from the shared accounts.json.

    Kick is intentionally sourced exclusively from accounts.json here.
    The runtime bridge config must not be used for Kick channel/chatroom
    credentials, because the account manager is the single source of truth.
    """
    wanted = str(channel or "").strip().lstrip("@").lower()
    for account in accounts:
        if not isinstance(account, dict):
            continue
        if str(account.get("platform") or "").strip().lower() != "kick":
            continue
        if not wanted:
            return account
        candidates = (
            account.get("channel"),
            account.get("display_name"),
            account.get("username"),
            account.get("slug"),
        )
        if any(str(value or "").strip().lstrip("@").lower() == wanted for value in candidates):
            return account
    return None


def kick_account_settings():
    """Return Kick settings exclusively from accounts.json."""
    accounts = load_accounts_file()
    account = find_kick_account(accounts)
    if not account:
        raise RuntimeError("Kein Kick-Account in accounts.json gefunden.")

    channel = str(
        account.get("channel")
        or account.get("username")
        or account.get("slug")
        or account.get("display_name")
        or ""
    ).strip().lstrip("@").lower()

    auth = account.get("auth") if isinstance(account.get("auth"), dict) else {}

    raw_chatroom_id = (
        account.get("chatroom_id")
        or account.get("chatroomId")
        or account.get("chatroomID")
        or auth.get("chatroom_id")
        or auth.get("chatroomId")
    )
    try:
        chatroom_id = int(raw_chatroom_id) if raw_chatroom_id not in (None, "") else None
    except (TypeError, ValueError):
        chatroom_id = None

    app_key = str(
        account.get("pusher_app_key")
        or account.get("kick_pusher_app_key")
        or auth.get("pusher_app_key")
        or auth.get("kick_pusher_app_key")
        or "32cbd69e4b950bf97679"
    ).strip()

    return account, channel, chatroom_id, app_key


def kick_loop(config):
    # IMPORTANT: Kick account data comes exclusively from accounts.json.
    # Runtime-only data such as the chatroom ID is resolved automatically.
    try:
        account, slug, chatroom_id, _app_key = kick_account_settings()
    except Exception as exc:
        print("[KICK] accounts.json konnte nicht geladen werden:", repr(exc))
        return

    if not slug:
        print("[KICK] Kick-Account in accounts.json enthält keinen Channel/Username.")
        return

    # chatroom_id is runtime data and is resolved from the public Kick channel
    # endpoint. It is deliberately not required in accounts.json.
    if not chatroom_id:
        print(f"[KICK] Löse Chatroom-ID für #{slug} auf ...")
        chatroom_id = kick_resolve_chatroom(slug)

    if not chatroom_id:
        print(f"[KICK] Chatroom-ID für #{slug} konnte nicht automatisch ermittelt werden.")
        return

    channel_id = kick_resolve_channel_id(slug)
    if not channel_id:
        print(f"[KICK] Channel-ID für #{slug} konnte nicht automatisch ermittelt werden.")
        return

    print(
        f"[KICK] Account aus accounts.json: #{slug} | "
        f"Channel-ID={channel_id} | Chatroom-ID={chatroom_id}"
    )
    client_id = str(uuid.uuid4())

    while True:
        ws=None

        try:
            if websocket is None:
                print(
                    "[KICK] websocket-client fehlt. "
                    "Installieren: py -m pip install websocket-client"
                )
                return

            print(
                f"[KICK] Verbinde mit #{slug} ..."
            )

            ws_url = kick_negotiate_chat_connection(client_id, channel_id)
            token = kick_connection_token(client_id)
            ws=websocket.create_connection(
                ws_url,
                timeout=10,
                origin="https://kick.com"
            )
            ws.settimeout(1.0)

            command_id = 1
            ws.send(json.dumps({
                "id": command_id,
                "connect": {
                    "token": token,
                    "name": "multichat",
                    "version": "1",
                },
            }))
            connect_reply, pending_frames = _kick_centrifugo_wait_ack(
                ws, command_id, "connect"
            )
            pong_required = bool(connect_reply.get("pong"))
            try:
                ping_seconds = int(connect_reply.get("ping", 0))
            except (TypeError, ValueError):
                ping_seconds = 0
            heartbeat_timeout = ping_seconds + 10 if ping_seconds > 0 else 180
            refresh_at = _kick_centrifugo_refresh_at(connect_reply)

            command_id += 1
            ws.send(json.dumps({
                "id": command_id,
                "subscribe": {"channel": f"chatrooms.{chatroom_id}.v2"},
            }))
            _subscribe_reply, subscribe_frames = _kick_centrifugo_wait_ack(
                ws, command_id, "subscribe", pong_required=pong_required
            )
            pending_frames.extend(subscribe_frames)

            print(
                f"[KICK] Centrifugo-Chat für #{slug} abonniert"
            )
            last_received = time.monotonic()

            def mark_kick_activity():
                nonlocal last_received
                last_received = time.monotonic()

            while True:
                if time.monotonic() >= refresh_at:
                    token = kick_connection_token(client_id)
                    command_id += 1
                    ws.send(json.dumps({
                        "id": command_id,
                        "refresh": {"token": token},
                    }))
                    refresh_reply, refresh_frames = _kick_centrifugo_wait_ack(
                        ws,
                        command_id,
                        "refresh",
                        pong_required=pong_required,
                        activity_callback=mark_kick_activity,
                    )
                    refresh_at = _kick_centrifugo_refresh_at(refresh_reply)
                    pending_frames.extend(refresh_frames)
                    continue

                if pending_frames:
                    raw = pending_frames.pop(0)
                else:
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        if time.monotonic() - last_received > heartbeat_timeout:
                            raise RuntimeError("Kick Centrifugo-Heartbeat blieb aus")
                        continue
                if raw is None:
                    raise RuntimeError(
                        "Kick WebSocket beendet"
                    )

                frame_lines = _kick_centrifugo_lines(raw)
                if not frame_lines:
                    if pong_required:
                        ws.send("{}")
                    last_received = time.monotonic()
                    continue

                for frame_line in frame_lines:
                    try:
                        frame = json.loads(frame_line)
                    except Exception:
                        frame = None
                    if isinstance(frame, dict) and isinstance(frame.get("id"), int) and frame["id"] > 0:
                        error = frame.get("error")
                        if isinstance(error, dict):
                            raise RuntimeError(
                                f"Kick Centrifugo-Befehl fehlgeschlagen: "
                                f"{error.get('code', '?')} {error.get('message', '')}"
                            )
                        continue

                    kind, msg, _debug_data = kick_parse_event(frame_line)
                    last_received = time.monotonic()

                    if kind == "pong":
                        if pong_required:
                            ws.send("{}")
                        continue
                    if kind == "disconnect":
                        raise RuntimeError("Kick Centrifugo hat die Verbindung geschlossen")
                    if kind == "message" and msg:
                        print(f"[KICK] {msg['username']}: {msg['message']}")
                        publish(msg)
                    elif kind == "system" and msg:
                        print(
                            f"[KICK EVENT] {msg.get('eventType', 'system')} | "
                            f"{msg.get('message', '')}"
                        )
                        publish(msg)

        except Exception as e:
            print(
                "[KICK] Verbindung/Fehler:",
                repr(e)
            )

        finally:
            try:
                if ws:
                    ws.close()
            except Exception:
                pass

        print(
            "[KICK] Neuer Verbindungsversuch in 5 Sekunden ..."
        )
        time.sleep(5)


# ============================================================
# TIKTOK LIVE
# ============================================================

TIKTOK_WS_IDLE_TIMEOUT_SECONDS = 120.0
TIKTOK_WS_WATCH_INTERVAL_SECONDS = 15.0


def _tiktok_install_ws_activity_monitor(client, on_activity):
    """Track raw TikTok WebSocket frames, including non-chat heartbeat frames.

    TikTokLive 7.x hides transport frames from its public event API. Its
    WebcastConnect iterator can be wrapped locally so the watchdog can tell a
    quiet chat from a socket that stopped receiving anything.
    """
    ws_client = getattr(client, "_ws", None)
    base_connect_class = getattr(ws_client, "_connect_generator_class", None)
    if ws_client is None or not isinstance(base_connect_class, type):
        return False

    class ActivityMonitoredConnect(base_connect_class):
        async def __aiter__(self):
            hook_installed = False
            async for item in super().__aiter__():
                if not hook_installed:
                    protocol = getattr(self, "ws", None)
                    original_recv = getattr(protocol, "recv", None)
                    if protocol is not None and callable(original_recv):
                        async def monitored_recv(*args, **kwargs):
                            payload = await original_recv(*args, **kwargs)
                            on_activity()
                            return payload

                        try:
                            protocol.recv = monitored_recv
                            hook_installed = True
                        except (AttributeError, TypeError):
                            pass
                yield item

    ws_client._connect_generator_class = ActivityMonitoredConnect
    return True


def _tiktok_start_ws_watchdog(client, activity_state):
    """Close a TikTok socket that stays open but stops delivering all frames."""
    async def watchdog():
        while True:
            await asyncio.sleep(TIKTOK_WS_WATCH_INTERVAL_SECONDS)
            if not getattr(client, "connected", False):
                return

            idle_seconds = time.monotonic() - activity_state["last_received"]
            if idle_seconds < TIKTOK_WS_IDLE_TIMEOUT_SECONDS:
                continue

            print(
                f"[TIKTOK] Seit {idle_seconds:.0f}s keine eingehenden "
                "WebSocket-Frames; Verbindung wird neu aufgebaut."
            )
            try:
                await client.disconnect()
            except Exception as e:
                print("[TIKTOK] Fehler beim automatischen Reconnect:", repr(e))
            return

    return asyncio.create_task(watchdog())


def _tiktok_cancel_ws_watchdog(client, activity_state):
    """Cancel the per-connection watchdog after TikTokLive.run() returns."""
    task = activity_state.get("watchdog_task")
    if task is None or task.done():
        return

    task.cancel()
    loop = getattr(client, "_owned_asyncio_loop", None)
    if loop is not None and not loop.is_running() and not loop.is_closed():
        try:
            loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
        except Exception as e:
            print("[TIKTOK] Watchdog konnte nicht sauber beendet werden:", repr(e))

def tiktok_avatar(user):
    try:
        avatar=getattr(user, "avatar_thumb", None)
        if avatar is None:
            avatar=getattr(user, "avatar_medium", None)
        if avatar is None:
            avatar=getattr(user, "avatar_larger", None)

        if isinstance(avatar,str):
            return avatar

        url=getattr(avatar, "url", None)
        if isinstance(url,str):
            return url

        urls=getattr(avatar, "url_list", None)
        if urls:
            return urls[0]

    except Exception:
        pass

    return ""



def _tt_public_dict(value, depth=0, seen=None):
    """Convert TikTokLive/betterproto objects to plain data.

    Generated TikTokLiveProto objects often use slots/properties instead of a
    normal __dict__. We therefore inspect annotations and a small set of
    known proto fields as well.
    """
    if seen is None:
        seen=set()

    if value is None or depth > 8:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    oid=id(value)
    if oid in seen:
        return None
    seen.add(oid)

    if isinstance(value, (list, tuple)):
        return [_tt_public_dict(x, depth + 1, seen) for x in value]

    if isinstance(value, dict):
        return {
            str(k): _tt_public_dict(v, depth + 1, seen)
            for k, v in value.items()
            if not str(k).startswith("_")
        }

    result={}

    # Generated proto classes normally expose their field names via
    # __annotations__ even when they have no useful __dict__.
    names=[]
    cls=type(value)

    for c in getattr(cls, "__mro__", (cls,)):
        ann=getattr(c, "__annotations__", {})
        if isinstance(ann, dict):
            names.extend(ann.keys())

    names.extend([
        "index","emote","id","name","display_name","text",
        "image","image_url","image_model","url","url_list",
        "uri","src","start","end","start_index","end_index",
        "emote_id","emote_list"
    ])

    # Keep ordering but remove duplicates.
    names=list(dict.fromkeys(names))

    for name in names:
        if str(name).startswith("_"):
            continue

        try:
            val=getattr(value,name)
        except Exception:
            continue

        if callable(val):
            continue

        # Skip empty/default values.
        if val is None or val == "" or val == []:
            continue

        try:
            result[str(name)] = _tt_public_dict(
                val, depth + 1, seen
            )
        except Exception:
            pass

    # Some generated objects do expose useful values through __dict__.
    try:
        fields=getattr(value,"__dict__",{})
        if isinstance(fields,dict):
            for key,val in fields.items():
                if str(key).startswith("_") or key in result:
                    continue
                try:
                    result[str(key)] = _tt_public_dict(
                        val, depth + 1, seen
                    )
                except Exception:
                    pass
    except Exception:
        pass

    return result



def _tt_find_strings(value, out=None, depth=0):
    if out is None:
        out = []

    if depth > 7:
        return out

    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            out.append(value)
        return out

    if isinstance(value, dict):
        for v in value.values():
            _tt_find_strings(v, out, depth + 1)
        return out

    if isinstance(value, (list, tuple)):
        for v in value:
            _tt_find_strings(v, out, depth + 1)
        return out

    return out


def _tt_direct_emote_url(item):
    """Try known TikTok Emote -> ImageModel URL paths first."""
    candidates=[]

    try:
        emote=getattr(item,"emote",None)
        if emote is not None:
            candidates.append(emote)

            for attr in (
                "image","image_model","image_url","image_model_v2",
                "sticker","resource","asset"
            ):
                try:
                    v=getattr(emote,attr,None)
                    if v is not None:
                        candidates.append(v)
                except Exception:
                    pass
    except Exception:
        pass

    candidates.append(item)

    def walk(v, depth=0):
        if v is None or depth>7:
            return ""

        if isinstance(v,str):
            if v.startswith(("http://","https://")) and (
                "tiktok" in v.lower()
                or "byteimg" in v.lower()
                or "ibytedtos" in v.lower()
                or "muscdn" in v.lower()
            ):
                return v
            return ""

        if isinstance(v,(list,tuple)):
            for x in v:
                u=walk(x,depth+1)
                if u: return u
            return ""

        if isinstance(v,dict):
            preferred=(
                "url_list","url","image_url","imageUrl","src",
                "uri","resource_uri","image"
            )
            for k in preferred:
                if k in v:
                    u=walk(v[k],depth+1)
                    if u: return u
            for x in v.values():
                u=walk(x,depth+1)
                if u: return u
            return ""

        # Proto object: inspect known image fields directly.
        for attr in (
            "url_list","url","image_url","imageUrl","src",
            "uri","resource_uri","image"
        ):
            try:
                x=getattr(v,attr,None)
            except Exception:
                continue
            if x is not None:
                u=walk(x,depth+1)
                if u: return u

        return ""

    for c in candidates:
        u=walk(c)
        if u:
            return u

    return ""



def _tt_extract_emotes(event):
    """
    TikTokLive v7 / TikTokLiveProto v3:
      CommentEvent.emotes -> EmoteWithIndex[]
      EmoteWithIndex.index -> position in comment
      EmoteWithIndex.emote.image.url_list -> image URL

    The proto schema defines exactly these fields. We use them directly
    instead of trying to infer the generated object's structure.
    """
    result=[]

    try:
        items=getattr(event, "emotes", None) or []
    except Exception:
        items=[]

    content=str(
        getattr(event, "content", None)
        or getattr(event, "comment", None)
        or ""
    )

    for item in items:
        try:
            emote=getattr(item, "emote", None)
            if emote is None:
                continue

            # v3 schema: index + emote.image.url_list
            index=getattr(item, "index", None)
            try:
                index=int(index)
            except Exception:
                index=None

            image=getattr(emote, "image", None)
            url=""

            if image is not None:
                for attr in ("url_list", "url", "image_url"):
                    try:
                        value=getattr(image, attr, None)
                    except Exception:
                        value=None

                    if isinstance(value, (list,tuple)) and value:
                        value=value[0]

                    if isinstance(value,str) and value.startswith(("http://","https://")):
                        url=value
                        break

            # Some generated versions expose image_url directly.
            if not url:
                for attr in ("image_url","imageUrl"):
                    try:
                        value=getattr(emote, attr, None)
                    except Exception:
                        value=None
                    if isinstance(value,str) and value.startswith(("http://","https://")):
                        url=value
                        break

            # Emote ID is useful as a diagnostic/title.
            emote_id=getattr(emote, "emote_id", None)
            if emote_id is None:
                emote_id=getattr(emote, "emoteId", None)

            if index is None or not url:
                print(
                    "[TIKTOK] Emote-Daten unvollständig:",
                    "index=", index,
                    "url=", bool(url),
                    "id=", emote_id
                )
                continue

            # index is the character position. TikTok's comments seen by us
            # use [name] placeholders. Determine the exact token from content.
            start=index
            end=None

            if 0 <= start < len(content) and content[start] == "[":
                close=content.find("]", start+1)
                if close != -1:
                    end=close

            # If the exact token is not bracketed, use the next emote index
            # or the next whitespace as a conservative fallback.
            if end is None:
                next_indices=[]
                for other in items:
                    try:
                        oi=int(getattr(other,"index", -1))
                        if oi > start:
                            next_indices.append(oi)
                    except Exception:
                        pass

                if next_indices:
                    end=max(start, min(next_indices)-1)
                else:
                    end=min(len(content)-1, start)

            result.append({
                "url": url,
                "start": start,
                "end": end,
                "name": str(emote_id or ""),
                "emoteId": str(emote_id or "")
            })

        except Exception as ex:
            print("[TIKTOK] Emote parse warning:", repr(ex))

    result.sort(key=lambda x: (x["start"], x["end"]))
    return result



def tiktok_loop(config):
    if not TIKTOK_AVAILABLE:
        print(
            "[TIKTOK] TikTokLive fehlt. "
            "Installieren: py -m pip install -U TikTokLive"
        )
        return

    while True:
        client=None
        tiktok_started_at=time.time()

        # TikTok account is resolved from the shared accounts.json.
        # Do not use a separate TikTok runtime channel setting; use accounts.json.
        try:
            accounts = load_accounts_file()
            account, unique_id = find_tiktok_account(accounts)
        except Exception as e:
            print("[TIKTOK] accounts.json konnte nicht geladen werden:", repr(e))
            time.sleep(10)
            continue

        if not account or not unique_id:
            print("[TIKTOK] Kein TikTok-Account in accounts.json gefunden.")
            time.sleep(10)
            continue

        # TikTokLive expects the username without the leading @.
        unique_id = unique_id.lstrip("@")

        print(
            f"[TIKTOK] Account aus accounts.json: "
            f"{account.get('display_name') or account.get('channel')} "
            f"| unique_id=@{unique_id}"
        )
        tiktok_seen_ids=set()

        try:
            print(
                f"[TIKTOK] Verbinde mit {unique_id} ..."
            )

            client=TikTokLiveClient(
                unique_id=unique_id
            )
            tiktok_activity = {
                "last_received": time.monotonic(),
                "watchdog_started": False,
                "watchdog_task": None,
            }
            if not _tiktok_install_ws_activity_monitor(
                client,
                lambda: tiktok_activity.__setitem__("last_received", time.monotonic()),
            ):
                print(
                    "[TIKTOK] WebSocket-Überwachung nicht verfügbar; "
                    "verwende Ereignisüberwachung als Rückfall."
                )

            @client.on(ConnectEvent)
            async def _on_connect(event):
                tiktok_activity["last_received"] = time.monotonic()
                if not tiktok_activity["watchdog_started"]:
                    tiktok_activity["watchdog_task"] = _tiktok_start_ws_watchdog(
                        client, tiktok_activity
                    )
                    tiktok_activity["watchdog_started"] = True
                try:
                    viewer_count = int(getattr(client, "viewer_count", 0) or 0)
                    room_info = _tt_public_dict(getattr(client, "room_info", None)) or {}
                    follower_count = 0

                    def _find_numeric_key(obj, names):
                        if isinstance(obj, dict):
                            for k, v in obj.items():
                                key = str(k).lower().replace("-", "_")
                                if key in names:
                                    if isinstance(v, (int, float)):
                                        return int(v)
                                    if isinstance(v, str):
                                        cleaned = re.sub(r"[^0-9]", "", v)
                                        if cleaned:
                                            try:
                                                return int(cleaned)
                                            except ValueError:
                                                pass
                            for v in obj.values():
                                found = _find_numeric_key(v, names)
                                if found is not None:
                                    return found
                        elif isinstance(obj, list):
                            for v in obj:
                                found = _find_numeric_key(v, names)
                                if found is not None:
                                    return found
                        return None

                    follower_count = _find_numeric_key(
                        room_info,
                        {
                            "follower_count",
                            "followers_count",
                            "followerscount",
                            "followercount",
                            "fans_count",
                            "fanscount",
                        },
                    ) or 0

                    if follower_count <= 0:
                        follower_count = int(
                            get_social_status()["platforms"]["tiktok"].get(
                                "followers", 0
                            ) or 0
                        )

                    update_social_status(
                        "tiktok",
                        viewers=viewer_count,
                        followers=follower_count,
                        available=True,
                    )
                    print(
                        f"[SOCIAL STATUS] TIKTOK | LIVE | "
                        f"{viewer_count} Zuschauer | {follower_count} Follower"
                    )
                except Exception as e:
                    print("[SOCIAL STATUS] TikTok Connect Fehler:", repr(e))
                print(
                    f"[TIKTOK] Verbunden mit {unique_id}"
                )

            # TikTokLive provides the authoritative periodic live viewer count
            # through RoomUserSeqEvent. Keep the older viewer-update handlers
            # below as fallbacks for compatibility.
            try:
                @client.on(RoomUserSeqEvent)
                async def _on_room_user_seq(event, _client=client):
                    try:
                        value = getattr(event, "viewer_count", None)
                        if value is None:
                            value = getattr(event, "viewerCount", None)
                        if value is None:
                            value = getattr(event, "total", None)
                        if value is None:
                            value = getattr(event, "total_user", None)
                        if value is None:
                            value = getattr(event, "totalUser", None)
                        if value is None:
                            value = getattr(_client, "viewer_count", 0)
                        current = get_social_status()["platforms"]["tiktok"]
                        viewer_count = int(value or 0)
                        update_social_status(
                            "tiktok",
                            viewers=viewer_count,
                            followers=int(current.get("followers", 0) or 0),
                            available=True,
                        )
                        print(
                            f"[SOCIAL STATUS] TIKTOK | Viewer-Update | "
                            f"{viewer_count} Zuschauer | "
                            f"{int(current.get('followers', 0) or 0)} Follower"
                        )
                    except Exception as e:
                        print("[SOCIAL STATUS] TikTok RoomUserSeq Fehler:", repr(e))
            except Exception:
                pass

            # Keep the combined status synchronized with TikTokLive's
            # cached viewer count.
            for _event_name in ("viewer_update", "viewer_count_update"):
                try:
                    @client.on(_event_name)
                    async def _on_viewer_update(event, _client=client):
                        try:
                            value = getattr(
                                event,
                                "viewCount",
                                getattr(event, "viewer_count", None),
                            )
                            if value is None:
                                value = getattr(_client, "viewer_count", 0)
                            current = get_social_status()["platforms"]["tiktok"]
                            update_social_status(
                                "tiktok",
                                viewers=int(value or 0),
                                followers=int(current.get("followers", 0) or 0),
                                available=True,
                            )
                        except Exception as e:
                            print("[SOCIAL STATUS] TikTok Viewer-Update Fehler:", repr(e))
                except Exception:
                    pass

            @client.on(CommentEvent)
            async def _on_comment(event):
                try:
                    # TikTok can replay a small batch of already-existing
                    # room messages when the Webcast connection is opened.
                    # Ignore comments older than the moment this connection
                    # was established.
                    event_time=None
                    common=getattr(event, "common", None)
                    for attr in ("create_time", "createTime", "timestamp"):
                        try:
                            v=getattr(common, attr, None) if common is not None else None
                            if v is not None:
                                event_time=float(v)
                                # TikTok timestamps are normally milliseconds.
                                if event_time > 100000000000:
                                    event_time /= 1000.0
                                break
                        except Exception:
                            pass

                    if event_time is not None and event_time < tiktok_started_at:
                        return

                    msg_id=getattr(event, "msg_id", None) or getattr(event, "message_id", None)
                    msg_key=str(msg_id or "")
                    if msg_key:
                        if msg_key in tiktok_seen_ids:
                            return
                        tiktok_seen_ids.add(msg_key)
                        if len(tiktok_seen_ids)>5000:
                            tiktok_seen_ids.clear()

                    user=event.user
                    username=(
                        getattr(user,"nickname",None)
                        or getattr(user,"unique_id",None)
                        or "Unbekannt"
                    )

                    avatar=tiktok_avatar(user)

                    # TikTokLive exposes the comment's emotes separately on
                    # newer versions. Keep the plain comment text for now;
                    # native EmoteChatEvent handling can be added without
                    # changing the common message schema.
                    tiktok_emotes = _tt_extract_emotes(event)

                    if tiktok_emotes:
                        print(
                            "[TIKTOK] Emotes:",
                            [
                                {
                                    "id": x.get("emoteId",""),
                                    "start": x.get("start"),
                                    "end": x.get("end"),
                                    "url": x.get("url","")
                                }
                                for x in tiktok_emotes
                            ]
                        )

                    message={
                        "platform":"tiktok",
                        "id":str(
                            getattr(event,"msg_id","")
                            or getattr(event,"message_id","")
                            or ""
                        ),
                        "userId":str(
                            getattr(user,"user_id","")
                            or getattr(user,"unique_id","")
                            or username
                        ),
                        "username":username,
                        "message":str(
                            getattr(event,"comment","")
                            or getattr(event,"content","")
                            or ""
                        ),
                        "tiktokEmotes": tiktok_emotes,
                        "avatar":avatar,
                        "timestamp":None,
                        "badges":[],
                        "moderator":False,
                        "subscriber":False
                    }

                    print(
                        f"[TIKTOK] {username}: "
                        f"{message['message']}"
                    )
                    publish(message)

                except Exception as e:
                    print(
                        "[TIKTOK] Kommentar-Fehler:",
                        repr(e)
                    )

            @client.on(GiftEvent)
            async def _on_gift(event):
                try:
                    gift = getattr(event, "gift", None)
                    if gift is None:
                        return
                    # Only emit the final event of streakable gifts to avoid
                    # turning every intermediate combo update into an alert.
                    if getattr(event, "streaking", False):
                        return
                    user = getattr(event, "user", None)
                    username = getattr(user, "nickname", None) or getattr(user, "unique_id", None) or "Unbekannt"
                    count = getattr(event, "repeat_count", 1) or 1
                    gift_name = getattr(gift, "name", None) or "Geschenk"
                    message = f"{username} hat {count}x {gift_name} gesendet!"
                    msg = {
                        "event": "system", "platform": "tiktok", "eventType": "gift",
                        "id": str(getattr(event, "msg_id", None) or f"gift:{time.time_ns()}"),
                        "userId": str(getattr(user, "user_id", None) or getattr(user, "unique_id", None) or username),
                        "username": username, "message": message, "tts": message,
                        "avatar": tiktok_avatar(user), "timestamp": None,
                        "badges": [], "moderator": False, "subscriber": False,
                        "giftName": str(gift_name), "repeatCount": count,
                    }
                    print("[TIKTOK EVENT]", json.dumps(msg, ensure_ascii=False, separators=(",", ":")))
                    publish(msg)
                except Exception as e:
                    print("[TIKTOK] Gift-Fehler:", repr(e))

            @client.on(FollowEvent)
            async def _on_follow(event):
                try:
                    user = getattr(event, "user", None)
                    username = getattr(user, "nickname", None) or getattr(user, "unique_id", None) or "Unbekannt"
                    msg = {
                        "event": "system", "platform": "tiktok", "eventType": "follow",
                        "id": f"follow:{getattr(user, 'user_id', None) or getattr(user, 'unique_id', None) or username}:{time.time_ns()}",
                        "userId": str(getattr(user, "user_id", None) or getattr(user, "unique_id", None) or username),
                        "username": username, "message": f"{username} folgt dir!", "tts": f"{username} folgt dir.",
                        "avatar": tiktok_avatar(user), "timestamp": None,
                        "badges": [], "moderator": False, "subscriber": False,
                    }
                    print("[TIKTOK EVENT]", json.dumps(msg, ensure_ascii=False, separators=(",", ":")))
                    publish(msg)
                    try:
                        current = get_social_status()["platforms"]["tiktok"]
                        update_social_status(
                            "tiktok",
                            viewers=getattr(client, "viewer_count", current.get("viewers", 0)) or current.get("viewers", 0),
                            followers=int(current.get("followers", 0) or 0) + 1,
                            available=True,
                        )
                    except Exception:
                        pass
                except Exception as e:
                    print("[TIKTOK] Follow-Fehler:", repr(e))

            @client.on(ShareEvent)
            async def _on_share(event):
                try:
                    user = getattr(event, "user", None)
                    username = getattr(user, "nickname", None) or getattr(user, "unique_id", None) or "Unbekannt"
                    msg = {
                        "event": "system", "platform": "tiktok", "eventType": "share",
                        "id": f"share:{getattr(user, 'user_id', None) or getattr(user, 'unique_id', None) or username}:{time.time_ns()}",
                        "userId": str(getattr(user, "user_id", None) or getattr(user, "unique_id", None) or username),
                        "username": username, "message": f"{username} hat den Stream geteilt!", "tts": f"{username} hat den Stream geteilt.",
                        "avatar": tiktok_avatar(user), "timestamp": None,
                        "badges": [], "moderator": False, "subscriber": False,
                    }
                    print("[TIKTOK EVENT]", json.dumps(msg, ensure_ascii=False, separators=(",", ":")))
                    publish(msg)
                except Exception as e:
                    print("[TIKTOK] Share-Fehler:", repr(e))

            @client.on(LikeEvent)
            async def _on_like(event):
                try:
                    user = getattr(event, "user", None)
                    username = getattr(user, "nickname", None) or getattr(user, "unique_id", None) or "Unbekannt"
                    count = getattr(event, "count", None) or getattr(event, "total", None) or 1
                    msg = {
                        "event": "system", "platform": "tiktok", "eventType": "like",
                        "id": f"like:{getattr(user, 'user_id', None) or getattr(user, 'unique_id', None) or username}:{time.time_ns()}",
                        "userId": str(getattr(user, "user_id", None) or getattr(user, "unique_id", None) or username),
                        "username": username, "message": f"{username} hat {count} Likes gesendet!", "tts": f"{username} hat {count} Likes gesendet.",
                        "avatar": tiktok_avatar(user), "timestamp": None,
                        "badges": [], "moderator": False, "subscriber": False,
                        "count": count,
                    }
                    print("[TIKTOK EVENT]", json.dumps(msg, ensure_ascii=False, separators=(",", ":")))
                    publish(msg)
                except Exception as e:
                    print("[TIKTOK] Like-Fehler:", repr(e))

            @client.on(SubNotifyEvent)
            async def _on_subscribe(event):
                try:
                    user = getattr(event, "user", None)
                    username = getattr(user, "nickname", None) or getattr(user, "unique_id", None) or "Unbekannt"
                    msg = {
                        "event": "system", "platform": "tiktok", "eventType": "subscribe",
                        "id": f"subscribe:{getattr(user, 'user_id', None) or getattr(user, 'unique_id', None) or username}:{time.time_ns()}",
                        "userId": str(getattr(user, "user_id", None) or getattr(user, "unique_id", None) or username),
                        "username": username, "message": f"{username} hat abonniert!", "tts": f"{username} hat abonniert.",
                        "avatar": tiktok_avatar(user), "timestamp": None,
                        "badges": [], "moderator": False, "subscriber": True,
                    }
                    print("[TIKTOK EVENT]", json.dumps(msg, ensure_ascii=False, separators=(",", ":")))
                    publish(msg)
                except Exception as e:
                    print("[TIKTOK] Subscribe-Fehler:", repr(e))

            @client.on(WebsocketResponseEvent)
            async def _on_websocket_response(event):
                tiktok_activity["last_received"] = time.monotonic()
                try:
                    viewer_count = int(getattr(client, "viewer_count", 0) or 0)
                    update_social_status("tiktok", viewers=viewer_count, available=True)
                except Exception:
                    pass
                # Keep this deliberately diagnostic: unknown platform events
                # must never silently disappear. Known events are handled by
                # their dedicated listeners above.
                try:
                    event_name = type(event).__name__
                    if event_name not in {"CommentEvent", "GiftEvent", "LikeEvent", "FollowEvent", "ShareEvent", "SubNotifyEvent"}:
                        print("[TIKTOK RAW EVENT]", event_name, repr(event))
                except Exception:
                    pass

            @client.on(DisconnectEvent)
            async def _on_disconnect(event):
                update_social_status("tiktok", viewers=0, available=False)
                print(
                    "[TIKTOK] Verbindung getrennt."
                )

            try:
                client.run()
            finally:
                _tiktok_cancel_ws_watchdog(client, tiktok_activity)

        except Exception as e:
            print(
                "[TIKTOK] Verbindung/Fehler:",
                repr(e)
            )

        print(
            "[TIKTOK] Neuer Verbindungsversuch in 10 Sekunden ..."
        )
        time.sleep(10)


# ============================================================
# YOUTUBE PUBLIC LIVE CHAT
# ============================================================
# YouTube is handled without Google OAuth/API keys.  The public YouTube
# watch/live-chat pages expose the same continuation data used by the web
# client.  We discover active videos per configured channel and maintain one
# independent continuation/poller per active livestream.

YOUTUBE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36"
)
YOUTUBE_DISCOVERY_INTERVAL = 30.0
YOUTUBE_ERROR_RETRY = 5.0


def youtube_http_get(url, timeout=20):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": YOUTUBE_UA,
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def youtube_http_post(url, payload, context, timeout=30):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "User-Agent": YOUTUBE_UA,
            "Content-Type": "application/json",
            "Origin": "https://www.youtube.com",
            "Referer": "https://www.youtube.com/",
            "X-Youtube-Client-Name": "1",
            "X-Youtube-Client-Version": (
                context.get("client", {}).get("clientVersion", "")
            ),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8", "replace"))


def youtube_extract_live_video_ids(html):
    """Extract currently active YouTube live video IDs from page data."""
    found = []
    decoder = json.JSONDecoder()

    # 1) videoRenderer entries.
    for hit in re.finditer(r'"videoRenderer"\s*:\s*', html):
        pos = hit.end()
        while pos < len(html) and html[pos].isspace():
            pos += 1
        if pos >= len(html) or html[pos] != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(html[pos:])
        except Exception:
            continue

        vid = obj.get("videoId")
        if not isinstance(vid, str) or len(vid) != 11:
            continue

        blob = json.dumps(obj, ensure_ascii=False)
        live = (
            obj.get("isLiveNow") is True
            or '"style":"LIVE"' in blob
            or '"style": "LIVE"' in blob
            or '"label":"LIVE"' in blob
            or '"label": "LIVE"' in blob
            or "LIVE NOW" in blob.upper()
            or any(
                isinstance(x, dict)
                and "LIVE" in str(x.get("label", "")).upper()
                for x in (obj.get("badges") or [])
            )
        )
        if live:
            found.append(vid)

    # 2) Current-video endpoint / canonical / meta video ID.
    if not found:
        for pattern in (
            r'"currentVideoEndpoint"\s*:\s*\{.{0,1500}?"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"',
            r'<link[^>]+rel="canonical"[^>]+href="https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})"',
            r'<meta[^>]+itemprop="videoId"[^>]+content="([A-Za-z0-9_-]{11})"',
            r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})".{0,1000}"isLiveNow"\s*:\s*true',
        ):
            for hit in re.finditer(pattern, html, re.I | re.S):
                found.append(hit.group(1))
                break
            if found:
                break

    return list(dict.fromkeys(found))




def youtube_discover_live_videos(channel):
    """
    Discover the active livestream.

    The important difference from the previous implementation is that
    /@handle/live can redirect directly to the current watch page. We retain
    the final URL and extract its video ID before looking at /streams.
    """
    base = youtube_channel_base(channel)
    if not base:
        return []

    headers = {
        "User-Agent": YOUTUBE_UA,
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    }
    found = []

    # /live is the authoritative public live entry point. urllib follows
    # redirects, so geturl() can give us the actual current watch URL.
    try:
        req = urllib.request.Request(base + "/live", headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", "replace")
            final_url = r.geturl()

        match = re.search(
            r"(?:[?&]v=|/watch/)([A-Za-z0-9_-]{11})",
            final_url,
            re.I,
        )
        if match:
            found.append(match.group(1))
            print(
                f"[YOUTUBE DEBUG] /live -> Watchpage: "
                f"{match.group(1)} | {final_url}"
            )

        found.extend(youtube_extract_live_video_ids(html))

    except Exception as ex:
        print("[YOUTUBE DEBUG] /live Fehler:", repr(ex))

    # /streams remains a useful fallback for channels where /live does not
    # redirect and the active stream is exposed in the stream list.
    try:
        req = urllib.request.Request(base + "/streams", headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", "replace")
        found.extend(youtube_extract_live_video_ids(html))
    except Exception as ex:
        print("[YOUTUBE DEBUG] /streams Fehler:", repr(ex))

    return list(dict.fromkeys(found))
def youtube_channel_base(channel):
    channel = str(channel or "").strip()
    if not channel:
        return ""
    if channel.startswith(("http://", "https://")):
        return channel.rstrip("/")
    if channel.startswith("@"):
        return "https://www.youtube.com/" + channel
    if channel.startswith(("UC", "HC")) and len(channel) >= 20:
        return "https://www.youtube.com/channel/" + channel
    return "https://www.youtube.com/@" + channel




def youtube_extract_player_response(html):
    """Extract ytInitialPlayerResponse from the watch page."""
    markers = (
        'ytInitialPlayerResponse =',
        'var ytInitialPlayerResponse =',
        'window["ytInitialPlayerResponse"]',
    )
    for marker in markers:
        start = 0
        while True:
            pos = html.find(marker, start)
            if pos < 0:
                break
            brace = html.find("{", pos)
            if brace < 0:
                break
            try:
                decoder = json.JSONDecoder()
                data, _ = decoder.raw_decode(html[brace:])
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
            start = pos + len(marker)
    raise RuntimeError("ytInitialPlayerResponse nicht gefunden")


def youtube_extract_initial_data(html):
    """
    Extract ytInitialData robustly.

    YouTube changes the exact script wrapper frequently. Instead of relying
    on a single </script>-based regex, locate the assignment and let the
    JSON decoder determine where the object ends.
    """
    markers = (
        'window["ytInitialData"]',
        "var ytInitialData",
        "ytInitialData",
    )

    for marker in markers:
        start = 0
        while True:
            pos = html.find(marker, start)
            if pos < 0:
                break

            brace = html.find("{", pos)
            if brace < 0:
                break

            try:
                decoder = json.JSONDecoder()
                data, _ = decoder.raw_decode(html[brace:])
                if isinstance(data, dict):
                    return data
            except Exception:
                pass

            start = pos + len(marker)

    raise RuntimeError("ytInitialData nicht gefunden")


def youtube_find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            result = youtube_find_key(value, key)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for value in obj:
            result = youtube_find_key(value, key)
            if result is not None:
                return result
    return None


def youtube_find_continuations(obj):
    found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in (
                "timedContinuationData",
                "invalidationContinuationData",
                "reloadContinuationData",
            ) and isinstance(value, dict) and value.get("continuation"):
                found.append(value)
            found.extend(youtube_find_continuations(value))
    elif isinstance(obj, list):
        for value in obj:
            found.extend(youtube_find_continuations(value))
    return found


def youtube_bootstrap(video_id):
    watch = youtube_http_get("https://www.youtube.com/watch?v=" + video_id)
    data = youtube_extract_initial_data(watch)
    renderer = youtube_find_key(data, "liveChatRenderer")
    if not renderer:
        raise RuntimeError("liveChatRenderer nicht gefunden")

    candidates = youtube_find_continuations(renderer)
    if not candidates:
        raise RuntimeError("Keine Chat-Continuation gefunden")

    initial = next(
        (x for x in candidates if x.get("timeoutMs")),
        candidates[0],
    )
    continuation = initial["continuation"]

    chat_html = youtube_http_get(
        "https://www.youtube.com/live_chat?continuation="
        + quote(continuation, safe="")
    )
    match = re.search(r'ytcfg\.set\s*\(\s*({.+?})\s*\)\s*;', chat_html, re.S)
    if not match:
        raise RuntimeError("ytcfg.set nicht gefunden")

    cfg = json.loads(match.group(1))
    key = cfg.get("INNERTUBE_API_KEY")
    context = cfg.get("INNERTUBE_CONTEXT")
    if not key or not context:
        raise RuntimeError("INNERTUBE-Konfiguration fehlt")

    return continuation, key, context


def youtube_next_continuation(chat):
    values = []
    for item in chat.get("continuations", []) or []:
        if not isinstance(item, dict):
            continue
        for key in (
            "timedContinuationData",
            "invalidationContinuationData",
            "reloadContinuationData",
        ):
            data = item.get(key)
            if isinstance(data, dict) and data.get("continuation"):
                values.append(data)
    if not values:
        return None
    return next((x for x in values if x.get("timeoutMs")), values[0])


def youtube_text(value):
    if not isinstance(value, dict):
        return ""
    if isinstance(value.get("simpleText"), str):
        return value["simpleText"]

    out = []
    for run in value.get("runs", []) or []:
        if run.get("text") is not None:
            out.append(str(run["text"]))
        elif run.get("emoji"):
            emoji = run["emoji"]
            shortcuts = emoji.get("shortcuts") or []
            out.append(
                shortcuts[0]
                if shortcuts
                else ":" + str(emoji.get("emojiId", "emoji")) + ":"
            )
    return "".join(out)


def youtube_text_and_emotes(value):
    """Extract YouTube text plus native emoji image URLs."""
    if not isinstance(value, dict):
        return "", []

    if isinstance(value.get("simpleText"), str):
        return value["simpleText"], []

    out = []
    emotes = []

    for run in value.get("runs", []) or []:
        if run.get("text") is not None:
            out.append(str(run["text"]))
            continue

        emoji = run.get("emoji")
        if not isinstance(emoji, dict):
            continue

        emoji_id = str(emoji.get("emojiId") or "emoji").strip()
        token = ":" + emoji_id + ":"
        out.append(token)

        image = emoji.get("image") or {}
        thumbnails = image.get("thumbnails") if isinstance(image, dict) else []
        if not isinstance(thumbnails, list):
            thumbnails = []

        url = ""
        for thumb in reversed(thumbnails):
            if not isinstance(thumb, dict):
                continue
            candidate = thumb.get("url")
            if isinstance(candidate, str) and candidate.startswith(
                ("http://", "https://")
            ):
                url = candidate
                break

        if url:
            emotes.append({
                "token": token,
                "name": emoji_id,
                "url": url,
            })

    return "".join(out), emotes


def youtube_thumbnail(renderer):
    thumbs = renderer.get("authorPhoto", {}).get("thumbnails", []) or []
    for thumb in reversed(thumbs):
        url = thumb.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return url
    return ""


def youtube_badges(renderer):
    result = []
    for badge in renderer.get("authorBadges", []) or []:
        tooltip = str(
            badge.get("liveChatAuthorBadgeRenderer", {}).get("tooltip", "")
        ).strip()
        if tooltip:
            result.append(tooltip.upper())
    return result


def youtube_parse_action(action):
    item = (action.get("addChatItemAction") or {}).get("item") or {}
    renderer = (
        item.get("liveChatTextMessageRenderer")
        or item.get("liveChatPaidMessageRenderer")
        or item.get("liveChatMembershipItemRenderer")
    )
    if not renderer:
        return None

    text_value = renderer.get("message") or renderer.get("bodyText") or {}
    message, youtube_emotes = youtube_text_and_emotes(text_value)

    if not message:
        # Paid messages can use header text as their visible content.
        message, youtube_emotes = youtube_text_and_emotes(
            renderer.get("headerSubtext") or {}
        )

    badges = youtube_badges(renderer)
    author = youtube_text(renderer.get("authorName") or {}) or "Unbekannt"
    user_id = str(renderer.get("authorExternalChannelId") or "")
    msg_id = str(renderer.get("id") or "")

    return {
        "id": msg_id,
        "userId": user_id,
        "username": author,
        "message": message,
        "avatar": youtube_thumbnail(renderer),
        "timestamp": renderer.get("timestampUsec"),
        "badges": badges,
        "moderator": any("MODERATOR" in x for x in badges),
        "subscriber": any(
            token in x for x in badges for token in ("MEMBER", "SPONSOR")
        ),
        "youtubeEmotes": youtube_emotes,
    }


def youtube_accounts_from_config(config):
    accounts = config.get("youtube_channels")
    if isinstance(accounts, list):
        result = []
        for item in accounts:
            if isinstance(item, dict):
                channel = str(item.get("channel") or "").strip()
                display = str(item.get("display_name") or channel).strip()
                account_id = str(item.get("id") or channel).strip()
            else:
                channel = str(item or "").strip()
                display = channel
                account_id = channel
            if channel:
                result.append({
                    "id": account_id,
                    "channel": channel,
                    "display_name": display or channel,
                })
        return result

    channel = str(config.get("youtube_channel", "")).strip()
    if channel:
        return [{"id": channel, "channel": channel, "display_name": channel}]

    # Direct bridge execution fallback: read accounts.json if present.
    accounts_file = BASE_DIR / "accounts.json"
    try:
        with open(accounts_file, encoding="utf-8") as f:
            data = json.load(f)
        result = []
        for account in data.get("accounts", []):
            if account.get("platform") != "youtube":
                continue
            channel = str(account.get("channel") or "").strip()
            if channel:
                result.append({
                    "id": str(account.get("id") or channel),
                    "channel": channel,
                    "display_name": str(
                        account.get("display_name") or channel
                    ),
                })
        return result
    except Exception:
        return []


def youtube_loop(config):
    accounts = youtube_accounts_from_config(config)
    if not accounts:
        print("[YOUTUBE] Deaktiviert: kein YouTube-Kanal konfiguriert.")
        return

    print(
        "[YOUTUBE] aktiv - überwache "
        + str(len(accounts))
        + " Kanal(e): "
        + ", ".join(a["display_name"] for a in accounts)
    )

    streams = {}
    next_discovery = 0.0

    while True:
        now = time.time()

        if now >= next_discovery:
            for account in accounts:
                try:
                    video_ids = youtube_discover_live_videos(account["channel"])
                    wanted = set(video_ids)
                    existing = {
                        key[1]
                        for key in streams
                        if key[0] == account["id"]
                    }

                    for video_id in video_ids:
                        stream_key = (account["id"], video_id)
                        if stream_key in streams:
                            continue
                        try:
                            continuation, key, context = youtube_bootstrap(video_id)
                            streams[stream_key] = {
                                "account": account,
                                "video_id": video_id,
                                "continuation": continuation,
                                "api_key": key,
                                "context": context,
                                "seen": set(),
                                "first": True,
                                "next_poll": now,
                                "last_error": 0.0,
                            }
                            print(
                                f"[YOUTUBE] Chat verbunden | "
                                f"{account['display_name']} | {video_id}"
                            )
                        except Exception as ex:
                            print(
                                f"[YOUTUBE] Chat-Start fehlgeschlagen | "
                                f"{account['display_name']} | {video_id}: {ex}"
                            )

                    # Streams that disappeared from the channel's active list
                    # are kept until their current continuation ends. This
                    # prevents a temporary discovery failure from dropping chat.
                    _ = wanted, existing
                except Exception as ex:
                    print(
                        f"[YOUTUBE] Kanalprüfung fehlgeschlagen | "
                        f"{account['display_name']}: {ex}"
                    )
            next_discovery = now + YOUTUBE_DISCOVERY_INTERVAL

        soonest = now + 5.0
        for stream_key, state in list(streams.items()):
            if now < state["next_poll"]:
                soonest = min(soonest, state["next_poll"])
                continue

            try:
                url = (
                    "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat"
                    "?key=" + quote(state["api_key"], safe="")
                )
                _, data = youtube_http_post(
                    url,
                    {
                        "context": state["context"],
                        "continuation": state["continuation"],
                    },
                    state["context"],
                )

                chat = (
                    data.get("continuationContents", {})
                    .get("liveChatContinuation", {})
                )
                actions = chat.get("actions") or []

                for action in actions:
                    parsed = youtube_parse_action(action)
                    if not parsed or not parsed.get("id"):
                        continue
                    if parsed["id"] in state["seen"]:
                        continue
                    state["seen"].add(parsed["id"])
                    if len(state["seen"]) > 10000:
                        state["seen"].clear()

                    if state["first"]:
                        continue

                    message = {
                        "platform": "youtube",
                        "id": parsed["id"],
                        "userId": parsed["userId"],
                        "username": parsed["username"],
                        "message": parsed["message"],
                        "avatar": parsed["avatar"],
                        "timestamp": parsed["timestamp"],
                        "badges": parsed["badges"],
                        "youtubeEmotes": parsed["youtubeEmotes"],
                        "moderator": parsed["moderator"],
                        "subscriber": parsed["subscriber"],
                        "youtubeChannel": state["account"]["display_name"],
                        "youtubeChannelId": state["account"]["id"],
                        "youtubeVideoId": state["video_id"],
                    }
                    print(
                        f"[YOUTUBE] {state['account']['display_name']} | "
                        f"{message['username']}: {message['message']}"
                    )
                    publish(message)

                if state["first"]:
                    state["first"] = False
                    print(
                        f"[YOUTUBE] Initialer Chatbestand übersprungen | "
                        f"{state['account']['display_name']} | "
                        f"{state['video_id']} | {len(actions)} Actions"
                    )

                nxt = youtube_next_continuation(chat)
                if not nxt:
                    print(
                        f"[YOUTUBE] Chat beendet | "
                        f"{state['account']['display_name']} | {state['video_id']}"
                    )
                    streams.pop(stream_key, None)
                    continue

                state["continuation"] = nxt["continuation"]
                timeout_ms = int(nxt.get("timeoutMs") or 10000)
                state["next_poll"] = time.time() + max(1.0, timeout_ms / 1000.0)
                soonest = min(soonest, state["next_poll"])

            except Exception as ex:
                if now - state.get("last_error", 0.0) >= 5.0:
                    print(
                        f"[YOUTUBE] Poll-Fehler | "
                        f"{state['account']['display_name']} | "
                        f"{state['video_id']}: {ex}"
                    )
                    state["last_error"] = now
                state["next_poll"] = now + YOUTUBE_ERROR_RETRY
                soonest = min(soonest, state["next_poll"])

        time.sleep(max(0.2, min(1.0, soonest - time.time())))


CNG_STATUS_HTML = r'''<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CNG Multi-Platform Status</title>
<style>
:root{--bg:#090512;--head:#10091b;--line:rgba(177,91,255,.32);--text:#f7f1ff;--muted:#b7a7c8;--accent:#ff8a00;--status-font:11px;--purple:#a641ff;--pink:#ff35a6}
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}
body{font-family:"Segoe UI",Arial,sans-serif;color:var(--text);padding:6px}
.card{width:100%;height:100%;border:1px solid var(--line);border-radius:10px;background:linear-gradient(145deg,rgba(25,11,41,.98),rgba(9,5,18,.98));padding:8px;box-shadow:0 0 22px rgba(166,65,255,.15)}
.header{display:flex;align-items:center;gap:6px;height:25px;border-bottom:1px solid var(--line);padding-bottom:6px;margin-bottom:6px}
.logo{font-size:calc(var(--status-font)*1.05);font-weight:800}.logo span{background:linear-gradient(90deg,#ff8a00,#ff4e9b,#a641ff);-webkit-background-clip:text;background-clip:text;color:transparent}
.status{margin-left:auto;font-size:8px;color:var(--muted);display:flex;align-items:center;gap:5px}.dot{width:6px;height:6px;border-radius:50%;background:#39d98a;box-shadow:0 0 8px rgba(57,217,138,.65)}.dot.off{background:#ff5d67;box-shadow:0 0 8px rgba(255,93,103,.55)}
.btn{border:1px solid rgba(177,91,255,.34);background:rgba(25,12,40,.94);color:#eee3fa;border-radius:999px;padding:2px 7px;font-size:8px;cursor:pointer;transition:background .15s ease,border-color .15s ease,box-shadow .15s ease}.btn:hover{background:#28113c;border-color:rgba(206,119,255,.75);box-shadow:0 0 10px rgba(166,65,255,.18);color:#fff}.font-size{width:28px;text-align:center;font-size:8px;color:#c7b1d8}
.total{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:7px}.totalBox{background:linear-gradient(130deg,rgba(39,16,59,.96),rgba(17,8,28,.96));border:1px solid rgba(177,91,255,.28);border-radius:7px;padding:5px 8px;display:flex;justify-content:space-between;align-items:baseline}.totalBox:first-child{border-left:3px solid var(--accent)}.totalBox:last-child{border-left:3px solid var(--pink)}.totalLabel{font-size:8px;color:var(--muted);text-transform:uppercase;letter-spacing:.7px}.totalValue{font-size:calc(var(--status-font)*1.45);font-weight:800;color:#fff}
table{width:100%;border-collapse:separate;border-spacing:0;background:rgba(15,7,24,.96);border:1px solid rgba(177,91,255,.3);border-radius:7px;overflow:hidden;font-size:var(--status-font)}
th{font-size:8px;color:#c4a8dd;text-transform:uppercase;letter-spacing:.45px;font-weight:700;padding:4px 3px;border-bottom:1px solid rgba(177,91,255,.28)}td{padding:4px 3px;text-align:center;border-bottom:1px solid rgba(177,91,255,.14);white-space:nowrap}tr:last-child td{border-bottom:0}.rowlabel{text-align:left;color:#e4d5f2;font-size:calc(var(--status-font)*.82);font-weight:700;padding-left:7px}.platform{font-weight:700}.num{font-variant-numeric:tabular-nums;font-weight:650}.offline{opacity:.48}.dotp{display:inline-block;width:5px;height:5px;border-radius:50%;margin-right:3px;background:#39d98a}.offline .dotp{background:#626973}.footer{margin-top:5px;color:#a18eaf;font-size:7px;text-align:right}
@media(max-width:560px){.card{padding:6px}.totalBox{padding:4px 6px}table{font-size:min(var(--status-font),10px)}th,td{padding-left:2px;padding-right:2px}}
</style></head><body>
<div class="card">
 <div class="header"><div class="logo">CNG <span>Multi-Platform</span></div><div class="status"><i id="dot" class="dot"></i><span id="conn">Bridge verbunden</span></div><button id="fontDown" class="btn">A−</button><span id="fontSize" class="font-size">11px</span><button id="fontUp" class="btn">A+</button></div>
 <div class="total"><div class="totalBox"><span class="totalLabel">Viewer gesamt</span><span class="totalValue" id="totalViewers">0</span></div><div class="totalBox"><span class="totalLabel">Follower gesamt</span><span class="totalValue" id="totalFollowers">0</span></div></div>
 <table><thead><tr><th></th><th>CNG</th><th>Twitch</th><th>Kick</th><th>YouTube</th><th>TikTok</th></tr></thead><tbody><tr><td class="rowlabel">Viewer</td><td id="cng-v">0</td><td id="twitch-v">0</td><td id="kick-v">0</td><td id="youtube-v">0</td><td id="tiktok-v">0</td></tr><tr><td class="rowlabel">Follower</td><td id="cng-f">0</td><td id="twitch-f">0</td><td id="kick-f">0</td><td id="youtube-f">0</td><td id="tiktok-f">0</td></tr></tbody></table>
 <div class="footer" id="updated">Warte auf Daten …</div>
</div>
<script>
const ids=["cng","twitch","kick","youtube","tiktok"];
const fontKey="cng_multichat_font";
let font=Number(localStorage.getItem(fontKey)||11);font=Math.max(8,Math.min(24,font));
function applyFont(){document.documentElement.style.setProperty("--status-font",font+"px");document.getElementById("fontSize").textContent=font+"px";localStorage.setItem(fontKey,String(font));}
applyFont();
document.getElementById("fontDown").onclick=()=>{font=Math.max(8,font-1);applyFont()};document.getElementById("fontUp").onclick=()=>{font=Math.min(24,font+1);applyFont()};
function fmt(n){return Number(n||0).toLocaleString("de-DE")}
function render(d){if(!d)return;document.getElementById("totalViewers").textContent=fmt(d.total?.viewers);document.getElementById("totalFollowers").textContent=fmt(d.total?.followers);ids.forEach(p=>{const x=d.platforms?.[p]||{};document.getElementById(p+"-v").textContent=fmt(x.viewers);document.getElementById(p+"-f").textContent=fmt(x.followers);});document.getElementById("updated").textContent=d.updatedAt?"Aktualisiert "+new Date(d.updatedAt*1000).toLocaleTimeString("de-DE"):"";}
function connect(){const es=new EventSource("/multi-platform-status-events");es.onopen=()=>{document.getElementById("dot").classList.remove("off");document.getElementById("conn").textContent="Live"};es.onmessage=e=>{try{render(JSON.parse(e.data))}catch(_){}};es.onerror=()=>{document.getElementById("dot").classList.add("off");document.getElementById("conn").textContent="Bridge getrennt";es.close();setTimeout(connect,3000)}}connect();
</script></body></html>'''
# ============================================================
# LOCAL SSE SERVER
# ============================================================

class Handler(BaseHTTPRequestHandler):

    protocol_version = "HTTP/1.1"

    def handle(self):
        try:
            super().handle()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            # OBS may close/reload the Browser Source SSE connection.
            # WinError 10053 is a normal client-side disconnect here.
            pass

    def log_message(self, *args):
        pass

    def serve_html_file(self, filename):
        """Serve an existing self-contained HTML overlay without changing its contents."""
        path = BASE_DIR / filename
        try:
            body = path.read_bytes()
        except OSError:
            body = f"<h1>{filename} nicht gefunden</h1>".encode("utf-8")
            status = 404
        else:
            status = 200

        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def cors(self):
        self.send_header(
            "Access-Control-Allow-Origin", "*"
        )
        self.send_header(
            "Cache-Control", "no-cache, no-store"
        )
        self.send_header(
            "X-Accel-Buffering", "no"
        )

    def do_GET(self):

        # Existing HTML overlays are served byte-for-byte as they are on disk.
        # Their CSS, JavaScript, layout and functionality are intentionally untouched.
        if self.path in ("/chat", "/chat.html"):
            self.serve_html_file("cng-multichat-overlay.html")
            return

        if self.path in ("/chat-horizontal", "/chat-horizontal.html", "/horizontal", "/horizontal.html"):
            self.serve_html_file("cng-multichat-overlay_horizontal.html")
            return

        if self.path in ("/livechat", "/livechat.html"):
            self.serve_html_file("cng-multichat-livechat.html")
            return

        if self.path in ("/alerts", "/alerts.html"):
            self.serve_html_file("alerts.html")
            return

        # Serve configured alert media over HTTP so OBS/Chromium does not
        # have to access local files through file:// URLs.
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/alert-media":
            params = urllib.parse.parse_qs(parsed.query)
            alert_type = str((params.get("type") or [""])[0]).strip().lower()
            kind = str((params.get("kind") or ["sound"])[0]).strip().lower()
            if alert_type not in ALERT_OVERLAY_TYPES or kind not in {"gif", "sound"}:
                self.send_error(404)
                return
            try:
                runtime = load_config()
                settings = cng_alert_settings(runtime, alert_type)
                media_path = str(settings.get(kind) or "").strip()
                if not media_path:
                    raise FileNotFoundError(f"Kein {kind} konfiguriert")

                if media_path.lower().startswith("file:"):
                    file_url = urllib.parse.urlparse(media_path)
                    media_path = urllib.request.url2pathname(file_url.path)
                    if file_url.netloc:
                        media_path = "\\\\" + file_url.netloc + media_path.replace("/", "\\")
                    elif os.name == "nt" and re.match(r"^/[A-Za-z]:[/\\]", media_path):
                        media_path = media_path[1:]

                media_path = os.path.expandvars(os.path.expanduser(media_path))
                if not os.path.isabs(media_path):
                    media_path = os.path.join(BASE_DIR, media_path)
                media_path = os.path.abspath(media_path)
                if not os.path.isfile(media_path):
                    raise FileNotFoundError(media_path)
                with open(media_path, "rb") as f:
                    body = f.read()
                content_type = mimetypes.guess_type(media_path)[0] or "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-cache, no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                print(f"[ALERT MEDIA] {kind.title()} ausgeliefert: {alert_type} | {media_path}")
            except Exception as e:
                print(f"[ALERT MEDIA] {kind.title()} konnte nicht geladen werden: {alert_type} | {e!r}")
                self.send_error(404, f"Alert-{kind} nicht gefunden")
            return

        if self.path == "/config-events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                initial = load_display_config()
                self.wfile.write((
                    "data: " +
                    json.dumps({"type": "display_config", **initial},
                               ensure_ascii=False, separators=(",", ":")) +
                    "\n\n"
                ).encode("utf-8"))
                self.wfile.flush()
                with config_clients_lock:
                    config_clients.append(self.wfile)
                print("[DISPLAY CONFIG] Live-Client verbunden.")
                while True:
                    time.sleep(30)
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with config_clients_lock:
                    if self.wfile in config_clients:
                        config_clients.remove(self.wfile)
                print("[DISPLAY CONFIG] Live-Client getrennt.")
            return

        if self.path == "/multi-platform-status-events":
            self.send_response(200)
            self.send_header("Content-Type","text/event-stream; charset=utf-8")
            self.send_header("Cache-Control","no-cache, no-store, must-revalidate")
            self.send_header("Connection","keep-alive")
            self.send_header("Access-Control-Allow-Origin","*")
            self.end_headers()
            try:
                current=get_social_status()
                self.wfile.write(("data: "+json.dumps(current,ensure_ascii=False,separators=(",",":"))+"\n\n").encode("utf-8"))
                self.wfile.flush()
                last=None
                while True:
                    current=get_social_status()
                    sig=json.dumps(current,ensure_ascii=False,sort_keys=True,separators=(",",":"))
                    if sig!=last:
                        self.wfile.write(("data: "+sig+"\n\n").encode("utf-8"))
                        self.wfile.flush(); last=sig
                    time.sleep(1.0)
            except (BrokenPipeError,ConnectionResetError,OSError):
                pass
            return

        if self.path == "/config":
            config = load_display_config()
            body = json.dumps(
                config,
                ensure_ascii=False,
                separators=(",", ":")
            ).encode("utf-8")

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "application/json; charset=utf-8"
            )
            self.cors()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if urllib.parse.urlparse(self.path).path in (
            "/cng-status", "/cng-status.html", "/stats", "/stats.html"
        ):
            body = CNG_STATUS_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/html; charset=utf-8"
            )
            self.cors()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return


        if self.path == "/health":
            body = b'{"ok":true,"service":"unified-chat-bridge-v3"}'

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "application/json; charset=utf-8"
            )
            self.cors()
            self.send_header(
                "Content-Length",
                str(len(body))
            )
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/stream-status":
            body = json.dumps(
                get_cng_status(),
                ensure_ascii=False,
                separators=(",", ":")
            ).encode("utf-8")

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "application/json; charset=utf-8"
            )
            self.cors()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return


        if self.path in ("/stream-status-events", "/status-events"):
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/event-stream; charset=utf-8"
            )
            self.send_header(
                "Connection",
                "keep-alive"
            )
            self.cors()
            self.end_headers()

            with status_clients_lock:
                status_clients.append(self.wfile)

            try:
                # Immediately send the current state to a newly connected OBS dock.
                packet = (
                    "data: " +
                    json.dumps(
                        get_cng_status(),
                        ensure_ascii=False,
                        separators=(",", ":")
                    ) +
                    "\n\n"
                ).encode("utf-8")
                self.wfile.write(packet)
                self.wfile.flush()

                while True:
                    time.sleep(15)
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()

            except Exception:
                pass

            finally:
                with status_clients_lock:
                    if self.wfile in status_clients:
                        status_clients.remove(self.wfile)

            return


        if self.path in ("/alert-events", "/alerts/events", "/cng-alert-events"):

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/event-stream; charset=utf-8"
            )
            self.send_header(
                "Connection",
                "keep-alive"
            )
            self.cors()
            self.end_headers()

            with alert_clients_lock:
                alert_clients.append(self.wfile)

            try:
                self.wfile.write(
                    b": cng alert stream connected\n\n"
                )
                self.wfile.flush()

                while True:
                    time.sleep(15)
                    self.wfile.write(
                        b": keepalive\n\n"
                    )
                    self.wfile.flush()

            except Exception:
                pass

            finally:
                with alert_clients_lock:
                    if self.wfile in alert_clients:
                        alert_clients.remove(self.wfile)

            return

        if self.path.startswith("/events"):

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/event-stream; charset=utf-8"
            )
            self.send_header(
                "Connection",
                "keep-alive"
            )
            self.cors()
            self.end_headers()

            with clients_lock:
                clients.append(self.wfile)

            try:
                self.wfile.write(
                    b": unified chat connected\n\n"
                )
                self.wfile.flush()

                while True:
                    time.sleep(15)
                    self.wfile.write(
                        b": keepalive\n\n"
                    )
                    self.wfile.flush()

            except Exception:
                pass

            finally:
                with clients_lock:
                    if self.wfile in clients:
                        clients.remove(self.wfile)

            return

        body = b"Unified Chat Bridge v7 - Twitch auth diagnostics"

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )
        self.cors()
        self.send_header(
            "Content-Length",
            str(len(body))
        )
        self.end_headers()
        self.wfile.write(body)


def main():
    _pi_startup_trace("main erreicht; lade Bridge-Konfiguration")
    config = apply_accounts_to_config(load_config())
    _pi_startup_trace("erste Konfigurationsrunde beendet")

    # Windows-only convenience command:
    #   python unified_chat_bridge_systemevents_v8_twitch_device_auth.py --twitch-auth
    # This obtains a fresh Twitch user token with the EventSub scopes.
    if "--twitch-auth" in __import__("sys").argv:
        try:
            twitch_device_auth(config)
        except Exception as e:
            print("[TWITCH AUTH] Fehler:", repr(e))
            raise SystemExit(1)
        return

    if "--twitch-auth-status" in __import__("sys").argv:
        try:
            twitch_print_auth_status(config)
        except Exception as e:
            print("[TWITCH AUTH] Fehler:", repr(e))
            raise SystemExit(1)
        return

    config = apply_accounts_to_config(load_config())
    _pi_startup_trace("Konfiguration geladen; initialisiere Bridge-Threads")

    print("==========================================")
    print(" CNG MultiChat Bridge")
    print(" CNG + TWITCH + KICK + TIKTOK")
    print("==========================================")
    print("Kein Chromium")
    print("Kein Firefox")
    print()
    cng_channel = str(config.get("cng_channel") or "").strip()
    if not cng_channel:
        try:
            target = find_cng_account(load_accounts_file())
            cng_channel = str((target or {}).get("channel") or (target or {}).get("display_name") or "").strip()
        except Exception:
            cng_channel = ""
    print("CNG Chat:", "AKTIV" if cng_channel else "AUS", f"({cng_channel})" if cng_channel else "")
    print(
        "Twitch:",
        "AKTIV" if config.get("twitch_oauth_token") else "AUS"
    )
    print(
        "Twitch EventSub:",
        "AKTIV" if config.get("twitch_client_id") and config.get("twitch_oauth_token") else "AUS"
    )
    try:
        _kick_account, kick_channel, _kick_chatroom_id, _kick_app_key = kick_account_settings()
    except Exception:
        kick_channel = "AUS (kein Kick-Account in accounts.json)"
    print("Kick:", kick_channel)
    print(
        "Twitch Kanal:",
        config.get("twitch_channel", "")
    )
    print()
    print("Events: http://127.0.0.1:8765/events")
    print("Status: http://127.0.0.1:8765/stream-status")
    print("Status SSE: http://127.0.0.1:8765/stream-status-events")
    print("Chat: http://127.0.0.1:8765/chat")
    print("Chat Horizontal: http://127.0.0.1:8765/chat-horizontal")
    print("Live Chat: http://127.0.0.1:8765/livechat")
    print("Alerts: http://127.0.0.1:8765/alerts")
    print("CNG Status: http://127.0.0.1:8765/cng-status")
    print("Stats: http://127.0.0.1:8765/stats")
    print("Health: http://127.0.0.1:8765/health")
    print("==========================================")

    # OBS connection settings are read by OBSWebSocketClient directly from
    # obs_settings.json. Keep the startup status in sync with that file too.
    obs_client = get_obs_ws_client(config)
    obs_settings = obs_client._settings()
    if bool(obs_settings.get("enabled", True)):
        print(
            "OBS WebSocket:",
            f"AKTIV ({str(obs_settings.get('host', '127.0.0.1'))}:{int(obs_settings.get('port', 4455))})"
        )
        threading.Thread(
            target=obs_client.run_forever,
            daemon=True
        ).start()
    else:
        print("OBS WebSocket: AUS")

    # CNG CHAT: OBS-token streamChat.getMessages polling with session fallback.
    # CNG alerts/followers/status below use the same legacy platform API.
    threading.Thread(
        target=cng_loop,
        args=(config,),
        daemon=True
    ).start()

    try:
        selected_bot = load_selected_command_bot()
    except Exception as e:
        print("[COMMAND BOT] Aktiver Bot konnte beim Start nicht aus command_bot.json gelesen werden:", repr(e))
        selected_bot = "intern"

    print(f"[COMMAND BOT] Aktiver Bot: {selected_bot}")

    if selected_bot == "lumiastream":
        threading.Thread(
            target=get_lumia_ws_client(config).connect_forever,
            daemon=True
        ).start()
    else:
        print("[LUMIA WS] nicht gestartet - LumiaStream ist nicht ausgewählt.")

    # CNG Alerts / Follow / Unfollow / Status.
    threading.Thread(
        target=cng_alert_loop,
        args=(config,),
        daemon=True
    ).start()

    # Unfollows are detected exclusively by comparing the current authoritative
    # follower list against active entries in cng_followers.json.
    threading.Thread(
        target=cng_follower_loop,
        args=(config,),
        daemon=True
    ).start()

    threading.Thread(
        target=cng_status_loop,
        args=(config,),
        daemon=True
    ).start()

    threading.Thread(
        target=twitch_loop,
        args=(config,),
        daemon=True
    ).start()

    threading.Thread(
        target=twitch_eventsub_loop,
        args=(config,),
        daemon=True
    ).start()

    threading.Thread(
        target=kick_loop,
        args=(config,),
        daemon=True
    ).start()

    threading.Thread(
        target=tiktok_loop,
        args=(config,),
        daemon=True
    ).start()

    threading.Thread(
        target=youtube_loop,
        args=(config,),
        daemon=True
    ).start()

    threading.Thread(
        target=social_status_loop,
        args=(config,),
        daemon=True
    ).start()

    server = ThreadingHTTPServer(
        (HOST, PORT),
        Handler
    )
    _pi_startup_trace(f"HTTP-Server gebunden auf {HOST}:{PORT}; Healthcheck verfuegbar")

    try:
        server.serve_forever()

    except KeyboardInterrupt:
        print("\nBeendet.")

    finally:
        server.server_close()


if __name__ == "__main__":
    main()
