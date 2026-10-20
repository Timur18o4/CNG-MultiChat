import base64
import codecs
import hashlib
import ctypes
import ctypes.wintypes
import json
import re
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, colorchooser
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from update_service import (
    download_and_stage,
    latest_update,
    read_app_version,
    start_external_updater,
)

APP_NAME = "CNG MultiChat Manager "
BASE_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
CONFIG_FILE = os.path.join(BASE_DIR, "accounts.json")
BRIDGE_SOURCE = os.path.join(BASE_DIR, "unified_chat_bridge.py")
BRIDGE_EXE = os.path.join(BASE_DIR, "UnifiedChatBridge.exe")
BRIDGE_CONFIG_NAME = "unified_chat_config.json"
BRIDGE_PORT = 8765
COMMAND_BOT_CONFIG_FILE = os.path.join(BASE_DIR, "command_bot.json")
ALERT_CONFIG_FILE = os.path.join(BASE_DIR, "alert_settings.json")
OBS_CONFIG_FILE = os.path.join(BASE_DIR, "obs_settings.json")
APP_SETTINGS_FILE = os.path.join(BASE_DIR, "manager_settings.json")
IDEAS_DISCUSSION_URL = (
    "https://github.com/Timur18o4/CNG-MultiChat/discussions/new?category=ideas"
)
try:
    APP_VERSION = read_app_version(BASE_DIR)
except Exception:
    APP_VERSION = "0.0.0"

ALERT_EVENT_DEFAULTS = {
    "follow": {
        "label": "Follow",
        "gif": "",
        "sound": "",
        "duration_seconds": 15,
    },
    "donation": {
        "label": "Coin Donation",
        "gif": "",
        "sound": "",
        "duration_seconds": 5,
    },
    "unfollow": {
        "label": "Unfollow",
        "gif": "",
        "sound": "",
        "duration_seconds": 5,
    },
    "raid": {
        "label": "Raid",
        "gif": "",
        "sound": "",
        "duration_seconds": 5,
    },
}

COMMAND_BOT_DEFAULTS = {
    "command_bot": "intern",
    "intern": {
        "account_name": "CNG-Bot",
        "cng_session": "",
        "user_id": ""
    },
    "lumiastream": {
        "api_url": "http://localhost:39231/api/send",
        "token": ""
    },
    "streamerbot": {
        "scheme": "ws",
        "host": "127.0.0.1",
        "port": 8080,
        "endpoint": "/",
        "password": "",
        "action_name": "CNG Command Router",
        "timeout": 10
    }
}

OBS_DEFAULTS = {
    "enabled": True,
    "host": "127.0.0.1",
    "port": 4455,
    "password": "",
}

DISPLAY_DEFAULTS = {
    "auto_hide_messages": False,
    "message_lifetime_seconds": 30,
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

DISPLAY_PLATFORMS = ("twitch", "kick", "cng", "tiktok", "youtube")
DISPLAY_COLOR_MODES = ("auto", "palette", "platform", "fixed")


def normalize_display_color(value, fallback):
    value = str(value or "").strip()
    return value.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else fallback


def normalize_display_color_map(value):
    if not isinstance(value, dict):
        return {}
    colors = {}
    for username, color in value.items():
        key = str(username or "").strip().lower()
        if key:
            normalized = normalize_display_color(color, "")
            if normalized:
                colors[key] = normalized
    return colors

APP_DEFAULTS = {
    "start_with_windows": False,
    "start_minimized": False,
    "minimize_to_tray": True,
}

CHAT_STYLE_DEFS = {
    "classic": {"name": "Classic", "description": "Aktuelles CNG-Design mit Avatar und Plattform-Leiste."},
    "minimal": {"name": "Minimal", "description": "Schlank, transparent und unaufdringlich."},
    "bubble": {"name": "Bubble", "description": "Runde Chatblasen mit stärkerem Abstand."},
    "compact": {"name": "Compact", "description": "Mehr Nachrichten auf kleiner Fläche, ohne Avatar."},
    "neon": {"name": "Neon", "description": "Kräftiger Gaming-Look mit Plattform-Glow."},
}

# ---------------------------------------------------------------------------
# Windows DPAPI
# ---------------------------------------------------------------------------
if os.name == "nt":
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32

    def dpapi_encrypt(text):
        raw = text.encode("utf-8")
        buf = ctypes.create_string_buffer(raw)
        inp = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        out = DATA_BLOB()
        if not crypt32.CryptProtectData(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
            raise ctypes.WinError()
        data = ctypes.string_at(out.pbData, out.cbData)
        ctypes.windll.kernel32.LocalFree(out.pbData)
        return base64.b64encode(data).decode("ascii")

    def dpapi_decrypt(value):
        raw = base64.b64decode(value)
        buf = ctypes.create_string_buffer(raw)
        inp = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        out = DATA_BLOB()
        if not crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
            raise ctypes.WinError()
        data = ctypes.string_at(out.pbData, out.cbData)
        ctypes.windll.kernel32.LocalFree(out.pbData)
        return data.decode("utf-8")
else:
    def dpapi_encrypt(text):
        return text
    def dpapi_decrypt(value):
        return value


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
def module_available(name):
    try:
        __import__(name)
        return True
    except Exception:
        return False


def _json_clone(value):
    """Copy serializable Manager state before background deployment work."""
    return json.loads(json.dumps(value, ensure_ascii=False))

def pip_install(package):
    return subprocess.check_call([
        sys.executable, "-m", "pip", "install",
        "--disable-pip-version-check", "-U", package
    ])

def ensure_runtime_dependencies(update=None):
    # Packaged builds contain their Python dependencies. Do not try to invoke
    # pip through the GUI executable at runtime.
    if getattr(sys, "frozen", False):
        return True

    missing = []
    if not module_available("websocket"):
        missing.append("websocket-client")
    if not module_available("TikTokLive"):
        missing.append("TikTokLive")
    if not module_available("PIL"):
        missing.append("Pillow")
    if not module_available("pystray"):
        missing.append("pystray")
    if not module_available("paramiko"):
        missing.append("paramiko")

    for package in missing:
        if update:
            update(f"Installiere {package} …")
        try:
            pip_install(package)
        except Exception as ex:
            raise RuntimeError(f"{package} konnte nicht installiert werden:\n{ex}")

    return True


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------
def load_accounts():
    if not os.path.exists(CONFIG_FILE):
        return []
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        accounts = data.get("accounts", [])
        for a in accounts:
            a.setdefault("auth", {})
            # accounts.json stores credentials encrypted with Windows DPAPI.
            # Keep the in-memory account data decrypted so a later save does
            # not encrypt already-encrypted values a second time.
            if isinstance(a.get("auth"), dict):
                decrypted_auth = {}
                for key, value in a["auth"].items():
                    if not value:
                        decrypted_auth[key] = value
                        continue
                    try:
                        decrypted_auth[key] = dpapi_decrypt(value)
                    except Exception:
                        # Backward compatibility: tolerate values that are
                        # already plaintext (or otherwise not DPAPI data).
                        decrypted_auth[key] = value
                a["auth"] = decrypted_auth
            if a.get("platform") == "cng":
                # CNG uses the channel slug, numeric creator_id and optional
                # session for authenticated legacy CNG API requests.
                # Keep creator_id in the account; the Bridge needs it for
                # chat, alerts, followers and status.
                try:
                    creator_id = int(a.get("creator_id"))
                    if creator_id <= 0:
                        raise ValueError
                    a["creator_id"] = creator_id
                except (TypeError, ValueError):
                    a["creator_id"] = ""
            if a.get("platform") == "tiktok":
                a["auth"].clear()
        return accounts
    except Exception:
        return []

def save_accounts(accounts):
    output = {"version": 3, "accounts": []}
    for a in accounts:
        item = dict(a)
        item["auth"] = {
            k: dpapi_encrypt(v)
            for k, v in item.get("auth", {}).items()
            if v
        }
        output["accounts"].append(item)

    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_FILE)


# ---------------------------------------------------------------------------
# Command bot configuration
# ---------------------------------------------------------------------------
def load_command_bot_config():
    cfg = json.loads(json.dumps(COMMAND_BOT_DEFAULTS))
    try:
        if os.path.exists(COMMAND_BOT_CONFIG_FILE):
            with open(COMMAND_BOT_CONFIG_FILE, "r", encoding="utf-8") as f:
                stored = json.load(f)

            if isinstance(stored, dict):
                cfg["command_bot"] = stored.get(
                    "command_bot",
                    cfg["command_bot"]
                )

                for section in ("intern", "lumiastream", "streamerbot"):
                    if isinstance(stored.get(section), dict):
                        cfg[section].update(stored[section])

                if cfg["streamerbot"].get("password"):
                    try:
                        cfg["streamerbot"]["password"] = dpapi_decrypt(
                            cfg["streamerbot"]["password"]
                        )
                    except Exception:
                        pass
    except Exception:
        pass

    return cfg


def save_command_bot_config(cfg):
    output = json.loads(json.dumps(cfg))
    password = output.get("streamerbot", {}).get("password", "")

    if password:
        output["streamerbot"]["password"] = dpapi_encrypt(password)

    tmp = COMMAND_BOT_CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    os.replace(tmp, COMMAND_BOT_CONFIG_FILE)


# ---------------------------------------------------------------------------
# Bridge service
# ---------------------------------------------------------------------------
def youtube_extract_live_video_ids(html):
    """Return only videoRenderer entries that are actually marked LIVE."""
    found=[]
    decoder=json.JSONDecoder()

    for hit in re.finditer(r'"videoRenderer"\s*:\s*', html):
        pos=hit.end()
        while pos < len(html) and html[pos].isspace():
            pos += 1
        if pos >= len(html) or html[pos] != "{":
            continue
        try:
            obj,_=decoder.raw_decode(html[pos:])
        except Exception:
            continue

        vid=obj.get("videoId")
        if not isinstance(vid,str) or len(vid)!=11:
            continue

        blob=json.dumps(obj, ensure_ascii=False)
        live=(
            obj.get("isLiveNow") is True
            or '"style":"LIVE"' in blob
            or '"style": "LIVE"' in blob
            or '"label":"LIVE"' in blob
            or '"label": "LIVE"' in blob
            or "LIVE NOW" in blob.upper()
            or any(
                isinstance(x,dict) and "LIVE" in str(x.get("label","")).upper()
                for x in (obj.get("badges") or [])
            )
        )
        if live:
            found.append(vid)

    if not found:
        for pattern in (
            r'<link[^>]+rel="canonical"[^>]+href="https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})"',
            r'<meta[^>]+itemprop="videoId"[^>]+content="([A-Za-z0-9_-]{11})"',
            r'"currentVideoEndpoint".{0,1000}?"videoId":"([A-Za-z0-9_-]{11})"',
        ):
            hit=re.search(pattern,html,re.I|re.S)
            if hit:
                found.append(hit.group(1))
                break

    return list(dict.fromkeys(found))



class BridgeService:
    def __init__(self, manager):
        self.manager = manager
        self.process = None
        self.runtime_dir = os.path.join(tempfile.gettempdir(), "UnifiedChatBridge")
        self.log_file = os.path.join(self.runtime_dir, "bridge.log")
        self.starting = False
        self.process_lock = threading.RLock()

    def decrypt_auth(self, account):
        result = {}
        for k, v in account.get("auth", {}).items():
            try:
                result[k] = dpapi_decrypt(v)
            except Exception:
                result[k] = v
        return result

    def find(self, platform):
        for account in self.manager.accounts:
            if account.get("platform") == platform:
                return account
        return None

    def build_bridge_config(self):
        twitch = self.find("twitch")
        kick = self.find("kick")
        tiktok = self.find("tiktok")
        cng = self.find("cng")
        youtube_accounts = [
            a for a in self.manager.accounts
            if a.get("platform") == "youtube"
        ]
        youtube = youtube_accounts[0] if youtube_accounts else None

        # Keep the existing bridge configuration and update only settings
        # owned by the Manager. Platform account data is deliberately left out.
        cfg = {}
        config_path = os.path.join(BASE_DIR, BRIDGE_CONFIG_NAME)
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if isinstance(existing, dict):
                    cfg.update(existing)
            except Exception:
                pass

        cfg["display"] = dict(self.manager.display_settings)
        cfg["obs"] = dict(self.manager.obs_settings)
        cfg["alerts"] = {
            event_name: {
                "label": str(data.get("label", event_name.title()) or ""),
                "gif": str(data.get("gif", "") or ""),
                "sound": str(data.get("sound", "") or ""),
                "duration_seconds": max(1, min(300, int(data.get("duration_seconds", 5))))
            }
            for event_name, data in self.manager.alert_settings.items()
            if isinstance(data, dict)
        }

        # Remove legacy duplicated account fields if they are still present
        # in an older unified_chat_config.json. accounts.json is authoritative.
        for key in (
            "twitch_channel", "twitch_login", "twitch_client_id",
            "twitch_oauth_token", "twitch_refresh_token",
            "kick_channel", "kick_chatroom_id", "kick_pusher_app_key",
            "tiktok_channel", "cng_channel", "cng_session",
            "youtube_channel", "youtube_channels",
        ):
            cfg.pop(key, None)

        # Platform account data is intentionally NOT copied into
        # unified_chat_config.json. The Bridge reads accounts.json directly.
        # YouTube account/channel data is also read directly from accounts.json.
        return cfg

    def has_any_source(self):
        return any(self.find(p) for p in ("twitch", "kick", "tiktok", "cng", "youtube"))

    def write_persistent_config(self):
        """Create the persistent bridge config from the manager's real accounts."""
        config_path = os.path.join(BASE_DIR, BRIDGE_CONFIG_NAME)
        config = self.build_bridge_config()

        if not isinstance(config, dict):
            raise RuntimeError("Ungültige Bridge-Konfiguration.")

        tmp = config_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, config_path)
        return config_path

    def write_runtime_config(self):
        # Kept as a compatibility wrapper. There is no separate runtime
        # configuration file; the persistent unified_chat_config.json is used.
        return self.write_persistent_config()

    def start(self):
        if self.manager.quitting:
            return

        with self.process_lock:
            if self.manager.quitting:
                return
            if self.process and self.process.poll() is None:
                self.manager.update_status("Bridge läuft")
                return
            if self.starting:
                return

        if getattr(sys, "frozen", False):
            if not os.path.isfile(BRIDGE_EXE):
                self.manager.update_status("Bridge-EXE fehlt")
                messagebox.showerror(
                    APP_NAME,
                    "Die Bridge-EXE wurde nicht gefunden.\n\n"
                    + BRIDGE_EXE
                )
                return
        elif not os.path.isfile(BRIDGE_SOURCE):
            self.manager.update_status("Bridge-Datei fehlt")
            messagebox.showerror(APP_NAME, f"Nicht gefunden:\n{BRIDGE_SOURCE}")
            return

        if not self.has_any_source():
            self.manager.update_status("Keine Konten – Bridge wartet")
            return

        with self.process_lock:
            if self.manager.quitting or self.starting:
                return
            if self.process and self.process.poll() is None:
                self.manager.update_status("Bridge läuft")
                return
            self.starting = True
        self.manager.update_status("Bridge wird gestartet …")

        def worker():
            try:
                ensure_runtime_dependencies(self.manager.update_status)

                self.write_persistent_config()
                os.makedirs(self.runtime_dir, exist_ok=True)

                with open(self.log_file, "a", encoding="utf-8") as log:
                    kwargs = {
                        "cwd": BASE_DIR,
                        "stdin": subprocess.DEVNULL,
                        "stdout": log,
                        "stderr": log,
                        "text": True
                    }
                    # The live-log viewer reads this redirected output file.
                    # Unbuffered output makes print() and stderr messages
                    # visible while the bridge is still running.
                    kwargs["env"] = os.environ.copy()
                    kwargs["env"]["PYTHONUNBUFFERED"] = "1"

                    if os.name == "nt":
                        kwargs["creationflags"] = (
                            subprocess.CREATE_NO_WINDOW |
                            subprocess.CREATE_NEW_PROCESS_GROUP
                        )

                    if getattr(sys, "frozen", False):
                        # In the packaged application, sys.executable is the
                        # manager itself. Never launch it as the bridge.
                        bridge_cmd = [BRIDGE_EXE]
                    else:
                        bridge_cmd = [sys.executable, "-u", BRIDGE_SOURCE]

                    # Serialize process creation with shutdown. If the user
                    # exits from the tray while dependencies/config are being
                    # prepared, the bridge must not start after the Manager.
                    with self.process_lock:
                        if self.manager.quitting:
                            return
                        process = subprocess.Popen(bridge_cmd, **kwargs)
                        self.process = process

                if not self.manager.quitting:
                    self.manager.after(0, lambda: self.manager.update_status(
                        f"Bridge läuft (PID {process.pid})"
                    ))
                    self.manager.after(0, self.manager.update_bridge_buttons)
                    self.manager.after(0, self.monitor)

            except Exception as ex:
                if not self.manager.quitting:
                    self.manager.after(0, lambda: self.manager.update_status("Bridge-Start fehlgeschlagen"))
                    error_message = str(ex)
                    self.manager.after(
                        0,
                        lambda error=error_message: messagebox.showerror(
                            APP_NAME,
                            "Bridge konnte nicht gestartet werden:\n\n" + error
                        )
                    )
            finally:
                with self.process_lock:
                    self.starting = False

        threading.Thread(target=worker, daemon=True).start()

    def monitor(self):
        if self.manager.quitting:
            return
        if not self.process:
            return
        if self.process.poll() is None:
            self.manager.after(1500, self.monitor)
            return

        code = self.process.returncode
        self.process = None
        self.manager.update_bridge_buttons()
        self.manager.update_status(f"Bridge beendet (Code {code})")

    def stop(self):
        with self.process_lock:
            p = self.process
            if p:
                try:
                    if p.poll() is None:
                        if os.name == "nt":
                            # PyInstaller --onefile runs a bootloader parent and an
                            # application child. Terminating only Popen's PID leaves
                            # the child behind, so stop the complete process tree.
                            subprocess.run(
                                ["taskkill", "/PID", str(p.pid), "/T", "/F"],
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NO_WINDOW,
                                check=False,
                            )
                        else:
                            p.terminate()
                        try:
                            p.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            p.kill()
                            p.wait(timeout=2)
                except Exception:
                    pass
                finally:
                    self.process = None

        self.manager.update_bridge_buttons()
        self.manager.update_status("Bridge beendet")

    def restart(self):
        self.stop()
        self.manager.after(400, self.start)


# ---------------------------------------------------------------------------
# Single-instance protection
# ---------------------------------------------------------------------------
_single_instance_handle = None

def acquire_single_instance():
    """Allow only one CNG MultiChat Manager instance on Windows."""
    global _single_instance_handle
    if os.name != "nt":
        return True

    kernel32 = ctypes.windll.kernel32
    ERROR_ALREADY_EXISTS = 183
    mutex_name = "Global\\CNG MultiChat Manager_SingleInstance"

    handle = kernel32.CreateMutexW(None, False, mutex_name)
    if not handle:
        return True

    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False

    _single_instance_handle = handle
    return True


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class Manager(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.configure(bg="#111318")

        self.accounts = load_accounts()
        self.selected_id = None
        self.vars = {}
        self.auth_vars = {}
        self.tray_icon = None
        self.quitting = False
        self.hide_on_close = True
        self.bridge_log_window = None
        self.bridge_log_text = None
        self.bridge_log_offset = 0
        self.bridge_log_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.bridge_log_partial_line = ""
        self.bridge_log_follow = None
        self.update_status_var = tk.StringVar(master=self, value="Updateprüfung folgt beim Start.")
        self._update_check_lock = threading.Lock()
        self.display_settings = self.load_display_settings()
        self.app_settings = self.load_app_settings()
        self.alert_settings = self.load_alert_settings()
        self.command_bot_config = load_command_bot_config()
        self.obs_settings = self.load_obs_settings()
        if not os.path.exists(OBS_CONFIG_FILE):
            try:
                self.save_obs_settings()
            except Exception:
                pass

        self.setup_style()
        self.build_ui()
        self.refresh_accounts()
        self.new_account()

        # Tray is initialized after the GUI exists.
        self.after(300, self.start_tray)

        self.protocol("WM_DELETE_WINDOW", self.on_window_close)
        self.bind("<Unmap>", self.on_window_minimize, add="+")
        if self.app_settings.get("start_minimized", False):
            # Hide before Tk's first idle draw to avoid flashing the window.
            self.withdraw()

    def load_app_settings(self):
        settings = dict(APP_DEFAULTS)
        try:
            if os.path.exists(APP_SETTINGS_FILE):
                with open(APP_SETTINGS_FILE, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                if isinstance(stored, dict):
                    for key, default in APP_DEFAULTS.items():
                        settings[key] = bool(stored.get(key, default))
        except Exception:
            pass
        return settings

    def save_app_settings(self):
        tmp = APP_SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.app_settings, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, APP_SETTINGS_FILE)

    @staticmethod
    def set_windows_startup(enabled):
        if os.name != "nt":
            if enabled:
                raise OSError("Der Windows-Autostart ist nur unter Windows verfügbar.")
            return

        import winreg

        run_key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        value_name = "CNG MultiChat Manager"
        if getattr(sys, "frozen", False):
            command_parts = [sys.executable]
        else:
            command_parts = [sys.executable, os.path.abspath(__file__)]
        command = subprocess.list2cmdline(command_parts)

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            run_key_path,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, value_name)
                except FileNotFoundError:
                    pass

    def save_manager_start_options(self, new_settings):
        old_settings = dict(self.app_settings)
        try:
            self.set_windows_startup(bool(new_settings.get("start_with_windows", False)))
            self.app_settings = {
                key: bool(new_settings.get(key, default))
                for key, default in APP_DEFAULTS.items()
            }
            self.save_app_settings()
        except Exception:
            self.app_settings = old_settings
            try:
                self.set_windows_startup(bool(old_settings.get("start_with_windows", False)))
                self.save_app_settings()
            except Exception:
                pass
            raise

    def load_display_settings(self):
        settings = json.loads(json.dumps(DISPLAY_DEFAULTS))
        try:
            path = os.path.join(BASE_DIR, "display_settings.json")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    settings["auto_hide_messages"] = bool(
                        data.get("auto_hide_messages", False)
                    )
                    try:
                        seconds = int(data.get("message_lifetime_seconds", 30))
                    except (TypeError, ValueError):
                        seconds = 30
                    settings["message_lifetime_seconds"] = max(1, min(300, seconds))
                    style = str(data.get("chat_layout", data.get("chat_style", "classic"))).strip().lower()
                    settings["chat_layout"] = style if style in CHAT_STYLE_DEFS else "classic"
                    try:
                        font_size = int(data.get("chat_font_size", DISPLAY_DEFAULTS["chat_font_size"]))
                    except (TypeError, ValueError):
                        font_size = DISPLAY_DEFAULTS["chat_font_size"]
                    settings["chat_font_size"] = max(8, min(32, font_size))
                    color_mode = str(data.get("name_color_mode", "auto")).strip().lower()
                    settings["name_color_mode"] = color_mode if color_mode in DISPLAY_COLOR_MODES else "auto"
                    settings["fixed_name_color"] = normalize_display_color(
                        data.get("fixed_name_color"), DISPLAY_DEFAULTS["fixed_name_color"]
                    )
                    palette = data.get("name_color_palette")
                    if isinstance(palette, list):
                        cleaned_palette = [
                            normalize_display_color(color, "") for color in palette
                        ]
                        cleaned_palette = [color for color in cleaned_palette if color][:32]
                        settings["name_color_palette"] = cleaned_palette or list(DISPLAY_DEFAULTS["name_color_palette"])
                    platform_colors = data.get("platform_name_colors")
                    if isinstance(platform_colors, dict):
                        for platform in DISPLAY_PLATFORMS:
                            settings["platform_name_colors"][platform] = normalize_display_color(
                                platform_colors.get(platform),
                                DISPLAY_DEFAULTS["platform_name_colors"][platform],
                            )
                    settings["user_name_colors"] = normalize_display_color_map(
                        data.get("user_name_colors")
                    )
                    settings["message_text_color"] = normalize_display_color(
                        data.get("message_text_color"), DISPLAY_DEFAULTS["message_text_color"]
                    )
        except Exception:
            pass
        return settings

    def save_display_settings(self):
        path = os.path.join(BASE_DIR, "display_settings.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.display_settings, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

    def load_alert_settings(self):
        settings = json.loads(json.dumps(ALERT_EVENT_DEFAULTS))
        stored_fields = {}
        try:
            if os.path.exists(ALERT_CONFIG_FILE):
                with open(ALERT_CONFIG_FILE, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                if isinstance(stored, dict):
                    for event_name, defaults in ALERT_EVENT_DEFAULTS.items():
                        if isinstance(stored.get(event_name), dict):
                            settings[event_name].update(stored[event_name])
                            stored_fields[event_name] = set(stored[event_name])
        except Exception:
            pass

        # Before alert_settings.json existed, the manager stored these values
        # inside unified_chat_config.json. Import fields that are still absent
        # from the new file so an upgrade does not silently lose custom media.
        # Explicitly saved empty values remain empty, allowing users to clear a
        # GIF or sound intentionally.
        migrated = False
        try:
            bridge_config_path = os.path.join(BASE_DIR, BRIDGE_CONFIG_NAME)
            with open(bridge_config_path, "r", encoding="utf-8") as f:
                bridge_config = json.load(f)
            legacy_alerts = bridge_config.get("alerts", {}) if isinstance(bridge_config, dict) else {}
            if isinstance(legacy_alerts, dict):
                for event_name in ALERT_EVENT_DEFAULTS:
                    legacy = legacy_alerts.get(event_name, {})
                    if not isinstance(legacy, dict):
                        continue
                    saved_fields = stored_fields.get(event_name, set())
                    for field_name in ("label", "gif", "sound", "duration_seconds"):
                        value = legacy.get(field_name)
                        if field_name not in saved_fields and value not in (None, ""):
                            settings[event_name][field_name] = value
                            migrated = True
        except Exception:
            pass

        for event_name, defaults in ALERT_EVENT_DEFAULTS.items():
            try:
                seconds = int(settings[event_name].get("duration_seconds", defaults["duration_seconds"]))
            except (TypeError, ValueError):
                seconds = defaults["duration_seconds"]
            settings[event_name]["duration_seconds"] = max(1, min(300, seconds))
            settings[event_name]["gif"] = str(settings[event_name].get("gif", "") or "")
            settings[event_name]["sound"] = str(settings[event_name].get("sound", "") or "")

        if migrated:
            try:
                tmp = ALERT_CONFIG_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(settings, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                os.replace(tmp, ALERT_CONFIG_FILE)
            except OSError:
                pass

        return settings

    def save_alert_settings(self):
        # _vars contains Tkinter StringVar/IntVar objects used only by the UI.
        # They must NEVER be written to JSON.
        output = {}
        for event_name, defaults in ALERT_EVENT_DEFAULTS.items():
            data = self.alert_settings.get(event_name, {})
            if not isinstance(data, dict):
                data = {}

            try:
                seconds = int(data.get("duration_seconds", defaults["duration_seconds"]))
            except (TypeError, ValueError):
                seconds = defaults["duration_seconds"]

            output[event_name] = {
                "label": str(data.get("label", defaults["label"]) or defaults["label"]),
                "gif": str(data.get("gif", "") or ""),
                "sound": str(data.get("sound", "") or ""),
                "duration_seconds": max(1, min(300, seconds)),
            }

        tmp = ALERT_CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, ALERT_CONFIG_FILE)

    def load_obs_settings(self):
        settings = dict(OBS_DEFAULTS)
        try:
            if os.path.exists(OBS_CONFIG_FILE):
                with open(OBS_CONFIG_FILE, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                if isinstance(stored, dict):
                    settings.update({
                        "enabled": bool(stored.get("enabled", settings["enabled"])),
                        "host": str(stored.get("host", settings["host"]) or settings["host"]),
                        "port": int(stored.get("port", settings["port"])),
                        "password": str(stored.get("password", "") or ""),
                    })
                    if settings["password"]:
                        try:
                            settings["password"] = dpapi_decrypt(settings["password"])
                        except Exception:
                            pass
        except Exception:
            pass
        try:
            settings["port"] = max(1, min(65535, int(settings.get("port", 4455))))
        except (TypeError, ValueError):
            settings["port"] = 4455
        return settings

    def save_obs_settings(self):
        output = dict(self.obs_settings)
        if output.get("password"):
            output["password"] = dpapi_encrypt(str(output["password"]))
        tmp = OBS_CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, OBS_CONFIG_FILE)

    def show_obs_settings(self):
        self._show_manager_section(
            "OBS-Einstellungen",
            "OBS WebSocket konfigurieren und Verbindung testen",
            "OBS-VERBINDUNG",
            "Verbindungsdaten für OBS Studio",
        )
        self.clear_form()

        ttk.Label(self.form, text="OBS Studio", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            self.form,
            text="Verbindung zu OBS Studio über obs-websocket für OBS-Funktionen.",
            style="Sub.TLabel",
            wraplength=760
        ).pack(anchor="w", pady=(0, 18))

        box = ttk.LabelFrame(self.form, text="OBS WebSocket", padding=14)
        box.pack(fill="x", pady=(0, 14))
        box.columnconfigure(1, weight=1)

        enabled = tk.BooleanVar(value=bool(self.obs_settings.get("enabled", True)))
        host = tk.StringVar(value=str(self.obs_settings.get("host", "127.0.0.1")))
        port = tk.StringVar(value=str(self.obs_settings.get("port", 4455)))
        password = tk.StringVar(value=str(self.obs_settings.get("password", "")))

        ttk.Checkbutton(box, text="OBS WebSocket aktiviert", variable=enabled).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10)
        )
        ttk.Label(box, text="Host").grid(row=1, column=0, sticky="w", padx=(0, 15), pady=6)
        ttk.Entry(box, textvariable=host, style="TEntry").grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Label(box, text="Port").grid(row=2, column=0, sticky="w", padx=(0, 15), pady=6)
        ttk.Entry(box, textvariable=port, style="TEntry").grid(row=2, column=1, sticky="ew", pady=6)
        ttk.Label(box, text="Passwort").grid(row=3, column=0, sticky="w", padx=(0, 15), pady=6)
        ttk.Entry(box, textvariable=password, show="*", style="TEntry").grid(row=3, column=1, sticky="ew", pady=6)
        ttk.Label(
            box,
            text="Standard für OBS Studio 28+: 127.0.0.1:4455. Das Passwort wird lokal per Windows DPAPI verschlüsselt gespeichert.",
            style="Sub.TLabel", wraplength=720
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

        def collect():
            try:
                port_value = max(1, min(65535, int(port.get().strip())))
            except (TypeError, ValueError):
                messagebox.showwarning(APP_NAME, "Der OBS-Port muss eine Zahl zwischen 1 und 65535 sein.")
                return None
            host_value = host.get().strip() or "127.0.0.1"
            return {
                "enabled": bool(enabled.get()),
                "host": host_value,
                "port": port_value,
                "password": password.get(),
            }

        def save():
            values = collect()
            if values is None:
                return
            self.obs_settings = values
            try:
                self.save_obs_settings()
                if hasattr(self, "bridge") and self.bridge_has_process():
                    self.restart_bridge()
                elif hasattr(self, "bridge"):
                    self.bridge.write_runtime_config()
            except Exception as ex:
                messagebox.showerror(APP_NAME, "OBS-Einstellungen konnten nicht gespeichert werden:\n\n" + str(ex))
                return
            self.update_status("OBS-Einstellungen gespeichert")
            messagebox.showinfo(APP_NAME, "OBS-Einstellungen wurden gespeichert.")

        def test():
            values = collect()
            if values is None:
                return
            if not values["enabled"]:
                messagebox.showinfo(APP_NAME, "OBS WebSocket ist deaktiviert.")
                return

            def worker():
                try:
                    import websocket as wsclient
                    import hashlib as _hashlib
                    import base64 as _base64

                    ws = wsclient.create_connection(
                        f"ws://{values['host']}:{values['port']}",
                        timeout=5
                    )
                    hello = json.loads(ws.recv())
                    if hello.get("op") != 0:
                        raise RuntimeError("OBS hat keine gültige WebSocket-Hello-Nachricht gesendet.")

                    hello_data = hello.get("d") or {}
                    identify = {"rpcVersion": int(hello_data.get("rpcVersion", 1))}
                    auth = hello_data.get("authentication")
                    if auth:
                        if not values["password"]:
                            ws.close()
                            raise RuntimeError("OBS verlangt ein WebSocket-Passwort.")
                        secret = _base64.b64encode(
                            _hashlib.sha256(
                                (values["password"] + str(auth.get("salt", ""))).encode("utf-8")
                            ).digest()
                        ).decode("utf-8")
                        identify["authentication"] = _base64.b64encode(
                            _hashlib.sha256(
                                (secret + str(auth.get("challenge", ""))).encode("utf-8")
                            ).digest()
                        ).decode("utf-8")

                    ws.send(json.dumps({"op": 1, "d": identify}))
                    identified = json.loads(ws.recv())
                    if identified.get("op") != 2:
                        ws.close()
                        raise RuntimeError("OBS WebSocket-Authentifizierung fehlgeschlagen.")

                    request_id = f"manager-obs-{time.time_ns()}"
                    ws.send(json.dumps({
                        "op": 6,
                        "d": {
                            "requestType": "GetVersion",
                            "requestId": request_id
                        }
                    }))

                    response = json.loads(ws.recv())
                    ws.close()
                    response_data = response.get("d") or {}
                    if response.get("op") != 7 or response_data.get("requestId") != request_id:
                        raise RuntimeError("Keine gültige Antwort von OBS auf GetVersion.")
                    status = response_data.get("requestStatus") or {}
                    if not status.get("result"):
                        raise RuntimeError(str(status.get("comment") or "OBS hat die Anfrage abgelehnt."))

                    version = response_data.get("responseData") or {}
                    self.after(0, lambda: messagebox.showinfo(
                        APP_NAME,
                        "OBS-Verbindung OK.\n\n"
                        f"OBS-Version: {version.get('obsVersion', '?')}\n"
                        f"WebSocket: {version.get('obsWebSocketVersion', '?')}\n"
                        f"Endpoint: ws://{values['host']}:{values['port']}"
                    ))
                except Exception as ex:
                    self.after(0, lambda: messagebox.showerror(
                        APP_NAME, "OBS-Verbindung fehlgeschlagen:\n\n" + str(ex)
                    ))

            threading.Thread(target=worker, daemon=True).start()

        actions = tk.Frame(self.form, bg=self.colors["panel"])
        actions.pack(fill="x", pady=(4, 0))
        ttk.Button(actions, text="Verbindung testen", command=test).pack(side="left")
        ttk.Button(actions, text="Speichern", command=save, style="Accent.TButton").pack(side="right")
        self.update_status("OBS-Einstellungen")

    def show_alert_settings(self):
        self._show_manager_section(
            "Alert-Einstellungen",
            "GIFs und Sounds für CNG-Aktivitäten verwalten",
            "ALERT-EINSTELLUNGEN",
            "Medien und Anzeigedauer der Alerts",
        )
        self.clear_form()

        ttk.Label(self.form, text="Alert Einstellungen", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            self.form,
            text="GIF und Sound für CNG-Aktivitäten festlegen. Der Sound wird beim Start des Alerts einmal abgespielt; das GIF bleibt für die eingestellte Dauer sichtbar.",
            style="Sub.TLabel",
            wraplength=760
        ).pack(anchor="w", pady=(0, 18))

        event_titles = [("follow", "Follow"), ("donation", "Coin Donation"), ("unfollow", "Unfollow"), ("raid", "Raid")]

        for event_key, title in event_titles:
            data = self.alert_settings[event_key]
            box = ttk.LabelFrame(self.form, text=title, padding=14)
            box.pack(fill="x", pady=(0, 14))
            box.columnconfigure(1, weight=1)

            gif_var = tk.StringVar(value=data.get("gif", ""))
            sound_var = tk.StringVar(value=data.get("sound", ""))
            duration_var = tk.IntVar(value=int(data.get("duration_seconds", 5)))

            def choose_gif(var=gif_var):
                path = filedialog.askopenfilename(
                    parent=self,
                    title="GIF für Alert auswählen",
                    filetypes=[("GIF-Dateien", "*.gif"), ("Alle Dateien", "*.*")]
                )
                if path:
                    var.set(path)

            def choose_sound(var=sound_var):
                path = filedialog.askopenfilename(
                    parent=self,
                    title="Sound für Alert auswählen",
                    filetypes=[
                        ("Audio", "*.wav;*.mp3;*.ogg;*.m4a"),
                        ("WAV", "*.wav"),
                        ("MP3", "*.mp3"),
                        ("Alle Dateien", "*.*")
                    ]
                )
                if path:
                    var.set(path)

            ttk.Label(box, text="GIF").grid(row=0, column=0, sticky="w", padx=(0, 12), pady=6)
            ttk.Entry(box, textvariable=gif_var, style="TEntry").grid(row=0, column=1, sticky="ew", pady=6)
            ttk.Button(box, text="Auswählen…", command=choose_gif).grid(row=0, column=2, padx=(8, 0), pady=6)

            ttk.Label(box, text="Sound").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=6)
            ttk.Entry(box, textvariable=sound_var, style="TEntry").grid(row=1, column=1, sticky="ew", pady=6)
            ttk.Button(box, text="Auswählen…", command=choose_sound).grid(row=1, column=2, padx=(8, 0), pady=6)

            ttk.Label(box, text="Anzeige-Dauer").grid(row=2, column=0, sticky="w", padx=(0, 12), pady=6)
            spin = tk.Spinbox(
                box, from_=1, to=300, increment=1, width=8,
                textvariable=duration_var,
                bg="#0f0615", fg="#fff", insertbackground="#fff",
                buttonbackground="#29113a", relief="flat",
                highlightthickness=1, highlightbackground=self.colors["border"],
                highlightcolor=self.colors["orange"], justify="right"
            )
            spin.grid(row=2, column=1, sticky="w", pady=6)
            ttk.Label(box, text="Sekunden").grid(row=2, column=2, sticky="w", padx=(8, 0), pady=6)

            ttk.Label(
                box,
                text="Der Sound startet einmal beim Alert. Das GIF wird während der gesamten Anzeige-Dauer eingeblendet.",
                style="Sub.TLabel", wraplength=720
            ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))

            data["_vars"] = (gif_var, sound_var, duration_var)

        actions = tk.Frame(self.form, bg=self.colors["panel"])
        actions.pack(fill="x", pady=(4, 0))

        def save():
            for event_key, _ in event_titles:
                vars_tuple = self.alert_settings[event_key].get("_vars")
                if not vars_tuple or len(vars_tuple) != 3:
                    messagebox.showerror(
                        APP_NAME,
                        f"Die UI-Variablen für {self.alert_settings[event_key].get('label', event_key)} fehlen."
                    )
                    return

                gif_var, sound_var, duration_var = vars_tuple
                try:
                    seconds = max(1, min(300, int(duration_var.get())))
                except (TypeError, ValueError):
                    messagebox.showwarning(
                        APP_NAME,
                        f"Bitte bei {self.alert_settings[event_key]['label']} eine Dauer zwischen 1 und 300 Sekunden eingeben."
                    )
                    return

                self.alert_settings[event_key]["gif"] = gif_var.get().strip()
                self.alert_settings[event_key]["sound"] = sound_var.get().strip()
                self.alert_settings[event_key]["duration_seconds"] = seconds

            try:
                self.save_alert_settings()
            except Exception as ex:
                messagebox.showerror(
                    APP_NAME,
                    "Alert-Einstellungen konnten nicht gespeichert werden:\n\n" + str(ex)
                )
                return

            try:
                if hasattr(self, "bridge"):
                    self.bridge.write_runtime_config()
            except Exception as ex:
                # The alert JSON is already saved at this point. Do not report
                # this as a save failure; only the runtime refresh failed.
                messagebox.showwarning(
                    APP_NAME,
                    "Alert-Einstellungen wurden gespeichert.\n\n"
                    "Die laufende Runtime-Konfiguration konnte jedoch nicht aktualisiert werden:\n" + str(ex)
                )
                return

            self.update_status("Alert-Einstellungen gespeichert")

        ttk.Button(actions, text="Speichern", command=save).pack(side="right")

        def clear_temp_vars():
            for event_key, _ in event_titles:
                self.alert_settings[event_key].pop("_vars", None)

        # Remove temporary Tk variable references when navigating away is not necessary;
        # the variables are harmless and are replaced the next time this page is opened.
        self.update_status("Alert-Einstellungen")

    def show_settings(self):
        self._show_manager_section(
            "Einstellungen",
            "Manager-Start, Autostart und Darstellung konfigurieren",
            "ANZEIGEEINSTELLUNGEN",
            "Manager-Start und Chat-Anzeige",
        )
        self.clear_form()

        ttk.Label(self.form, text="Einstellungen", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            self.form,
            text="Manager-Verhalten unter Windows und Darstellung des MultiChat-Overlays konfigurieren.",
            style="Sub.TLabel"
        ).pack(anchor="w", pady=(0, 18))

        manager_box = ttk.LabelFrame(self.form, text="Manager und Windows", padding=14)
        manager_box.pack(fill="x", pady=(0, 14))

        startup_var = tk.BooleanVar(value=self.app_settings.get("start_with_windows", False))
        start_minimized_var = tk.BooleanVar(value=self.app_settings.get("start_minimized", False))
        minimize_to_tray_var = tk.BooleanVar(value=self.app_settings.get("minimize_to_tray", True))

        ttk.Checkbutton(
            manager_box,
            text="Manager beim Anmelden automatisch mit Windows starten",
            variable=startup_var,
        ).pack(anchor="w", pady=3)
        ttk.Checkbutton(
            manager_box,
            text="Manager beim Start minimiert im Infobereich anzeigen",
            variable=start_minimized_var,
        ).pack(anchor="w", pady=3)
        ttk.Checkbutton(
            manager_box,
            text="Beim Minimieren in den Infobereich verschieben",
            variable=minimize_to_tray_var,
        ).pack(anchor="w", pady=3)
        ttk.Label(
            manager_box,
            text="Das Schließen über X blendet den Manager ebenfalls in den Infobereich aus. Zum vollständigen Beenden im Tray-Menü „Beenden“ wählen.",
            style="Sub.TLabel",
            wraplength=760,
        ).pack(anchor="w", pady=(8, 0))

        box = ttk.LabelFrame(self.form, text="Chat-Design", padding=14)
        box.pack(fill="x", pady=(0, 14))
        box.columnconfigure(1, weight=1)

        style_var = tk.StringVar(value=self.display_settings.get("chat_layout", self.display_settings.get("chat_style", "classic")))
        preview = tk.Canvas(
            box, width=430, height=235, bg="#08090d", highlightthickness=1,
            highlightbackground=self.colors["border"]
        )
        preview.grid(row=0, column=1, rowspan=7, sticky="nsew", padx=(22, 0), pady=2)

        style_order = list(CHAT_STYLE_DEFS.keys())

        def draw_preview(*_):
            style_key = style_var.get()
            if style_key not in CHAT_STYLE_DEFS:
                style_key = "classic"
                style_var.set(style_key)

            preview.delete("all")
            w = 430
            h = 235
            preview.create_text(14, 12, anchor="nw", text="LIVE CHAT • VORSCHAU",
                                fill="#8f8798", font=("Segoe UI", 8, "bold"))

            samples = [
                ("TWITCH", "Beispielkanal", "Das sieht richtig gut aus!", "#9147ff", "#4b2674"),
                ("KICK", "Alex", "Läuft der Stream heute länger?", "#53e000", "#214f0e"),
                ("CNG", "Bot", "Neue Nachricht :happy:", "#ff8a00", "#613707"),
            ]

            y = 48
            for platform, name, text, accent, dark in samples:
                if style_key == "compact":
                    preview.create_rectangle(16, y, 414, y + 47, fill="#101217", outline="#262932")
                    preview.create_rectangle(16, y, 19, y + 47, fill=accent, outline="")
                    preview.create_text(28, y + 14, anchor="w", text=platform, fill=accent,
                                        font=("Segoe UI", 7, "bold"))
                    preview.create_text(86, y + 14, anchor="w", text=name, fill="#fff",
                                        font=("Segoe UI", 9, "bold"))
                    preview.create_text(28, y + 32, anchor="w", text=text, fill="#ddd",
                                        font=("Segoe UI", 9))
                elif style_key == "minimal":
                    preview.create_oval(18, y + 4, 46, y + 32, fill=dark, outline=accent, width=2)
                    preview.create_rectangle(54, y, 410, y + 52, fill="#0c0d11", outline="")
                    preview.create_text(66, y + 13, anchor="w", text=name, fill="#fff",
                                        font=("Segoe UI", 9, "bold"))
                    preview.create_text(126, y + 13, anchor="w", text=platform, fill=accent,
                                        font=("Segoe UI", 7, "bold"))
                    preview.create_text(66, y + 34, anchor="w", text=text, fill="#ddd",
                                        font=("Segoe UI", 9))
                elif style_key == "bubble":
                    preview.create_oval(18, y + 3, 49, y + 34, fill=dark, outline=accent, width=2)
                    preview.create_rounded_rectangle = getattr(preview, "create_rounded_rectangle", None)
                    # Tkinter Canvas has no rounded rectangle; approximate the bubble with a rectangle.
                    preview.create_rectangle(56, y, 410, y + 54, fill="#15171d", outline=accent, width=1)
                    preview.create_text(68, y + 14, anchor="w", text=name, fill="#fff",
                                        font=("Segoe UI", 9, "bold"))
                    preview.create_text(122, y + 14, anchor="w", text=platform, fill=accent,
                                        font=("Segoe UI", 7, "bold"))
                    preview.create_text(68, y + 36, anchor="w", text=text, fill="#eee",
                                        font=("Segoe UI", 9))
                elif style_key == "neon":
                    preview.create_rectangle(16, y, 414, y + 55, fill="#090b10", outline=accent, width=2)
                    preview.create_oval(25, y + 11, 51, y + 37, fill=dark, outline=accent, width=2)
                    preview.create_text(61, y + 14, anchor="w", text=name, fill="#fff",
                                        font=("Segoe UI", 9, "bold"))
                    preview.create_text(118, y + 14, anchor="w", text=platform, fill=accent,
                                        font=("Segoe UI", 7, "bold"))
                    preview.create_text(61, y + 37, anchor="w", text=text, fill="#eee",
                                        font=("Segoe UI", 9))
                else:  # classic
                    preview.create_oval(18, y + 4, 50, y + 36, fill=dark, outline="#454954")
                    preview.create_rectangle(58, y, 410, y + 56, fill="#111318", outline="#292c34")
                    preview.create_rectangle(58, y, 61, y + 56, fill=accent, outline="")
                    preview.create_text(70, y + 14, anchor="w", text=platform, fill=accent,
                                        font=("Segoe UI", 7, "bold"))
                    preview.create_text(122, y + 14, anchor="w", text=name, fill="#fff",
                                        font=("Segoe UI", 9, "bold"))
                    preview.create_text(70, y + 38, anchor="w", text=text, fill="#eee",
                                        font=("Segoe UI", 9))
                y += 61

            desc_var.set(CHAT_STYLE_DEFS[style_key]["description"])

        ttk.Label(box, text="Design auswählen").grid(row=0, column=0, sticky="w", padx=(0, 18), pady=(0, 8))
        desc_var = tk.StringVar()

        for row, key in enumerate(style_order, start=1):
            ttk.Radiobutton(
                box,
                text=CHAT_STYLE_DEFS[key]["name"],
                value=key,
                variable=style_var,
                command=draw_preview
            ).grid(row=row, column=0, sticky="w", pady=3)

        ttk.Label(box, textvariable=desc_var, style="Sub.TLabel", wraplength=250).grid(
            row=6, column=0, sticky="w", pady=(10, 0)
        )

        color_box = ttk.LabelFrame(self.form, text="Chat-Schrift und Farben", padding=14)
        color_box.pack(fill="x", pady=(0, 14))
        color_box.columnconfigure(1, weight=1)

        font_size_var = tk.IntVar(value=self.display_settings.get("chat_font_size", 14))
        ttk.Label(color_box, text="Schriftgröße (OBS-Text)").grid(row=0, column=0, sticky="w", padx=(0, 12))
        font_size_spin = tk.Spinbox(
            color_box, from_=8, to=32, increment=1, textvariable=font_size_var, width=7,
            bg="#0f0615", fg="#fff", insertbackground="#fff", buttonbackground="#29113a",
            relief="flat", highlightthickness=1, highlightbackground=self.colors["border"],
            highlightcolor=self.colors["orange"], justify="right"
        )
        font_size_spin.grid(row=0, column=1, sticky="w")
        ttk.Label(color_box, text="px (Livechat proportional; Dock A−/A+ bleibt)").grid(row=0, column=2, sticky="w", padx=(8, 0))

        color_mode_labels = {
            "auto": "Automatisch wie bisher",
            "palette": "Eigene Palette, stabil je Benutzer",
            "platform": "Feste Farbe je Plattform",
            "fixed": "Eine feste Farbe für alle Namen",
        }
        color_mode_reverse = {label: value for value, label in color_mode_labels.items()}
        color_mode_var = tk.StringVar(
            value=color_mode_labels.get(self.display_settings.get("name_color_mode", "auto"), color_mode_labels["auto"])
        )
        ttk.Label(color_box, text="Namensfarben").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=(10, 0))
        ttk.Combobox(
            color_box, textvariable=color_mode_var, values=list(color_mode_labels.values()),
            state="readonly", width=38
        ).grid(row=1, column=1, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(
            color_box,
            text="Feste Benutzerfarben gelten immer zuerst. Die automatische Einstellung behält die bisherige, stabile Namensfarbe bei.",
            style="Sub.TLabel", wraplength=760,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 12))

        def make_color_picker(parent, variable, row, column, fallback="#ffffff"):
            button = tk.Button(
                parent, text="Farbe wählen", command=lambda: choose_color(variable, button),
                bg=normalize_display_color(variable.get(), fallback), fg="#ffffff",
                activebackground=self.colors["orange"], activeforeground="#ffffff",
                relief="flat", padx=10, pady=3,
            )
            button.grid(row=row, column=column, sticky="w", padx=(8, 0))
            return button

        def choose_color(variable, button):
            selected = colorchooser.askcolor(
                color=normalize_display_color(variable.get(), "#ffffff"),
                parent=self,
                title="Farbe auswählen",
            )[1]
            if selected:
                variable.set(selected.lower())
                button.configure(bg=selected)

        fixed_name_color_var = tk.StringVar(value=self.display_settings.get("fixed_name_color", "#ffffff"))
        ttk.Label(color_box, text="Feste Namensfarbe").grid(row=3, column=0, sticky="w")
        fixed_name_entry = ttk.Entry(color_box, textvariable=fixed_name_color_var, width=12)
        fixed_name_entry.grid(row=3, column=1, sticky="w")
        make_color_picker(color_box, fixed_name_color_var, 3, 2)

        message_color_var = tk.StringVar(value=self.display_settings.get("message_text_color", "#f5f5f7"))
        ttk.Label(color_box, text="Nachrichtenfarbe").grid(row=4, column=0, sticky="w", pady=(7, 0))
        ttk.Entry(color_box, textvariable=message_color_var, width=12).grid(row=4, column=1, sticky="w", pady=(7, 0))
        make_color_picker(color_box, message_color_var, 4, 2)

        palette_box = ttk.LabelFrame(color_box, text="Eigene Farbpalette", padding=8)
        palette_box.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(12, 8))
        palette_box.columnconfigure(0, weight=1)
        palette_list = tk.Listbox(
            palette_box, height=5, exportselection=False, bg="#100817", fg="#ffffff",
            selectbackground=self.colors["purple"], selectforeground="#ffffff",
            highlightthickness=1, highlightbackground=self.colors["border"], relief="flat",
        )
        palette_list.grid(row=0, column=0, rowspan=3, sticky="nsew", padx=(0, 10))
        palette_var = tk.StringVar()
        ttk.Entry(palette_box, textvariable=palette_var, width=14).grid(row=0, column=1, sticky="w")
        palette_picker = make_color_picker(palette_box, palette_var, 0, 2)
        palette_buttons = ttk.Frame(palette_box)
        palette_buttons.grid(row=1, column=1, columnspan=2, sticky="w", pady=(6, 0))

        def refresh_palette(selected_index=None):
            palette_list.delete(0, "end")
            for color in palette_values:
                palette_list.insert("end", color)
            if selected_index is not None and palette_values:
                selected_index = min(selected_index, len(palette_values) - 1)
                palette_list.selection_set(selected_index)
                palette_list.activate(selected_index)

        def select_palette_color(_event=None):
            selected = palette_list.curselection()
            if selected:
                palette_var.set(palette_values[selected[0]])
                palette_picker.configure(bg=palette_values[selected[0]])

        def upsert_palette_color():
            color = normalize_display_color(palette_var.get(), "")
            if not color:
                messagebox.showwarning(APP_NAME, "Bitte eine gültige Farbe im Format #RRGGBB eingeben.")
                return
            selected = palette_list.curselection()
            if selected:
                palette_values[selected[0]] = color
                index = selected[0]
            else:
                if color not in palette_values:
                    palette_values.append(color)
                index = palette_values.index(color)
            refresh_palette(index)
            palette_var.set(color)
            palette_picker.configure(bg=color)

        def remove_palette_color():
            selected = palette_list.curselection()
            if not selected:
                return
            del palette_values[selected[0]]
            refresh_palette()

        palette_values = [
            normalize_display_color(color, "")
            for color in self.display_settings.get("name_color_palette", DISPLAY_DEFAULTS["name_color_palette"])
        ]
        palette_values = [color for color in palette_values if color]
        refresh_palette()
        palette_list.bind("<<ListboxSelect>>", select_palette_color)
        ttk.Button(palette_buttons, text="Hinzufügen / ersetzen", command=upsert_palette_color).pack(side="left", padx=(0, 5))
        ttk.Button(palette_buttons, text="Entfernen", command=remove_palette_color).pack(side="left", padx=5)
        ttk.Button(
            palette_buttons, text="Standardpalette", command=lambda: (palette_values.clear(), palette_values.extend(DISPLAY_DEFAULTS["name_color_palette"]), refresh_palette())
        ).pack(side="left", padx=5)
        ttk.Label(
            palette_box, text="Die Palette wird anhand des Benutzernamens stabil zugeordnet.", style="Sub.TLabel"
        ).grid(row=2, column=1, columnspan=2, sticky="w", pady=(6, 0))

        platform_box = ttk.LabelFrame(color_box, text="Feste Plattformfarben", padding=8)
        platform_box.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        platform_color_vars = {}
        for column, platform in enumerate(DISPLAY_PLATFORMS):
            platform_box.columnconfigure(column, weight=1)
            ttk.Label(platform_box, text=platform.upper()).grid(row=0, column=column, sticky="w", padx=(0, 6))
            color_var = tk.StringVar(
                value=self.display_settings.get("platform_name_colors", {}).get(
                    platform, DISPLAY_DEFAULTS["platform_name_colors"][platform]
                )
            )
            platform_color_vars[platform] = color_var
            ttk.Entry(platform_box, textvariable=color_var, width=10).grid(row=1, column=column, sticky="ew", padx=(0, 6))
            make_color_picker(platform_box, color_var, 2, column, DISPLAY_DEFAULTS["platform_name_colors"][platform])

        user_box = ttk.LabelFrame(color_box, text="Feste Farbe für einzelne Benutzer", padding=8)
        user_box.grid(row=7, column=0, columnspan=3, sticky="ew")
        user_box.columnconfigure(0, weight=1)
        user_color_list = tk.Listbox(
            user_box, height=5, exportselection=False, bg="#100817", fg="#ffffff",
            selectbackground=self.colors["purple"], selectforeground="#ffffff",
            highlightthickness=1, highlightbackground=self.colors["border"], relief="flat",
        )
        user_color_list.grid(row=0, column=0, rowspan=3, sticky="nsew", padx=(0, 10))
        user_name_var = tk.StringVar()
        user_color_var = tk.StringVar(value="#ffffff")
        ttk.Label(user_box, text="Benutzername").grid(row=0, column=1, sticky="w")
        ttk.Entry(user_box, textvariable=user_name_var, width=24).grid(row=0, column=2, sticky="w", padx=(6, 0))
        ttk.Label(user_box, text="Farbe").grid(row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Entry(user_box, textvariable=user_color_var, width=12).grid(row=1, column=2, sticky="w", padx=(6, 0), pady=(6, 0))
        user_color_picker = make_color_picker(user_box, user_color_var, 1, 3)
        user_color_values = dict(self.display_settings.get("user_name_colors", {}))

        def refresh_user_colors(selected_name=None):
            user_color_list.delete(0, "end")
            for username in sorted(user_color_values):
                user_color_list.insert("end", f"{username}    {user_color_values[username]}")
            if selected_name in user_color_values:
                index = sorted(user_color_values).index(selected_name)
                user_color_list.selection_set(index)
                user_color_list.activate(index)

        def select_user_color(_event=None):
            selected = user_color_list.curselection()
            if selected:
                username = sorted(user_color_values)[selected[0]]
                user_name_var.set(username)
                user_color_var.set(user_color_values[username])
                user_color_picker.configure(bg=user_color_values[username])

        def upsert_user_color():
            username = user_name_var.get().strip().lower()
            color = normalize_display_color(user_color_var.get(), "")
            if not username:
                messagebox.showwarning(APP_NAME, "Bitte einen Benutzernamen eingeben.")
                return
            if not color:
                messagebox.showwarning(APP_NAME, "Bitte eine gültige Farbe im Format #RRGGBB eingeben.")
                return
            user_color_values[username] = color
            refresh_user_colors(username)
            user_name_var.set(username)
            user_color_var.set(color)
            user_color_picker.configure(bg=color)

        def remove_user_color():
            selected = user_color_list.curselection()
            username = sorted(user_color_values)[selected[0]] if selected else user_name_var.get().strip().lower()
            if username in user_color_values:
                del user_color_values[username]
                refresh_user_colors()
                user_name_var.set("")

        user_color_list.bind("<<ListboxSelect>>", select_user_color)
        ttk.Button(user_box, text="Übernehmen", command=upsert_user_color).grid(row=2, column=1, sticky="w", pady=(6, 0))
        ttk.Button(user_box, text="Entfernen", command=remove_user_color).grid(row=2, column=2, sticky="w", padx=(6, 0), pady=(6, 0))
        ttk.Label(
            user_box,
            text="Benutzernamen werden ohne Beachtung der Groß-/Kleinschreibung verglichen und gelten plattformübergreifend.",
            style="Sub.TLabel", wraplength=480,
        ).grid(row=3, column=1, columnspan=3, sticky="w", pady=(6, 0))
        refresh_user_colors()

        box2 = ttk.LabelFrame(self.form, text="Nachrichten", padding=14)
        box2.pack(fill="x", pady=(0, 14))
        box2.columnconfigure(1, weight=1)

        auto_hide = tk.BooleanVar(value=self.display_settings.get("auto_hide_messages", False))
        lifetime = tk.IntVar(value=self.display_settings.get("message_lifetime_seconds", 30))

        ttk.Checkbutton(
            box2,
            text="Nachrichten automatisch ausblenden",
            variable=auto_hide,
            command=lambda: update_state()
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        ttk.Label(box2, text="Ausblenden nach").grid(row=1, column=0, sticky="w", padx=(0, 15))
        spin = tk.Spinbox(
            box2, from_=1, to=300, increment=1, textvariable=lifetime, width=7,
            bg="#0f0615", fg="#fff", insertbackground="#fff", buttonbackground="#29113a",
            relief="flat", highlightthickness=1, highlightbackground=self.colors["border"],
            highlightcolor=self.colors["orange"], justify="right"
        )
        spin.grid(row=1, column=1, sticky="w")
        ttk.Label(box2, text="Sekunden").grid(row=1, column=2, sticky="w", padx=(8, 0))
        ttk.Label(
            box2,
            text="Jede Nachricht erhält ihren eigenen Timer. Bei deaktivierter Option bleibt das bisherige Verhalten bestehen.",
            style="Sub.TLabel"
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))

        ttk.Label(
            self.form,
            text="Die Änderungen werden gespeichert und von der laufenden Overlay-Seite automatisch übernommen.",
            style="Sub.TLabel"
        ).pack(anchor="w")

        def update_state():
            spin.configure(state="normal" if auto_hide.get() else "disabled")

        def save():
            try:
                seconds = max(1, min(300, int(lifetime.get())))
            except (TypeError, ValueError):
                messagebox.showwarning(APP_NAME, "Bitte eine Zahl zwischen 1 und 300 Sekunden eingeben.")
                return

            try:
                chat_font_size = max(8, min(32, int(font_size_var.get())))
            except (TypeError, ValueError):
                messagebox.showwarning(APP_NAME, "Bitte eine Schriftgröße zwischen 8 und 32 px eingeben.")
                return

            color_values = {
                "Feste Namensfarbe": fixed_name_color_var.get(),
                "Nachrichtenfarbe": message_color_var.get(),
                **{f"Plattformfarbe {platform.upper()}": variable.get() for platform, variable in platform_color_vars.items()},
            }
            invalid = [label for label, value in color_values.items() if not normalize_display_color(value, "")]
            if invalid:
                messagebox.showwarning(APP_NAME, "Ungültiger Farbwert für: " + ", ".join(invalid) + ". Bitte #RRGGBB verwenden.")
                return
            if not palette_values:
                messagebox.showwarning(APP_NAME, "Die eigene Farbpalette benötigt mindestens eine Farbe.")
                return

            self.display_settings["chat_layout"] = style_var.get() if style_var.get() in CHAT_STYLE_DEFS else "classic"
            self.display_settings["auto_hide_messages"] = bool(auto_hide.get())
            self.display_settings["message_lifetime_seconds"] = seconds
            self.display_settings["chat_font_size"] = chat_font_size
            self.display_settings["name_color_mode"] = color_mode_reverse.get(color_mode_var.get(), "auto")
            self.display_settings["fixed_name_color"] = normalize_display_color(fixed_name_color_var.get(), "#ffffff")
            self.display_settings["message_text_color"] = normalize_display_color(message_color_var.get(), "#f5f5f7")
            self.display_settings["name_color_palette"] = [normalize_display_color(color, "#ffffff") for color in palette_values]
            self.display_settings["platform_name_colors"] = {
                platform: normalize_display_color(variable.get(), DISPLAY_DEFAULTS["platform_name_colors"][platform])
                for platform, variable in platform_color_vars.items()
            }
            self.display_settings["user_name_colors"] = normalize_display_color_map(user_color_values)
            try:
                self.save_manager_start_options({
                    "start_with_windows": startup_var.get(),
                    "start_minimized": start_minimized_var.get(),
                    "minimize_to_tray": minimize_to_tray_var.get(),
                })
                self.save_display_settings()
                if hasattr(self, "bridge"):
                    self.bridge.write_runtime_config()
            except Exception as ex:
                messagebox.showwarning(
                    APP_NAME,
                    "Einstellungen konnten nicht vollständig gespeichert werden:\n" + str(ex)
                )
                return
            self.update_status("Einstellungen gespeichert")

        actions = tk.Frame(self.form, bg=self.colors["panel"])
        actions.pack(fill="x", pady=(18, 0))
        ttk.Button(actions, text="Speichern", command=save).pack(side="right")

        draw_preview()
        update_state()

    def show_command_bot_settings(self):
        self._show_manager_section(
            "Command Bot",
            "Bot-Verbindung und Verarbeitung von CNG-Befehlen",
            "COMMAND-BOT-EINSTELLUNGEN",
            "Den aktiven Bot auswählen und einrichten",
        )
        self.clear_form()

        ttk.Label(
            self.form,
            text="Command Bot",
            style="Header.TLabel"
        ).pack(anchor="w")

        ttk.Label(
            self.form,
            text=(
                "CNG !Befehle werden an genau einen ausgewählten Bot "
                "weitergegeben. Cooldowns und Berechtigungen bleiben beim Bot."
            ),
            style="Sub.TLabel"
        ).pack(anchor="w", pady=(0, 18))

        box = ttk.LabelFrame(
            self.form,
            text="Aktiver Command Bot",
            padding=14
        )
        box.pack(fill="x", pady=(0, 14))
        box.columnconfigure(1, weight=1)

        bot_var = tk.StringVar(
            value=self.command_bot_config.get(
                "command_bot",
                "intern"
            )
        )

        ttk.Label(box, text="Bot").grid(
            row=0, column=0, sticky="w", padx=(0, 15), pady=7
        )

        bot_cb = ttk.Combobox(
            box,
            textvariable=bot_var,
            values=["intern", "lumiastream", "streamerbot"],
            state="readonly"
        )
        bot_cb.grid(row=0, column=1, sticky="ew", pady=7)

        details = ttk.LabelFrame(
            self.form,
            text="Verbindung",
            padding=14
        )
        details.pack(fill="x", pady=(0, 14))
        details.columnconfigure(1, weight=1)

        vars_ = {}

        def add_field(row, key, label, value="", secret=False):
            v = tk.StringVar(value=str(value))
            vars_[key] = v

            ttk.Label(details, text=label).grid(
                row=row, column=0, sticky="w",
                padx=(0, 15), pady=6
            )

            entry = ttk.Entry(
                details,
                textvariable=v,
                show="*" if secret else "",
                style="TEntry"
            )
            entry.grid(row=row, column=1, sticky="ew", pady=6)
            return entry

        def rebuild_details(*_):
            for w in details.winfo_children():
                w.destroy()

            details.columnconfigure(1, weight=1)

            bot = bot_var.get().strip().lower()

            if bot == "intern":
                intern = self.command_bot_config.setdefault("intern", {})
                add_field(
                    0, "intern_account_name", "Account-Name",
                    intern.get("account_name", "CNG-Bot")
                )
                add_field(
                    1, "intern_user_id", "User-ID",
                    intern.get("user_id", "")
                )
                add_field(
                    2, "intern_cng_session", "CNG Session",
                    intern.get("cng_session", ""), secret=True
                )
                ttk.Label(
                    details,
                    text="Der interne Bot sendet direkt über die CNG-API und benötigt eine gültige CNG-Session.",
                    style="Sub.TLabel"
                ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

            elif bot == "lumiastream":
                add_field(
                    0,
                    "lumi_api_url",
                    "API URL",
                    self.command_bot_config["lumiastream"].get(
                        "api_url",
                        "http://localhost:39231/api/send"
                    )
                )
                add_field(
                    1,
                    "lumi_token",
                    "Token",
                    self.command_bot_config["lumiastream"].get(
                        "token",
                        ""
                    ),
                    secret=True
                )

                ttk.Label(
                    details,
                    text=(
                        "LumiaStream verwendet weiterhin den vorhandenen "
                        "Command-Mechanismus."
                    ),
                    style="Sub.TLabel"
                ).grid(
                    row=2, column=0, columnspan=2,
                    sticky="w", pady=(8, 0)
                )

            else:
                sb = self.command_bot_config["streamerbot"]

                add_field(
                    0, "sb_scheme", "Scheme",
                    sb.get("scheme", "ws")
                )
                add_field(
                    1, "sb_host", "WebSocket Host",
                    sb.get("host", "127.0.0.1")
                )
                add_field(
                    2, "sb_port", "Port",
                    sb.get("port", 8080)
                )
                add_field(
                    3, "sb_endpoint", "Endpoint",
                    sb.get("endpoint", "/")
                )
                add_field(
                    4, "sb_password", "Passwort",
                    sb.get("password", ""),
                    secret=True
                )
                add_field(
                    5, "sb_action", "Action",
                    sb.get("action_name", "CNG Command Router")
                )
                add_field(
                    6, "sb_timeout", "Timeout (Sekunden)",
                    sb.get("timeout", 10)
                )

                ttk.Label(
                    details,
                    text=(
                        "Die Bridge ruft diese Streamer.bot-Action per "
                        "WebSocket DoAction auf und übergibt command, "
                        "message, username, userName, platform und userId."
                    ),
                    style="Sub.TLabel"
                ).grid(
                    row=7, column=0, columnspan=2,
                    sticky="w", pady=(8, 0)
                )

        bot_cb.bind("<<ComboboxSelected>>", rebuild_details)
        rebuild_details()

        info = ttk.LabelFrame(
            self.form,
            text="Streamer.bot Einrichtung",
            padding=14
        )
        info.pack(fill="x", pady=(0, 14))

        ttk.Label(
            info,
            text=(
                "In Streamer.bot muss eine Action mit dem oben eingetragenen "
                "Namen existieren. Empfohlen: „CNG Command Router“.\n\n"
                "Die Bridge erzeugt diese Action nicht automatisch. Sie ruft "
                "sie nur auf und übergibt die CNG-Daten als Argumente."
            ),
            style="Sub.TLabel",
            justify="left"
        ).pack(anchor="w")

        actions = tk.Frame(self.form, bg=self.colors["panel"])
        actions.pack(fill="x", pady=(8, 0))

        def test():
            self.save_command_bot_settings(
                bot_var,
                vars_,
                silent=True
            )

            bot = bot_var.get().strip().lower()

            if bot == "streamerbot":
                self.test_streamerbot()
            elif bot == "intern":
                messagebox.showinfo(
                    APP_NAME,
                    "Der interne Bot wird direkt über die CNG-API angesprochen."
                )
            else:
                messagebox.showinfo(
                    APP_NAME,
                    "LumiaStream wird weiterhin über den vorhandenen "
                    "API-Mechanismus angesprochen."
                )

        ttk.Button(
            actions,
            text="Verbindung testen",
            command=test
        ).pack(side="left")

        ttk.Button(
            actions,
            text="Speichern",
            command=lambda: self.save_command_bot_settings(
                bot_var,
                vars_,
                silent=False
            ),
            style="Accent.TButton"
        ).pack(side="right")

    def save_command_bot_settings(self, bot_var, vars_, silent=False):
        bot = bot_var.get().strip().lower()

        if bot not in ("intern", "lumiastream", "streamerbot"):
            bot = "intern"

        self.command_bot_config["command_bot"] = bot

        if bot == "intern":
            self.command_bot_config["intern"] = {
                "account_name": vars_.get(
                    "intern_account_name", tk.StringVar(value="CNG-Bot")
                ).get().strip() or "CNG-Bot",
                "user_id": vars_.get(
                    "intern_user_id", tk.StringVar()
                ).get().strip(),
                "cng_session": vars_.get(
                    "intern_cng_session", tk.StringVar()
                ).get().strip()
            }
        elif bot == "lumiastream":
            self.command_bot_config["lumiastream"] = {
                "api_url": vars_.get(
                    "lumi_api_url",
                    tk.StringVar()
                ).get().strip(),
                "token": vars_.get(
                    "lumi_token",
                    tk.StringVar()
                ).get().strip()
            }
        else:
            try:
                port = int(
                    vars_.get("sb_port", tk.StringVar(value="8080")).get()
                )
                timeout = int(
                    vars_.get("sb_timeout", tk.StringVar(value="10")).get()
                )
            except (TypeError, ValueError):
                messagebox.showwarning(
                    APP_NAME,
                    "Port und Timeout müssen Zahlen sein."
                )
                return False

            endpoint = vars_.get(
                "sb_endpoint",
                tk.StringVar(value="/")
            ).get().strip() or "/"

            if not endpoint.startswith("/"):
                endpoint = "/" + endpoint

            scheme = vars_.get(
                "sb_scheme",
                tk.StringVar(value="ws")
            ).get().strip().lower() or "ws"

            if scheme not in ("ws", "wss"):
                messagebox.showwarning(
                    APP_NAME,
                    "Scheme muss ws oder wss sein."
                )
                return False

            action_name = vars_.get(
                "sb_action",
                tk.StringVar()
            ).get().strip()

            if not action_name:
                messagebox.showwarning(
                    APP_NAME,
                    "Bitte eine Streamer.bot Action angeben."
                )
                return False

            self.command_bot_config["streamerbot"] = {
                "scheme": scheme,
                "host": vars_.get(
                    "sb_host",
                    tk.StringVar(value="127.0.0.1")
                ).get().strip() or "127.0.0.1",
                "port": port,
                "endpoint": endpoint,
                "password": vars_.get(
                    "sb_password",
                    tk.StringVar()
                ).get(),
                "action_name": action_name,
                "timeout": max(1, min(60, timeout))
            }

        save_command_bot_config(self.command_bot_config)

        if hasattr(self, "bridge") and self.bridge_has_process():
            self.restart_bridge()
        elif hasattr(self, "bridge"):
            self.bridge.write_runtime_config()

        if not silent:
            self.update_status(
                f"Command Bot gespeichert: {bot}"
            )
            messagebox.showinfo(
                APP_NAME,
                f"Command Bot „{bot}“ wurde gespeichert."
            )

        return True

    def test_streamerbot(self):
        cfg = self.command_bot_config.get("streamerbot", {})
        host = str(cfg.get("host", "127.0.0.1"))
        port = int(cfg.get("port", 8080))
        endpoint = str(cfg.get("endpoint", "/")) or "/"
        scheme = str(cfg.get("scheme", "ws")) or "ws"
        password = str(cfg.get("password", ""))

        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint

        def worker():
            try:
                import websocket as wsclient

                ws = wsclient.create_connection(
                    f"{scheme}://{host}:{port}{endpoint}",
                    timeout=max(1, int(cfg.get("timeout", 10)))
                )

                hello = json.loads(ws.recv())

                if hello.get("request") != "Hello":
                    raise RuntimeError(
                        "Unerwartete Antwort von Streamer.bot."
                    )

                auth = hello.get("authentication")

                if auth:
                    if not password:
                        raise RuntimeError(
                            "Streamer.bot verlangt ein Passwort."
                        )

                    secret = base64.b64encode(
                        hashlib.sha256(
                            (password + str(auth.get("salt", ""))).encode()
                        ).digest()
                    ).decode()

                    authentication = base64.b64encode(
                        hashlib.sha256(
                            (
                                secret +
                                str(auth.get("challenge", ""))
                            ).encode()
                        ).digest()
                    ).decode()

                    request_id = f"manager-auth-{time.time_ns()}"

                    ws.send(json.dumps({
                        "request": "Authenticate",
                        "id": request_id,
                        "authentication": authentication
                    }))

                    while True:
                        response = json.loads(ws.recv())
                        if response.get("id") == request_id:
                            if response.get("status") != "ok":
                                raise RuntimeError(
                                    "Streamer.bot Authentifizierung fehlgeschlagen."
                                )
                            break

                request_id = f"manager-info-{time.time_ns()}"

                ws.send(json.dumps({
                    "request": "GetInfo",
                    "id": request_id
                }))

                while True:
                    response = json.loads(ws.recv())
                    if response.get("id") == request_id:
                        break

                ws.close()

                if response.get("status") != "ok":
                    raise RuntimeError(
                        "GetInfo wurde von Streamer.bot abgelehnt."
                    )

                info = response.get("info") or {}

                self.after(
                    0,
                    lambda: messagebox.showinfo(
                        APP_NAME,
                        "Streamer.bot-Verbindung OK.\n\n"
                        f"Name: {info.get('name', '?')}\n"
                        f"Version: {info.get('version', '?')}\n"
                        f"Endpoint: {scheme}://{host}:{port}{endpoint}"
                    )
                )

            except Exception as ex:
                self.after(
                    0,
                    lambda: messagebox.showerror(
                        APP_NAME,
                        "Streamer.bot-Verbindung fehlgeschlagen:\n\n"
                        + str(ex)
                    )
                )

        threading.Thread(target=worker, daemon=True).start()

    def setup_style(self):
        self.colors = {
            "bg": "#0b0612",
            "panel": "#13091e",
            "panel2": "#1b0d2b",
            "border": "#432653",
            "text": "#ffffff",
            "muted": "#b2a3c1",
            "orange": "#ff8a00",
            "purple": "#9146ff",
            "pink": "#ed38c8",
            "accent": "#d83bdd",
            "green": "#53fc18",
            "cyan": "#00f2ea",
            "red": "#ff3b30",
        }
        self.configure(bg=self.colors["bg"])
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background=self.colors["bg"])
        style.configure("TLabel", background=self.colors["bg"], foreground="#fff",
                        font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=self.colors["bg"], foreground=self.colors["muted"],
                        font=("Segoe UI", 9))
        style.configure("TButton", background=self.colors["panel2"], foreground="#fff",
                        borderwidth=0, focusthickness=0, padding=(12, 8),
                        font=("Segoe UI", 9, "bold"))
        style.map("TButton", background=[("active", "#29113a"), ("pressed", "#321348")])
        style.configure("Accent.TButton", background=self.colors["accent"], foreground="#ffffff",
                        padding=(14, 9))
        style.map("Accent.TButton", background=[("active", "#ed54df"), ("pressed", "#b82ac4")])
        style.configure("Danger.TButton", background="#3a1018", foreground="#ff8d9d",
                        padding=(12, 8))
        style.map("Danger.TButton", background=[("active", "#541521")])
        style.configure("TEntry", fieldbackground="#0f0615", foreground="#fff",
                        insertcolor="#fff", bordercolor=self.colors["border"],
                        lightcolor=self.colors["border"], darkcolor=self.colors["border"],
                        padding=8)
        style.configure("TCombobox", fieldbackground="#0f0615", foreground="#fff",
                        insertcolor="#fff", bordercolor=self.colors["border"],
                        lightcolor=self.colors["border"], darkcolor=self.colors["border"],
                        padding=7)
        style.map("TCombobox", fieldbackground=[("readonly", "#0f0615")],
                  foreground=[("readonly", "#fff")])

    def build_ui(self):
        # Fit the initial window to the usable screen area. A lower minimum
        # keeps the fixed account and bridge controls reachable on shorter
        # displays and at Windows display scaling above 100%.
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        window_width = min(1180, max(900, screen_width - 80))
        window_height = min(760, max(560, screen_height - 100))
        self.geometry(f"{window_width}x{window_height}")
        self.minsize(min(900, window_width), min(560, window_height))

        outer = tk.Frame(self, bg=self.colors["bg"])
        outer.pack(fill="both", expand=True)

        # CNG-style vertical navigation
        sidebar = tk.Frame(outer, bg="#0d0414", width=225)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        brand = tk.Frame(sidebar, bg="#0d0414")
        brand.pack(fill="x", padx=18, pady=(22, 18))
        tk.Label(brand, text="CNG", bg="#0d0414", fg="#fff",
                 font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tk.Label(brand, text="CNG MultiChat Manager", bg="#0d0414",
                 fg=self.colors["pink"], font=("Segoe UI", 9, "bold")).pack(anchor="w")
        brand_rule = tk.Canvas(sidebar, bg="#0d0414", height=3, highlightthickness=0)
        brand_rule.pack(fill="x", padx=18, pady=(0, 18))
        brand_rule.bind("<Configure>", lambda event: self.draw_cng_gradient(brand_rule))

        tk.Label(sidebar, text="VERWALTUNG", bg="#0d0414", fg="#6f6278",
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=18, pady=(0, 8))

        self.nav_items = {}

        def nav(text, command=None, active=False, nested=False):
            bg = "#28133c" if active else "#0d0414"
            fg = self.colors["pink"] if active else "#d9d0df"
            f = tk.Frame(sidebar, bg=bg)
            f.pack(fill="x", padx=10, pady=3)
            marker = tk.Frame(f, bg=self.colors["pink"] if active else bg, width=3)
            marker.pack(side="left", fill="y")

            def select(section=text, callback=command):
                self.set_active_navigation(section)
                if callback:
                    callback()

            button = tk.Button(f, text=text, command=select, bg=bg, fg=fg,
                               activebackground="#28133c", activeforeground="#fff",
                               relief="flat", bd=0, anchor="w",
                               padx=21 if nested else 12,
                               pady=7 if nested else 9,
                               font=("Segoe UI", 8 if nested else 9, "bold"),
                               cursor="hand2")
            button.pack(fill="x")
            self.nav_items[text] = (f, marker, button)

        nav("▣   Konten & Accounts", command=self.show_account_manager, active=True)
        nav("●   Chat Bridge / Live-Log", command=self.show_bridge_page)
        nav("⚡   Alerts", command=self.show_alert_settings)
        nav("⚡   Command Bot", command=self.show_command_bot_settings)
        nav("◉   OBS", command=self.show_obs_settings)
        nav("⚙   Einstellungen", command=self.show_settings)
        nav("▣   Raspberry Pi", command=self.show_pi_deployment)
        nav("↳   Über / Kanäle", command=self.show_about, nested=True)

        tk.Label(sidebar, text="PLATTFORMEN", bg="#0d0414", fg="#6f6278",
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=18, pady=(28, 8))

        self.sidebar_platform_vars = {}
        platform_defs = [
            ("twitch", "TWITCH", self.colors["purple"]),
            ("kick", "KICK", self.colors["green"]),
            ("cng", "CNG", self.colors["orange"]),
            ("tiktok", "TIKTOK", self.colors["cyan"]),
            ("youtube", "YOUTUBE", self.colors["red"]),
        ]
        for platform, name, color in platform_defs:
            row = tk.Frame(sidebar, bg="#0d0414")
            row.pack(fill="x", padx=18, pady=4)
            dot = tk.Canvas(row, width=9, height=9, bg="#0d0414", highlightthickness=0)
            dot.create_oval(1, 1, 8, 8, fill=color, outline="")
            dot.pack(side="left")
            var = tk.StringVar(value=f"{name}  •  --")
            self.sidebar_platform_vars[platform] = var
            tk.Label(row, textvariable=var, bg="#0d0414", fg="#bdb4c4",
                     font=("Segoe UI", 8, "bold")).pack(side="left", padx=8)

        sb = tk.Frame(sidebar, bg="#0d0414")
        sb.pack(side="bottom", fill="x", padx=14, pady=16)
        tk.Label(sb, text="BRIDGE STATUS", bg="#0d0414", fg="#6f6278",
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.sidebar_bridge_status = tk.StringVar(value="Bereit")
        tk.Label(sb, textvariable=self.sidebar_bridge_status, bg="#0d0414",
                 fg=self.colors["green"], font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(4, 0))
        self.sidebar_app_version = tk.StringVar(value=f"Version {APP_VERSION}")
        tk.Label(sb, textvariable=self.sidebar_app_version, bg="#0d0414",
                 fg="#9c8da7", font=("Segoe UI", 8)).pack(anchor="w", pady=(6, 0))

        # Main area
        main = tk.Frame(outer, bg=self.colors["bg"])
        main.pack(side="left", fill="both", expand=True)

        header = tk.Frame(main, bg=self.colors["bg"])
        header.pack(fill="x", padx=30, pady=(25, 18))
        title = tk.Frame(header, bg=self.colors["bg"])
        title.pack(side="left")
        self.page_title_label = tk.Label(title, text="Konten verwalten", bg=self.colors["bg"], fg="#fff",
                                         font=("Segoe UI", 22, "bold"))
        self.page_title_label.pack(anchor="w")
        self.page_description_label = tk.Label(
            title,
            text="Accounts, Authentifizierung und Plattformverbindungen",
            bg=self.colors["bg"], fg=self.colors["muted"], font=("Segoe UI", 9)
        )
        self.page_description_label.pack(anchor="w", pady=(4, 0))
        self.top_status = tk.StringVar(value="CNG MultiChat Manager")
        tk.Label(header, textvariable=self.top_status, bg="#1b0d2b", fg=self.colors["pink"],
                 padx=14, pady=7, font=("Segoe UI", 8, "bold")).pack(side="right", anchor="n")

        # Keep the bridge controls in a compact toolbar above the account
        # editor instead of below it, where they could fall off short screens.
        bridge_box = tk.Frame(main, bg="#100518",
                              highlightbackground="#4a2059", highlightthickness=1)
        bridge_box.pack(fill="x", padx=30, pady=(0, 14))
        bl = tk.Frame(bridge_box, bg="#100518")
        bl.pack(side="left", fill="x", expand=True, padx=14, pady=9)
        tk.Label(bl, text="CHAT BRIDGE", bg="#100518", fg=self.colors["orange"],
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.bridge_status = tk.StringVar(value="Wird vorbereitet …")
        tk.Label(bl, textvariable=self.bridge_status, bg="#100518", fg="#fff",
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(2, 0))

        bb = tk.Frame(bridge_box, bg="#100518")
        bb.pack(side="right", padx=10, pady=8)
        self.log_btn = ttk.Button(bb, text="▤ Live-Log", command=self.open_bridge_log)
        self.log_btn.pack(side="left", padx=(0, 5))
        self.start_btn = ttk.Button(bb, text="▶ Starten", command=self.start_bridge, style="Accent.TButton")
        self.start_btn.pack(side="left", padx=(3, 0))
        self.restart_btn = ttk.Button(bb, text="↻ Neustarten", command=self.restart_bridge)
        self.restart_btn.pack(side="left", padx=(5, 0))
        self.stop_btn = ttk.Button(bb, text="■ Beenden", command=self.stop_bridge, style="Danger.TButton")
        self.stop_btn.pack(side="left", padx=(5, 0))

        cards = tk.Frame(main, bg=self.colors["bg"])
        cards.pack(fill="x", padx=30, pady=(0, 18))
        self.platform_status_frame = cards
        self.platform_status_vars = {}
        self.platform_status_labels = {}

        for platform, name, color in platform_defs:
            card = tk.Frame(cards, bg=self.colors["panel2"],
                            highlightbackground=self.colors["border"], highlightthickness=1)
            card.pack(side="left", fill="x", expand=True, padx=(0, 8))
            tk.Frame(card, bg=color, width=3).pack(side="left", fill="y")
            body = tk.Frame(card, bg=self.colors["panel2"])
            body.pack(fill="both", expand=True, padx=10, pady=9)
            tk.Label(body, text=name, bg=self.colors["panel2"], fg=color,
                     font=("Segoe UI", 8, "bold")).pack(anchor="w")
            var = tk.StringVar(value="nicht konfiguriert")
            self.platform_status_vars[platform] = var
            lbl = tk.Label(body, textvariable=var, bg=self.colors["panel2"], fg="#ded6e4",
                           font=("Segoe UI", 8))
            lbl.pack(anchor="w", pady=(3, 0))
            self.platform_status_labels[platform] = lbl

        content = tk.Frame(main, bg=self.colors["bg"])
        content.pack(fill="both", expand=True, padx=30)

        list_panel = tk.Frame(content, bg=self.colors["panel"],
                              highlightbackground=self.colors["border"], highlightthickness=1,
                              width=275)
        self.account_list_panel = list_panel
        list_panel.pack(side="left", fill="y", padx=(0, 14))
        list_panel.pack_propagate(False)
        tk.Label(list_panel, text="MEINE KONTEN", bg=self.colors["panel"], fg="#fff",
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(16, 2))
        tk.Label(list_panel, text="Plattformverbindungen", bg=self.colors["panel"], fg=self.colors["muted"],
                 font=("Segoe UI", 8)).pack(anchor="w", padx=16, pady=(0, 12))

        list_wrap = tk.Frame(list_panel, bg=self.colors["panel"])
        list_wrap.pack(fill="both", expand=True, padx=12)
        self.account_list = tk.Listbox(
            list_wrap, bg="#0d0414", fg="#eee8f1", selectbackground="#3a1650",
            selectforeground="#fff", activestyle="none", relief="flat", bd=0,
            highlightthickness=0, font=("Segoe UI", 9), selectborderwidth=0)
        self.account_list.pack(fill="both", expand=True)
        self.account_list.bind("<<ListboxSelect>>", self.on_select)

        lb = tk.Frame(list_panel, bg=self.colors["panel"])
        lb.pack(fill="x", padx=12, pady=12)
        tk.Button(lb, text="+ Neues Konto", command=self.new_account,
                  bg=self.colors["accent"], fg="#ffffff", activebackground="#ed54df",
                  relief="flat", bd=0, pady=8, font=("Segoe UI", 9, "bold"),
                  cursor="hand2").pack(fill="x")
        tk.Button(lb, text="Konto löschen", command=self.delete_account,
                  bg="#28101a", fg="#ff9baa", activebackground="#431522",
                  relief="flat", bd=0, pady=7, font=("Segoe UI", 8, "bold"),
                  cursor="hand2").pack(fill="x", pady=(6, 0))

        editor = tk.Frame(content, bg=self.colors["panel"],
                          highlightbackground=self.colors["border"], highlightthickness=1)
        self.editor = editor
        editor.pack(side="left", fill="both", expand=True)

        eh = tk.Frame(editor, bg=self.colors["panel"])
        eh.pack(fill="x", padx=20, pady=(17, 5))
        self.editor_title_label = tk.Label(eh, text="KONTO KONFIGURATION", bg=self.colors["panel"], fg="#fff",
                                           font=("Segoe UI", 11, "bold"))
        self.editor_title_label.pack(anchor="w")
        self.editor_description_label = tk.Label(
            eh, text="Verbindung und Authentifizierung der ausgewählten Plattform",
            bg=self.colors["panel"], fg=self.colors["muted"], font=("Segoe UI", 8)
        )
        self.editor_description_label.pack(anchor="w", pady=(3, 0))

        form_view = tk.Frame(editor, bg=self.colors["panel"])
        form_view.pack(fill="both", expand=True, padx=20, pady=(8, 0))

        self.form_scrollbar = ttk.Scrollbar(form_view, orient="vertical")
        self.form_canvas = tk.Canvas(
            form_view,
            bg=self.colors["panel"],
            highlightthickness=0,
            borderwidth=0,
            yscrollcommand=self.form_scrollbar.set,
        )
        self.form_scrollbar.configure(command=self.form_canvas.yview)
        self.form_scrollbar.pack(side="right", fill="y")
        self.form_canvas.pack(side="left", fill="both", expand=True)

        self.form = tk.Frame(self.form_canvas, bg=self.colors["panel"])
        self.form_canvas_window = self.form_canvas.create_window(
            (0, 0), window=self.form, anchor="nw"
        )
        self.form.bind("<Configure>", self._update_form_scroll_region)
        self.form_canvas.bind("<Configure>", self._fit_form_to_canvas)
        self.bind_all("<MouseWheel>", self._scroll_form_with_mousewheel, add="+")
        self.bind_all("<Button-4>", self._scroll_form_with_mousewheel, add="+")
        self.bind_all("<Button-5>", self._scroll_form_with_mousewheel, add="+")

        actions = tk.Frame(editor, bg=self.colors["panel"])
        self.account_actions = actions
        actions.pack(fill="x", padx=20, pady=14)
        tk.Button(actions, text="Verbindung testen", command=self.test_current,
                  bg="#24112f", fg="#fff", activebackground="#3a1650",
                  relief="flat", bd=0, padx=14, pady=8,
                  font=("Segoe UI", 9, "bold"), cursor="hand2").pack(side="right", padx=(8, 0))
        tk.Button(actions, text="Speichern", command=self.save_current,
                  bg=self.colors["accent"], fg="#ffffff", activebackground="#ed54df",
                  relief="flat", bd=0, padx=18, pady=8,
                  font=("Segoe UI", 9, "bold"), cursor="hand2").pack(side="right")

        self.update_bridge_buttons()

    def set_active_navigation(self, active_text):
        for text, (frame, marker, button) in getattr(self, "nav_items", {}).items():
            active = text == active_text
            bg = "#28133c" if active else "#0d0414"
            frame.configure(bg=bg)
            marker.configure(bg=self.colors["pink"] if active else bg)
            button.configure(
                bg=bg,
                fg=self.colors["pink"] if active else "#d9d0df",
            )

    def draw_cng_gradient(self, canvas):
        """Draw the orange-to-pink-to-violet accent used by the CNG website."""
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        stops = (self.colors["orange"], self.colors["pink"], self.colors["purple"])

        def rgb(value):
            value = value.lstrip("#")
            return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))

        def hex_color(parts):
            return "#%02x%02x%02x" % parts

        first, middle, last = (rgb(color) for color in stops)
        segments = min(180, width)
        canvas.delete("cng-gradient")
        for index in range(segments):
            position = index / max(1, segments - 1)
            if position <= 0.5:
                start, end, ratio = first, middle, position * 2
            else:
                start, end, ratio = middle, last, (position - 0.5) * 2
            color = hex_color(tuple(round(a + (b - a) * ratio) for a, b in zip(start, end)))
            x0 = int(index * width / segments)
            x1 = int((index + 1) * width / segments) + 1
            canvas.create_rectangle(
                x0, 0, x1, height, fill=color, outline="", tags="cng-gradient"
            )

    def _show_manager_section(
        self, page_title, page_description, editor_title, editor_description,
        show_accounts=False
    ):
        self.page_title_label.configure(text=page_title)
        self.page_description_label.configure(text=page_description)
        self.editor_title_label.configure(text=editor_title)
        self.editor_description_label.configure(text=editor_description)

        if show_accounts:
            if not self.account_list_panel.winfo_manager():
                self.account_list_panel.pack(
                    side="left", fill="y", padx=(0, 14), before=self.editor
                )
            if not self.account_actions.winfo_manager():
                self.account_actions.pack(fill="x", padx=20, pady=14)
        else:
            self.account_list_panel.pack_forget()
            self.account_actions.pack_forget()

    def show_account_manager(self):
        self._show_manager_section(
            "Konten verwalten",
            "Accounts, Authentifizierung und Plattformverbindungen",
            "KONTO KONFIGURATION",
            "Verbindung und Authentifizierung der ausgewählten Plattform",
            show_accounts=True,
        )

        selected_index = next(
            (index for index, account in enumerate(self.accounts)
             if account.get("id") == self.selected_id),
            None,
        )
        if selected_index is None:
            self.selected_id = None
            self.new_account()
        else:
            self.account_list.selection_clear(0, "end")
            self.account_list.selection_set(selected_index)
            self.account_list.see(selected_index)
            self.on_select()

    def start_update_check(self, manual=False):
        if not getattr(sys, "frozen", False):
            self.update_status_var.set("Automatische Updates stehen im fertigen Windows-Paket zur Verfügung.")
            return
        if not self._update_check_lock.acquire(blocking=False):
            self.update_status_var.set("Die Updateprüfung läuft bereits.")
            return

        self.update_status_var.set("Suche nach einer neueren stabilen Version …")
        button = getattr(self, "update_check_button", None)
        if button is not None:
            button.configure(state="disabled")

        def set_status(message):
            if self.quitting:
                return
            try:
                self.after(0, lambda message=message: self.update_status_var.set(message))
            except tk.TclError:
                pass

        def worker():
            stage_directory = None
            try:
                update = latest_update(APP_VERSION)
                if update is None:
                    set_status(f"Du verwendest die aktuelle Version {APP_VERSION}.")
                    return
                set_status(
                    f"Version {update['version']} gefunden. Prüfe Herausgebersignatur und lade das Paket …"
                )
                stage_directory = download_and_stage(update, BASE_DIR)
                self.after(
                    0,
                    lambda stage=stage_directory, version=update["version"]:
                    self._launch_verified_update(stage, version),
                )
                stage_directory = None
            except Exception as ex:
                set_status(f"Update nicht installiert; der Manager läuft weiter. {ex}")
            finally:
                if stage_directory:
                    shutil.rmtree(stage_directory, ignore_errors=True)
                self._update_check_lock.release()

                def restore_button():
                    button = getattr(self, "update_check_button", None)
                    if button is None:
                        return
                    try:
                        if button.winfo_exists():
                            button.configure(state="normal")
                    except tk.TclError:
                        pass

                try:
                    self.after(0, restore_button)
                except tk.TclError:
                    pass

        threading.Thread(target=worker, name="CNGUpdateCheck", daemon=True).start()

    def _launch_verified_update(self, stage_directory, version):
        if self.quitting:
            shutil.rmtree(stage_directory, ignore_errors=True)
            return
        self.update_status_var.set(f"Version {version} ist bereit. Der Manager wird aktualisiert und neu gestartet …")
        try:
            bridge_pid = None
            if hasattr(self, "bridge") and self.bridge.process is not None:
                bridge_pid = self.bridge.process.pid
            start_external_updater(stage_directory, BASE_DIR, os.getpid(), bridge_pid)
        except Exception as ex:
            shutil.rmtree(stage_directory, ignore_errors=True)
            self.update_status_var.set(f"Update konnte nicht gestartet werden: {ex}")
            return
        self.quit_application()

    def show_about(self):
        self._show_manager_section(
            "Über CNG MultiChat",
            "Entwickler, Projekt und Kanäle",
            "ÜBER CNG MULTICHAT",
            "CNG MultiChat und die Kanäle von Timur18o4",
        )
        self.clear_form()

        hero = tk.Frame(
            self.form,
            bg=self.colors["panel2"],
            highlightbackground=self.colors["border"],
            highlightthickness=1,
        )
        hero.pack(fill="x", pady=(0, 18))
        hero_text = tk.Frame(hero, bg=self.colors["panel2"])
        hero_text.pack(fill="x", padx=22, pady=(18, 16))
        tk.Label(
            hero_text,
            text="CNG MULTICHAT",
            bg=self.colors["panel2"],
            fg="#ffffff",
            font=("Segoe UI", 18, "bold"),
        ).pack(anchor="w")
        tk.Label(
            hero_text,
            text="Von Timur18o4 für Creator und Community",
            bg=self.colors["panel2"],
            fg=self.colors["pink"],
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", pady=(4, 0))
        tk.Label(
            hero_text,
            text="Kleine Creator. Große Zukunft.",
            bg=self.colors["panel2"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(5, 0))
        gradient = tk.Canvas(hero, bg=self.colors["panel2"], height=4, highlightthickness=0)
        gradient.pack(fill="x", side="bottom")
        gradient.bind("<Configure>", lambda event: self.draw_cng_gradient(gradient))

        update_card = tk.Frame(
            self.form,
            bg=self.colors["panel2"],
            highlightbackground=self.colors["border"],
            highlightthickness=1,
        )
        update_card.pack(fill="x", pady=(0, 18))
        update_content = tk.Frame(update_card, bg=self.colors["panel2"])
        update_content.pack(fill="x", padx=18, pady=14)
        tk.Label(
            update_content,
            text=f"Programmversion {APP_VERSION}",
            bg=self.colors["panel2"],
            fg="#ffffff",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")
        tk.Label(
            update_content,
            textvariable=self.update_status_var,
            bg=self.colors["panel2"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
            justify="left",
            wraplength=620,
        ).pack(anchor="w", pady=(5, 10))
        self.update_check_button = ttk.Button(
            update_content,
            text="Jetzt nach Updates suchen",
            command=lambda: self.start_update_check(manual=True),
        )
        self.update_check_button.pack(anchor="w")

        ideas_card = tk.Frame(
            self.form,
            bg=self.colors["panel2"],
            highlightbackground=self.colors["border"],
            highlightthickness=1,
        )
        ideas_card.pack(fill="x", pady=(0, 18))
        ideas_content = tk.Frame(ideas_card, bg=self.colors["panel2"])
        ideas_content.pack(fill="x", padx=18, pady=14)
        tk.Label(
            ideas_content,
            text="Ideen und Verbesserungsvorschläge",
            bg=self.colors["panel2"],
            fg="#ffffff",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")
        tk.Label(
            ideas_content,
            text=(
                "Reiche Vorschläge öffentlich über GitHub ein. Zum Veröffentlichen "
                "ist ein GitHub-Konto nötig. Bitte keine Tokens, Passwörter oder "
                "privaten Logs teilen."
            ),
            bg=self.colors["panel2"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
            justify="left",
            wraplength=620,
        ).pack(anchor="w", pady=(5, 10))
        tk.Button(
            ideas_content,
            text="Idee auf GitHub einreichen",
            command=self.open_ideas_discussion,
            bg=self.colors["orange"],
            fg="#17091d",
            activebackground="#ffb02e",
            activeforeground="#17091d",
            relief="flat",
            bd=0,
            padx=16,
            pady=9,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        ).pack(anchor="w")

        ttk.Label(self.form, text="Kanäle & Profile", style="Header.TLabel").pack(
            anchor="w", pady=(0, 4)
        )
        ttk.Label(
            self.form,
            text="Öffnet den jeweiligen Kanal in deinem Standardbrowser.",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(0, 12))

        channels = (
            ("CNG-Plattform", "timur18o4", "https://cng-plattform.com/timur18o4", self.colors["orange"]),
            ("Kick", "timur18o4", "https://kick.com/timur18o4", self.colors["green"]),
            ("Twitch", "timur18o4", "https://www.twitch.tv/timur18o4", self.colors["purple"]),
            ("TikTok", "@timur18o4", "https://www.tiktok.com/@timur18o4", self.colors["cyan"]),
            ("YouTube", "@timur18o4", "https://www.youtube.com/@timur18o4", self.colors["red"]),
        )
        channel_grid = tk.Frame(self.form, bg=self.colors["panel"])
        channel_grid.pack(fill="x", pady=(0, 14))
        channel_grid.columnconfigure(0, weight=1)
        channel_grid.columnconfigure(1, weight=1)

        for index, (platform, handle, url, color) in enumerate(channels):
            row, column = divmod(index, 2)
            card = tk.Frame(
                channel_grid,
                bg=self.colors["panel2"],
                highlightbackground=self.colors["border"],
                highlightthickness=1,
            )
            card.grid(
                row=row,
                column=column,
                sticky="nsew",
                padx=(0, 8) if column == 0 else (4, 0),
                pady=(0, 8),
            )
            tk.Frame(card, bg=color, width=4).pack(side="left", fill="y")
            button = tk.Button(
                card,
                text=f"{platform}  ·  {handle}     ↗",
                command=lambda target=url: webbrowser.open_new_tab(target),
                bg=self.colors["panel2"],
                fg="#ffffff",
                activebackground="#28133c",
                activeforeground="#ffffff",
                relief="flat",
                bd=0,
                anchor="w",
                padx=13,
                pady=15,
                font=("Segoe UI", 9, "bold"),
                cursor="hand2",
            )
            button.pack(fill="both", expand=True)

        self.update_status("Über CNG MultiChat")

    def open_ideas_discussion(self):
        try:
            opened = webbrowser.open_new_tab(IDEAS_DISCUSSION_URL)
        except Exception as ex:
            messagebox.showerror(
                APP_NAME,
                "GitHub konnte nicht im Standardbrowser geöffnet werden:\n\n" + str(ex),
            )
            return
        if not opened:
            messagebox.showwarning(
                APP_NAME,
                "Der Standardbrowser konnte nicht geöffnet werden. "
                "Öffne die GitHub-Projektseite manuell.",
            )

    def show_pi_deployment(self):
        self._show_manager_section(
            "Raspberry Pi",
            "Die aktuelle Bridge-Konfiguration per SSH auf Raspberry Pi OS übertragen",
            "PI-BEREITSTELLUNG",
            "Standalone-Bridge auf Raspberry Pi OS 64-Bit installieren oder aktualisieren",
        )
        self.clear_form()

        ttk.Label(self.form, text="Raspberry Pi bereitstellen", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            self.form,
            text=(
                "Überträgt Bridge, aktuelle gespeicherte Konten und Einstellungen sowie "
                "Alert-GIFs und Sounds. Der Pi installiert benötigte Python-Pakete in "
                "einer eigenen Umgebung und richtet die Bridge als Systemdienst ein. "
                "Für spätere Exporte wird die vorhandene Installation aktualisiert."
            ),
            style="Sub.TLabel",
            wraplength=780,
        ).pack(anchor="w", pady=(0, 16))

        fields = ttk.LabelFrame(self.form, text="SSH-Verbindung zum Raspberry Pi", padding=14)
        fields.pack(fill="x", pady=(0, 14))
        fields.columnconfigure(1, weight=1)

        host_var = tk.StringVar()
        port_var = tk.StringVar(value="22")
        username_var = tk.StringVar()
        ssh_password_var = tk.StringVar()
        sudo_password_var = tk.StringVar()

        ttk.Label(fields, text="IP-Adresse oder Hostname").grid(row=0, column=0, sticky="w", padx=(0, 14), pady=6)
        ttk.Entry(fields, textvariable=host_var, style="TEntry").grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Label(fields, text="SSH-Port").grid(row=1, column=0, sticky="w", padx=(0, 14), pady=6)
        ttk.Entry(fields, textvariable=port_var, style="TEntry", width=10).grid(row=1, column=1, sticky="w", pady=6)
        ttk.Label(fields, text="Linux-Benutzer").grid(row=2, column=0, sticky="w", padx=(0, 14), pady=6)
        ttk.Entry(fields, textvariable=username_var, style="TEntry").grid(row=2, column=1, sticky="ew", pady=6)
        ttk.Label(fields, text="SSH-Passwort").grid(row=3, column=0, sticky="w", padx=(0, 14), pady=6)
        ttk.Entry(fields, textvariable=ssh_password_var, show="*", style="TEntry").grid(row=3, column=1, sticky="ew", pady=6)
        ttk.Label(fields, text="sudo-Passwort (leer = SSH-Passwort)").grid(row=4, column=0, sticky="w", padx=(0, 14), pady=6)
        ttk.Entry(fields, textvariable=sudo_password_var, show="*", style="TEntry").grid(row=4, column=1, sticky="ew", pady=6)
        ttk.Label(
            fields,
            text=(
                "Das Linux-Konto muss SSH-Zugriff und sudo-Rechte haben. Passwörter "
                "werden nur für diese Übertragung im Arbeitsspeicher verwendet. Beim "
                "ersten Kontakt wird der SSH-Fingerabdruck zur Bestätigung angezeigt."
            ),
            style="Sub.TLabel",
            wraplength=760,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(9, 0))

        ttk.Label(
            self.form,
            text=(
                "Hinweis: Wenn OBS, LumiaStream oder Streamer.bot auf diesem Windows-PC "
                "laufen, werden lokale Zieladressen im Export auf dessen LAN-IP gesetzt. "
                "Die jeweiligen Dienste und die Windows-Firewall müssen Verbindungen "
                "aus dem LAN zulassen."
            ),
            style="Sub.TLabel",
            wraplength=790,
        ).pack(anchor="w", pady=(0, 12))

        stop_local_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self.form,
            text=(
                "Lokale Windows-Bridge nach erfolgreicher Pi-Prüfung beenden "
                "(verhindert doppelte Chat-/Alert-/Command-Verarbeitung)"
            ),
            variable=stop_local_var,
        ).pack(anchor="w", pady=(0, 12))

        log_frame = ttk.LabelFrame(self.form, text="Übertragungsprotokoll", padding=8)
        log_frame.pack(fill="both", expand=True, pady=(0, 12))
        log_text = tk.Text(
            log_frame, height=10, wrap="word", state="disabled",
            bg="#0d0414", fg="#e8deef", insertbackground="#ffffff",
            relief="flat", font=("Consolas", 9),
        )
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=log_text.yview)
        log_text.configure(yscrollcommand=log_scroll.set)
        log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        actions = tk.Frame(self.form, bg=self.colors["panel"])
        actions.pack(fill="x")
        start_button = ttk.Button(actions, text="Konfiguration sicher übertragen", style="Accent.TButton")
        start_button.pack(side="right")

        def append_log(line):
            try:
                if not log_text.winfo_exists():
                    return
                log_text.configure(state="normal")
                log_text.insert("end", str(line) + "\n")
                log_text.see("end")
                log_text.configure(state="disabled")
            except tk.TclError:
                pass

        def start_deployment():
            host = host_var.get().strip()
            username = username_var.get().strip()
            ssh_password = ssh_password_var.get()
            sudo_password = sudo_password_var.get() or ssh_password
            stop_local_after_success = bool(stop_local_var.get())
            try:
                port = int(port_var.get().strip())
                if not 1 <= port <= 65535:
                    raise ValueError
            except ValueError:
                messagebox.showwarning(APP_NAME, "Der SSH-Port muss zwischen 1 und 65535 liegen.")
                return
            if not host or not username:
                messagebox.showwarning(APP_NAME, "Bitte Pi-Adresse und Linux-Benutzer eintragen.")
                return
            if not ssh_password:
                messagebox.showwarning(APP_NAME, "Bitte das SSH-Passwort eintragen.")
                return
            if not hasattr(self, "bridge"):
                messagebox.showerror(APP_NAME, "Die Bridge-Konfiguration ist noch nicht verfügbar.")
                return

            # Capture only saved/current Manager values on the Tk thread. UI
            # variable objects in alert_settings are intentionally excluded.
            try:
                snapshot = {
                    "accounts": _json_clone(self.accounts),
                    "bridge_config": self.bridge.build_bridge_config(),
                    "command_bot_config": _json_clone(self.command_bot_config),
                    "alert_settings": {
                        event: {
                            key: value.get(key)
                            for key in ("label", "gif", "sound", "duration_seconds")
                        }
                        for event, value in self.alert_settings.items()
                        if isinstance(value, dict)
                    },
                    "display_settings": _json_clone(self.display_settings),
                    "obs_settings": _json_clone(self.obs_settings),
                }
            except Exception as ex:
                messagebox.showerror(APP_NAME, "Aktuelle Manager-Einstellungen konnten nicht vorbereitet werden:\n\n" + str(ex))
                return

            events = queue.Queue()
            decision = {"event": None, "accepted": False}
            start_button.configure(state="disabled")
            log_text.configure(state="normal")
            log_text.delete("1.0", "end")
            log_text.configure(state="disabled")
            append_log("Paket wird aus den gespeicherten Manager-Einstellungen erzeugt …")

            def worker():
                import pi_deployment

                cleanup = None
                try:
                    archive, local_ip, cleanup = pi_deployment.create_bundle(
                        BASE_DIR,
                        target_host=host,
                        **snapshot,
                    )
                    events.put(("log", f"Windows-LAN-IP für lokale Integrationen: {local_ip}"))
                    events.put(("log", "Prüfe den SSH-Hostschlüssel des Raspberry Pi …"))
                    key, fingerprint = pi_deployment.fetch_host_key(host, port)
                    if pi_deployment.host_key_is_trusted(host, port, key):
                        events.put(("log", "Gespeicherter SSH-Hostschlüssel stimmt überein."))
                    else:
                        key_decision = {"event": threading.Event(), "accepted": False}
                        events.put(("host_key", host, fingerprint, key, key_decision))
                        if not key_decision["event"].wait(300):
                            raise RuntimeError("Die Bestätigung des SSH-Fingerabdrucks ist abgelaufen.")
                        if not key_decision["accepted"]:
                            raise RuntimeError("SSH-Verbindung vom Benutzer abgebrochen.")
                        pi_deployment.trust_host_key(host, port, key)
                    events.put(("log", "SSH-Schlüssel bestätigt. Starte sichere Übertragung …"))
                    pi_deployment.deploy_bundle(
                        host, port, username, ssh_password, sudo_password, archive,
                        on_output=lambda line: events.put(("log", line)),
                    )
                    events.put(("done", f"Pi-Bereitstellung erfolgreich.\n\nOverlay: http://{host}:8765/\nLivechat: http://{host}:8765/livechat\nAlerts: http://{host}:8765/alerts\nStats: http://{host}:8765/stats"))
                except Exception as ex:
                    events.put(("error", str(ex)))
                finally:
                    if cleanup:
                        cleanup()
                    events.put(("finished",))

            def poll_events():
                try:
                    while True:
                        event = events.get_nowait()
                        if event[0] == "log":
                            append_log(event[1])
                        elif event[0] == "host_key":
                            _, remote_host, fingerprint, key, key_decision = event
                            accepted = messagebox.askyesno(
                                APP_NAME,
                                "Beim ersten SSH-Kontakt kann dieser Schlüssel noch nicht "
                                "mit einem gespeicherten Schlüssel verglichen werden. "
                                "Vergleiche den Fingerabdruck nach Möglichkeit mit einer "
                                "vertrauenswürdigen Quelle.\n\n"
                                f"Pi: {remote_host}:{port}\n"
                                f"Fingerabdruck: {fingerprint}\n\n"
                                "Diesen SSH-Schlüssel für künftige Verbindungen speichern?",
                            )
                            key_decision["accepted"] = accepted
                            key_decision["event"].set()
                        elif event[0] == "done":
                            decision["event"] = ("done", event[1])
                        elif event[0] == "error":
                            decision["event"] = ("error", event[1])
                        elif event[0] == "finished":
                            try:
                                if start_button.winfo_exists():
                                    start_button.configure(state="normal")
                            except tk.TclError:
                                pass
                            if decision["event"]:
                                kind, detail = decision["event"]
                                if kind == "done":
                                    append_log("Fertig.")
                                    if stop_local_after_success and self.bridge_has_process():
                                        self.stop_bridge()
                                    self.update_status("Pi-Bridge bereitgestellt")
                                    messagebox.showinfo(APP_NAME, detail)
                                else:
                                    append_log("FEHLER: " + detail)
                                    self.update_status("Pi-Bereitstellung fehlgeschlagen")
                                    messagebox.showerror(APP_NAME, "Pi-Bereitstellung fehlgeschlagen:\n\n" + detail)
                            return
                except queue.Empty:
                    pass
                self.after(100, poll_events)

            threading.Thread(target=worker, daemon=True).start()
            self.after(100, poll_events)

        start_button.configure(command=start_deployment)
        self.update_status("Raspberry Pi Bereitstellung")

    def show_bridge_page(self):
        self._show_manager_section(
            "Chat Bridge",
            "Status und Konsolenausgabe des Hintergrunddienstes",
            "BRIDGE-ÜBERSICHT",
            "Die Bridge-Ausgabe ist im Live-Log sichtbar.",
        )
        self.clear_form()
        ttk.Label(self.form, text="Chat Bridge", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            self.form,
            text="Die Bridge läuft als Hintergrundprozess. Öffne das Live-Log, um ihre aktuelle Konsolen- und Fehlerausgabe zu verfolgen.",
            style="Sub.TLabel",
            wraplength=760,
        ).pack(anchor="w", pady=(0, 16))
        ttk.Button(
            self.form, text="Live-Log öffnen", command=self.open_bridge_log
        ).pack(anchor="w")
        self.update_status("Chat Bridge")
        self.open_bridge_log()

    def update_platform_status(self):
        if not hasattr(self, "platform_status_vars"):
            return

        defs = [
            ("twitch", "TWITCH"),
            ("kick", "KICK"),
            ("cng", "CNG"),
            ("tiktok", "TIKTOK"),
            ("youtube", "YOUTUBE"),
        ]

        for platform, label in defs:
            account = next(
                (a for a in self.accounts if a.get("platform") == platform),
                None
            )
            if account:
                name = account.get("channel") or account.get("display_name") or "konfiguriert"
                value, state = name, "OK"
            else:
                value, state = "nicht konfiguriert", "--"

            if platform in self.platform_status_vars:
                self.platform_status_vars[platform].set(value)
            if hasattr(self, "sidebar_platform_vars") and platform in self.sidebar_platform_vars:
                self.sidebar_platform_vars[platform].set(f"{label}  •  {state}")

    def _update_form_scroll_region(self, event=None):
        if hasattr(self, "form_canvas"):
            self.form_canvas.configure(scrollregion=self.form_canvas.bbox("all"))

    def _fit_form_to_canvas(self, event):
        if hasattr(self, "form_canvas_window"):
            self.form_canvas.itemconfigure(self.form_canvas_window, width=event.width)

    def _scroll_form_with_mousewheel(self, event):
        canvas = getattr(self, "form_canvas", None)
        if canvas is None:
            return

        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None and widget is not canvas:
            widget = getattr(widget, "master", None)
        if widget is not canvas:
            return

        if getattr(event, "num", None) == 4:
            units = -3
        elif getattr(event, "num", None) == 5:
            units = 3
        else:
            delta = getattr(event, "delta", 0)
            units = int(-delta / 120)
            if units == 0 and delta:
                units = -1 if delta > 0 else 1

        if units:
            canvas.yview_scroll(units, "units")
        return "break"

    def update_status(self, text):
        if hasattr(self, "status_var"):
            self.status_var.set(text)
        if hasattr(self, "bridge_status"):
            self.bridge_status.set(text)
        if hasattr(self, "sidebar_bridge_status"):
            self.sidebar_bridge_status.set(text)
        if hasattr(self, "top_status"):
            self.top_status.set(text)

    def clear_form(self):
        for widget in self.form.winfo_children():
            widget.destroy()
        if hasattr(self, "form_canvas"):
            self.form_canvas.yview_moveto(0)

    def build_form(self):
        self.clear_form()

        ttk.Label(self.form, text="Konto", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            self.form,
            text="Plattform und Accountname eintragen. Authentifizierung wird nur angezeigt, wenn sie benötigt wird.",
            style="Sub.TLabel"
        ).pack(anchor="w", pady=(0,18))

        grid = ttk.Frame(self.form)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        self.vars = {}
        platform = tk.StringVar(value="twitch")
        self.vars["platform"] = platform

        ttk.Label(grid, text="Plattform").grid(row=0,column=0,sticky="w",pady=7,padx=(0,15))
        cb = ttk.Combobox(grid, textvariable=platform,
                          values=["twitch","kick","tiktok","cng","youtube"], state="readonly")
        cb.grid(row=0,column=1,sticky="ew",pady=7)
        cb.bind("<<ComboboxSelected>>", lambda e: self.rebuild_auth())

        self.vars["channel"] = tk.StringVar()
        ttk.Label(grid,text="Accountname / Kanal").grid(
            row=1,column=0,sticky="w",pady=7,padx=(0,15)
        )
        ttk.Entry(
            grid,textvariable=self.vars["channel"],style="TEntry"
        ).grid(row=1,column=1,sticky="ew",pady=7)

        # CNG only: the numeric creator ID is part of the CNG account
        # identity and must not be confused with the login/session cookie.
        self.cng_creator_id_frame = ttk.Frame(grid)
        self.cng_creator_id_var = tk.StringVar()
        self.cng_creator_id_entry = ttk.Entry(
            self.cng_creator_id_frame,
            textvariable=self.cng_creator_id_var,
            style="TEntry"
        )
        self.cng_creator_id_entry.pack(fill="x")

        self.auth_frame=ttk.LabelFrame(self.form,text="Authentifizierung",padding=10)
        self.rebuild_auth()

        self.cng_help_label = ttk.Label(
            self.form,
            text="",
            style="Sub.TLabel",
            justify="left",
            wraplength=760
        )

    def rebuild_auth(self):
        if not hasattr(self,"auth_frame"):
            return
        for w in self.auth_frame.winfo_children():
            w.destroy()

        platform=self.vars["platform"].get()
        self.auth_vars={}

        if hasattr(self, "cng_help_label"):
            self.cng_help_label.pack_forget()

        # CNG requires a numeric creator ID in addition to the channel name.
        if hasattr(self, "cng_creator_id_frame"):
            self.cng_creator_id_frame.grid_remove()

        if platform == "cng":
            self.cng_creator_id_frame.grid(
                row=3, column=1, sticky="ew", pady=7
            )
            # Keep the label aligned with the other account fields.
            if not hasattr(self, "cng_creator_id_label"):
                self.cng_creator_id_label = ttk.Label(
                    self.cng_creator_id_frame.master,
                    text="CNG Creator-ID"
                )
                self.cng_creator_id_label.grid(
                    row=3, column=0, sticky="w", pady=7, padx=(0,15)
                )
            else:
                self.cng_creator_id_label.grid(
                    row=3, column=0, sticky="w", pady=7, padx=(0,15)
                )

            ttk.Label(
                self.cng_creator_id_frame,
                text="Numerische ID deines CNG-Kanals, z. B. 30001.",
                style="Sub.TLabel"
            ).pack(anchor="w", pady=(3,0))

        if platform in ("kick","tiktok","youtube"):
            self.auth_frame.pack_forget()
            if platform=="tiktok":
                self.update_status("TikTok benötigt nur den Kanalnamen.")
            elif platform=="youtube":
                self.update_status("YouTube benötigt nur den Kanalnamen / Handle.")
            return

        self.auth_frame.pack(fill="x",pady=22)

        if platform=="twitch":
            fields=[
                ("client_id","Client ID"),
                ("access_token","Access Token"),
                ("refresh_token","Refresh Token")
            ]
            for r,(key,label) in enumerate(fields):
                self.add_auth_entry(r,key,label,secret=("token" in key))

            ttk.Label(
                self.auth_frame,
                text="Scopes automatisch: chat:read, chat:edit, user:read:chat.",
                style="Sub.TLabel"
            ).grid(row=3,column=0,columnspan=2,sticky="w",pady=(4,8))
            ttk.Button(
                self.auth_frame,text="🌐 Twitch Token Generator öffnen",
                command=self.open_twitch_token_generator
            ).grid(row=4,column=0,columnspan=2,sticky="w")

        elif platform=="cng":
            # Legacy CNG authentication:
            # - creator_id is the numeric broadcaster ID (e.g. 30001)
            # - cng_session stores ONLY the session token, without
            #   "cng_session="; pasted cookie strings are normalized below.
            self.add_auth_entry(0, "cng_session", "CNG Session", secret=True)
            ttk.Label(
                self.auth_frame,
                text=(
                    "CNG Session: Bitte nur den Wert hinter cng_session= eintragen. "
                    "Beispiel: lj4F9vs6T_nRlED6MLRex4pK6CoycbSHcOGi9HOAKyM"
                ),
                style="Sub.TLabel",
                wraplength=720
            ).grid(row=1,column=0,columnspan=2,sticky="w",pady=(4,4))

            ttk.Label(
                self.auth_frame,
                text=(
                    "Falls du den kompletten Cookie kopierst "
                    "(cng_session=...), erkennt der Manager ihn ebenfalls "
                    "und speichert automatisch nur den Token."
                ),
                style="Sub.TLabel",
                wraplength=720
            ).grid(row=2,column=0,columnspan=2,sticky="w",pady=(0,8))

            ttk.Button(
                self.auth_frame,text="🌐 CNG Dashboard öffnen",
                command=self.open_cng_dashboard
            ).grid(row=3,column=0,columnspan=2,sticky="w",pady=(4,8))

            self.cng_help_label.configure(
                text=(
                    "CNG-Konto: Kanal/Benutzername = dein CNG-Kanal.\n"
                    "CNG Creator-ID = die numerische ID des Kanals (z. B. 30001).\n"
                    "CNG Session = nur der Token hinter „cng_session=“. "
                    "Der Manager akzeptiert auch einen komplett kopierten Cookie "
                    "und entfernt „cng_session=“ automatisch.\n"
                    "Gespeichert wird die Session lokal verschlüsselt."
                )
            )
            self.cng_help_label.pack(anchor="w", pady=(8,0))

    def add_auth_entry(self,row,key,label,secret=False):
        ttk.Label(self.auth_frame,text=label).grid(row=row,column=0,sticky="w",padx=2,pady=6)
        v=tk.StringVar()
        self.auth_vars[key]=v
        ttk.Entry(self.auth_frame,textvariable=v,style="TEntry",
                  show="•" if secret else "").grid(
                      row=row,column=1,sticky="ew",padx=2,pady=6)
        self.auth_frame.columnconfigure(1,weight=1)

    def open_twitch_token_generator(self):
        try:
            os.startfile("https://twitchtokengenerator.com/?scope=chat%3Aread+chat%3Aedit+user%3Aread%3Achat")
        except Exception as ex:
            messagebox.showerror(APP_NAME, str(ex))

    def open_cng_dashboard(self):
        try:
            os.startfile("https://cng-plattform.com/dashboard")
        except Exception as ex:
            messagebox.showerror(APP_NAME, str(ex))

    @staticmethod
    def extract_cng_session(raw):
        import re
        m=re.search(r"(?:^|[;\s])cng_session=([^;\s]+)",raw,re.I)
        if not m:
            m=re.search(r"cng_session\s*=\s*([^;\s]+)",raw,re.I)
        return urllib.parse.unquote(m.group(1).strip('"')) if m else ""

    # ---------------- Account operations ----------------
    def new_account(self):
        self.selected_id=None
        self.build_form()
        self.vars["platform"].set("twitch")
        self.vars["channel"].set("")
        if hasattr(self, "cng_creator_id_var"):
            self.cng_creator_id_var.set("")
        self.rebuild_auth()
        self.update_status("Neues Konto")

    def refresh_accounts(self):
        self.account_list.delete(0,"end")
        for a in self.accounts:
            self.account_list.insert(
                "end",
                f"{a.get('platform','?').upper()}  •  "
                f"{a.get('channel') or a.get('display_name') or 'Unbenannt'}"
            )
        self.update_platform_status()

    def on_select(self,event=None):
        sel=self.account_list.curselection()
        if not sel:
            return
        a=self.accounts[sel[0]]
        self.selected_id=a["id"]

        self.build_form()
        self.vars["platform"].set(a.get("platform","twitch"))
        self.vars["channel"].set(a.get("channel") or a.get("display_name", ""))
        self.rebuild_auth()

        if a.get("platform") == "cng" and hasattr(self, "cng_creator_id_var"):
            self.cng_creator_id_var.set(str(a.get("creator_id") or ""))

        for k,v in a.get("auth",{}).items():
            if k in self.auth_vars:
                try:
                    self.auth_vars[k].set(dpapi_decrypt(v))
                except Exception:
                    self.auth_vars[k].set(v)

        self.update_status("Konto geladen")

    def save_current(self):
        platform=self.vars["platform"].get()
        channel=self.vars["channel"].get().strip()

        if not channel:
            messagebox.showwarning(APP_NAME,"Bitte einen Accountnamen eintragen.")
            return

        if platform=="tiktok":
            try:
                ensure_runtime_dependencies(self.update_status)
            except Exception as ex:
                messagebox.showerror(APP_NAME, str(ex))
                return

        auth={}
        if platform=="twitch":
            for k,v in self.auth_vars.items():
                if v.get().strip():
                    auth[k]=v.get().strip()
            auth["scopes"]="chat:read,chat:edit,user:read:chat"
        elif platform=="cng":
            creator_raw = self.cng_creator_id_var.get().strip()
            try:
                creator_id = int(creator_raw)
                if creator_id <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                messagebox.showwarning(
                    APP_NAME,
                    "Bitte bei CNG eine gültige numerische Creator-ID eintragen, "
                    "z. B. 30001."
                )
                return

            session = self.auth_vars.get("cng_session")
            raw_session = session.get().strip() if session else ""
            if raw_session:
                normalized_session = self.extract_cng_session(raw_session)
                if not normalized_session:
                    # Also allow the plain token without any cookie prefix.
                    normalized_session = raw_session.strip().strip('"')
                auth["cng_session"] = normalized_session

        item={
            "id":self.selected_id or str(uuid.uuid4()),
            "platform":platform,
            # The manager has one account-name field. Keep the legacy
            # display_name key synchronized for bridge paths that still read it.
            "display_name":channel,
            "channel":channel,
            "auth":auth
        }
        if platform == "cng":
            item["creator_id"] = creator_id
        if self.selected_id:
            for i,a in enumerate(self.accounts):
                if a["id"]==self.selected_id:
                    self.accounts[i]=item
                    break
        else:
            self.accounts.append(item)
            self.selected_id=item["id"]

        save_accounts(self.accounts)
        self.refresh_accounts()

        for i,a in enumerate(self.accounts):
            if a["id"]==self.selected_id:
                self.account_list.selection_set(i)
                self.account_list.see(i)
                break

        # Apply account changes to the running bridge.
        if self.bridge_has_process():
            self.restart_bridge()
        else:
            self.start_bridge()

        self.update_status("Konto gespeichert")

    def delete_account(self):
        if not self.selected_id:
            return
        if not messagebox.askyesno(APP_NAME,"Konto wirklich löschen?"):
            return

        self.accounts=[a for a in self.accounts if a["id"]!=self.selected_id]
        save_accounts(self.accounts)
        self.selected_id=None
        self.refresh_accounts()
        self.new_account()

        if self.bridge_has_process():
            self.restart_bridge()

    # ---------------- Bridge controls ----------------
    def bridge_has_process(self):
        return bool(self.bridge.process and self.bridge.process.poll() is None)

    def start_bridge(self):
        self.bridge.start() if hasattr(self,"bridge") else None

    def stop_bridge(self):
        if hasattr(self,"bridge"):
            self.bridge.stop()

    def restart_bridge(self):
        if hasattr(self,"bridge"):
            self.bridge.restart()

    def update_bridge_buttons(self):
        running=self.bridge_has_process() if hasattr(self,"bridge") else False
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")
        self.restart_btn.configure(state="normal" if running else "disabled")

    # ---------------- Bridge live log ----------------
    def open_bridge_log(self):
        if self.bridge_log_window is not None:
            try:
                if self.bridge_log_window.winfo_exists():
                    self.bridge_log_window.deiconify()
                    self.bridge_log_window.lift()
                    self.bridge_log_window.focus_set()
                    return
            except tk.TclError:
                pass

        window = tk.Toplevel(self)
        window.title("Chat Bridge – Live-Log")
        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        width = min(1000, max(640, screen_width - 80))
        height = min(640, max(360, screen_height - 100))
        window.geometry(f"{width}x{height}")
        window.minsize(min(640, width), min(360, height))
        window.configure(bg=self.colors["bg"])
        self.bridge_log_window = window
        self.bridge_log_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.bridge_log_partial_line = ""

        toolbar = tk.Frame(window, bg=self.colors["panel"])
        toolbar.pack(fill="x", padx=12, pady=(12, 6))
        self.bridge_log_state = tk.StringVar(value="Lese Bridge-Log …")
        tk.Label(toolbar, textvariable=self.bridge_log_state,
                 bg=self.colors["panel"], fg=self.colors["text"],
                 font=("Segoe UI", 9, "bold")).pack(side="left", padx=10, pady=8)
        self.bridge_log_follow = tk.BooleanVar(master=window, value=True)
        ttk.Checkbutton(toolbar, text="Automatisch ans Ende", variable=self.bridge_log_follow).pack(
            side="right", padx=(6, 10)
        )
        ttk.Button(toolbar, text="Ansicht leeren", command=self.clear_bridge_log_view).pack(
            side="right", padx=6
        )

        body = tk.Frame(window, bg=self.colors["bg"])
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        log_text = tk.Text(
            body,
            wrap="none",
            bg="#0d0414",
            fg="#ded6e4",
            insertbackground="#ffffff",
            selectbackground="#3a1650",
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=8,
            font=("Consolas", 9),
            state="disabled",
        )
        log_text.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(body, orient="vertical", command=log_text.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(body, orient="horizontal", command=log_text.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        log_text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.bridge_log_text = log_text

        log_path = self.bridge.log_file if hasattr(self, "bridge") else ""
        try:
            size = os.path.getsize(log_path)
            self.bridge_log_offset = max(0, size - 300_000)
        except OSError:
            self.bridge_log_offset = 0

        window.protocol("WM_DELETE_WINDOW", self.close_bridge_log)
        self._poll_bridge_log()

    def close_bridge_log(self):
        window = self.bridge_log_window
        self.bridge_log_window = None
        self.bridge_log_text = None
        self.bridge_log_follow = None
        self.bridge_log_state = None
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass

    def clear_bridge_log_view(self):
        if self.bridge_log_text is None:
            return
        self.bridge_log_text.configure(state="normal")
        self.bridge_log_text.delete("1.0", "end")
        self.bridge_log_text.configure(state="disabled")
        try:
            self.bridge_log_offset = os.path.getsize(self.bridge.log_file)
        except OSError:
            self.bridge_log_offset = 0
        self.bridge_log_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.bridge_log_partial_line = ""

    def _append_bridge_log(self, text, flush=False):
        widget = self.bridge_log_text
        if widget is None:
            return
        combined = self.bridge_log_partial_line + (text or "")
        self.bridge_log_partial_line = ""
        lines = combined.splitlines(keepends=True)
        formatted_lines = []
        for line in lines:
            if line.endswith(("\n", "\r")):
                formatted_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {line}")
            else:
                self.bridge_log_partial_line += line
        if flush and self.bridge_log_partial_line:
            formatted_lines.append(
                f"[{datetime.now().strftime('%H:%M:%S')}] {self.bridge_log_partial_line}\n"
            )
            self.bridge_log_partial_line = ""
        if not formatted_lines:
            return
        widget.configure(state="normal")
        widget.insert("end", "".join(formatted_lines))
        line_count = int(widget.index("end-1c").split(".")[0])
        if line_count > 5000:
            widget.delete("1.0", f"{line_count - 5000}.0")
        if self.bridge_log_follow is not None and self.bridge_log_follow.get():
            widget.see("end")
        widget.configure(state="disabled")

    def _poll_bridge_log(self):
        window = self.bridge_log_window
        if window is None:
            return
        try:
            if not window.winfo_exists():
                return
            log_path = self.bridge.log_file
            size = os.path.getsize(log_path)
            if size < self.bridge_log_offset:
                # The log was truncated or replaced while the viewer was open.
                self.bridge_log_offset = 0
                self.bridge_log_decoder = codecs.getincrementaldecoder("utf-8")("replace")
                self.bridge_log_partial_line = ""
                if self.bridge_log_text is not None:
                    self.bridge_log_text.configure(state="normal")
                    self.bridge_log_text.delete("1.0", "end")
                    self.bridge_log_text.configure(state="disabled")

            if size > self.bridge_log_offset:
                with open(log_path, "rb") as log:
                    log.seek(self.bridge_log_offset)
                    chunk = log.read()
                    self.bridge_log_offset = log.tell()
                self._append_bridge_log(self.bridge_log_decoder.decode(chunk, final=False))

            if self.bridge_has_process():
                process = self.bridge.process
                status = f"Bridge läuft · PID {process.pid}"
            else:
                status = "Bridge ist beendet"
                self._append_bridge_log("", flush=True)
            if self.bridge_log_state is not None:
                self.bridge_log_state.set(f"{status}  ·  {log_path}")
        except FileNotFoundError:
            if self.bridge_log_state is not None:
                self.bridge_log_state.set("Noch keine Logdatei vorhanden – sie wird beim Start der Bridge angelegt.")
        except (OSError, tk.TclError) as ex:
            if self.bridge_log_state is not None:
                self.bridge_log_state.set(f"Log kann gerade nicht gelesen werden: {ex}")

        if self.bridge_log_window is window:
            try:
                window.after(500, self._poll_bridge_log)
            except tk.TclError:
                pass

    # ---------------- Tray ----------------
    def start_tray(self):
        try:
            ensure_runtime_dependencies(self.update_status)
            import pystray
            from PIL import Image, ImageDraw

            image=Image.new("RGBA",(64,64),(17,19,24,255))
            draw=ImageDraw.Draw(image)
            draw.ellipse((7,7,57,57),outline=(255,138,0,255),width=5)
            draw.text((22,17),"U",fill=(255,255,255,255))

            menu=pystray.Menu(
                pystray.MenuItem(
                    "CNG CHAT öffnen",
                    self.tray_show,
                    default=True
                ),
                pystray.MenuItem(
                    "Bridge starten",
                    lambda icon,item:self.after(0,self.start_bridge)
                ),
                pystray.MenuItem(
                    "Bridge beenden",
                    lambda icon,item:self.after(0,self.stop_bridge)
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "Bridge und Manager beenden",
                    lambda icon,item:self.after(0,self.quit_application)
                )
            )

            self.tray_icon=pystray.Icon("UnifiedChat",image,APP_NAME,menu)
            threading.Thread(target=self.tray_icon.run,daemon=True).start()
            self.update_status("Bereit – Bridge wird automatisch gestartet")
        except Exception as ex:
            self.update_status("Tray konnte nicht initialisiert werden")
            if self.app_settings.get("start_minimized", False):
                self.deiconify()
                self.lift()
            messagebox.showwarning(
                APP_NAME,
                "Tray konnte nicht initialisiert werden.\n\n"
                + str(ex)
            )

    def tray_show(self, icon=None, item=None):
        self.after(0,self.show_window)

    def show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def hide_window(self):
        self.withdraw()

    def on_window_minimize(self, event=None):
        if self.quitting or not self.app_settings.get("minimize_to_tray", True):
            return
        try:
            if self.state() == "iconic":
                self.after_idle(self.hide_window)
        except tk.TclError:
            pass

    def on_window_close(self):
        # X hides to tray; the explicit tray "Beenden" exits everything.
        if self.hide_on_close and not self.quitting:
            self.hide_window()
            return
        self.quit_application()

    def quit_application(self):
        if self.quitting:
            return
        self.quitting=True

        # Stop synchronously before closing the tray icon or destroying Tk.
        # BridgeService.stop() also serializes with startup, so a pending
        # background start cannot outlive the Manager.
        try:
            self.bridge.stop()
        except Exception:
            pass

        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass

        # Give the bridge process a moment to close before destroying Tk.
        self.after(250, self.destroy)


    def test_current(self):
        p=self.vars["platform"].get()
        if p=="twitch":
            cid=self.auth_vars.get("client_id",tk.StringVar()).get().strip()
            token=self.auth_vars.get("access_token",tk.StringVar()).get().strip()
            if not cid or not token:
                messagebox.showwarning(APP_NAME,"Client ID und Access Token fehlen.")
                return
            try:
                req=urllib.request.Request(
                    "https://id.twitch.tv/oauth2/validate",
                    headers={"Client-ID":cid,"Authorization":"OAuth "+token}
                )
                with urllib.request.urlopen(req,timeout=15) as r:
                    data=json.loads(r.read().decode())
                messagebox.showinfo(
                    APP_NAME,
                    "Twitch-Verbindung OK.\n\n"
                    f"Benutzer: {data.get('login','?')}\n"
                    f"Scopes: {', '.join(data.get('scopes',[]))}"
                )
            except Exception as ex:
                messagebox.showerror(APP_NAME,"Twitch-Verbindung fehlgeschlagen:\n\n"+str(ex))
        elif p=="kick":
            messagebox.showinfo(APP_NAME,"Kick benötigt für den aktuellen Chat-Adapter keine Zugangsdaten.")
        elif p=="tiktok":
            try:
                ensure_runtime_dependencies(self.update_status)
                messagebox.showinfo(APP_NAME,"TikTokLive ist installiert und bereit.")
            except Exception as ex:
                messagebox.showerror(APP_NAME,str(ex))
        elif p=="youtube":
            channel=self.vars.get("channel",tk.StringVar()).get().strip()
            if not channel:
                messagebox.showwarning(APP_NAME,"YouTube-Kanal / Handle fehlt.")
                return

            def youtube_test():
                try:
                    if channel.startswith(("http://","https://")):
                        base=channel.rstrip("/")
                    elif channel.startswith("@"):
                        base="https://www.youtube.com/"+channel
                    else:
                        base="https://www.youtube.com/@"+channel

                    headers={
                        "User-Agent":
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/131.0 Safari/537.36",
                        "Accept-Language":"de-DE,de;q=0.9,en;q=0.8"
                    }

                    live_ids=[]
                    for suffix in ("/live","/streams"):
                        req=urllib.request.Request(base+suffix,headers=headers)
                        with urllib.request.urlopen(req,timeout=20) as r:
                            html=r.read().decode("utf-8","replace")
                        live_ids.extend(youtube_extract_live_video_ids(html))

                    live_ids=list(dict.fromkeys(live_ids))

                    if live_ids:
                        result=(
                            "YouTube-Verbindung OK.\n\n"
                            f"Kanal: {channel}\n"
                            f"Gefundene Livestream-ID(s): {len(live_ids)}\n\n"
                            "Video-ID(s):\n"+ "\n".join(live_ids)+
                            "\n\nDie Bridge verwendet diese Video-ID(s) "
                            "für den Live-Chat."
                        )
                    else:
                        result=(
                            "YouTube-Verbindung OK.\n\n"
                            f"Kanal: {channel}\n\n"
                            "Aktuell wurden keine aktiven Livestreams erkannt."
                        )

                    self.after(0,lambda result=result:
                              messagebox.showinfo(APP_NAME,result))

                except urllib.error.HTTPError as ex:
                    error_text=(
                        "YouTube rate-limitiert die Anfrage (HTTP 429).\n\n"
                        "30-60 Sekunden warten und den Test einmal erneut ausführen."
                        if ex.code==429
                        else f"HTTP Error {ex.code}: {ex.reason}"
                    )
                    self.after(0,lambda error_text=error_text:
                              messagebox.showerror(
                                  APP_NAME,
                                  "YouTube-Verbindung fehlgeschlagen:\n\n"+error_text
                              ))
                except Exception as ex:
                    error_text=str(ex)
                    self.after(0,lambda error_text=error_text:
                              messagebox.showerror(
                                  APP_NAME,
                                  "YouTube-Verbindung fehlgeschlagen:\n\n"+error_text
                              ))

            threading.Thread(target=youtube_test,daemon=True).start()

        elif p=="cng":
            channel=self.vars.get("channel", tk.StringVar()).get().strip()
            messagebox.showinfo(APP_NAME,
                "CNG Kanal/Benutzername vorhanden." if channel else "Kein CNG Kanal/Benutzername eingetragen.")


def main():
    if not acquire_single_instance():
        # A second launch simply exits instead of opening another window.
        return

    # Bridge object is created before Tk mainloop schedules auto-start.
    app=Manager()
    app.bridge=BridgeService(app)

    # If the persistent bridge config was deleted, rebuild it from the
    # manager's actual saved account data. Do not create an empty template.
    try:
        persistent_config = os.path.join(BASE_DIR, BRIDGE_CONFIG_NAME)
        if not os.path.exists(persistent_config):
            app.bridge.write_persistent_config()
    except Exception as ex:
        print(
            "[MANAGER] Bridge-Konfiguration konnte beim Start "
            "nicht erzeugt werden:",
            ex
        )

    app.update_bridge_buttons()
    app.after(1000,app.start_bridge)
    if getattr(sys, "frozen", False):
        app.after(5000, app.start_update_check)
    app.mainloop()


if __name__=="__main__":
    main()
