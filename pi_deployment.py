"""Build and install a private Raspberry Pi copy of the current Bridge config."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import socket
import tarfile
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


REQUIRED_FILES = (
    "unified_chat_bridge.py",
    "requirements-linux.txt",
    "install_pi.sh",
    "alerts.html",
    "cng-multichat-livechat.html",
    "cng-multichat-overlay.html",
    "cng-multichat-overlay_horizontal.html",
    "Raspberry-Pi-Bereitstellung.md",
)

SECRET_FILES = {
    "accounts.json",
    "command_bot.json",
    "obs_settings.json",
    "unified_chat_config.json",
    "cng_creator_cookie.json",
}


def _json_copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def _write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _windows_local_ip(target_host: str, target_port: int = 22) -> str:
    """Ask Windows routing which local interface it would use for the Pi."""
    candidates = []
    try:
        for family, socktype, proto, _, address in socket.getaddrinfo(
            target_host, target_port, type=socket.SOCK_DGRAM
        ):
            if family in (socket.AF_INET, socket.AF_INET6):
                candidates.append((family, address))
    except OSError as exc:
        raise ValueError(f"Die Raspberry-Pi-Adresse '{target_host}' ist nicht auflösbar: {exc}") from exc

    candidates.sort(key=lambda item: 0 if item[0] == socket.AF_INET else 1)
    for family, address in candidates:
        if family != socket.AF_INET:
            continue
        probe = socket.socket(family, socket.SOCK_DGRAM)
        try:
            probe.connect(address)
            local_ip = probe.getsockname()[0]
            if local_ip and not local_ip.startswith("127."):
                return local_ip
        except OSError:
            continue
        finally:
            probe.close()
    raise ValueError(
        "Die lokale Windows-IPv4-Adresse für die Verbindung zum Pi konnte nicht bestimmt werden. "
        "Bitte den Pi über seine IPv4-Adresse angeben und die Netzwerkverbindung prüfen."
    )


def _is_loopback(host: str) -> bool:
    normalized = str(host or "").strip().strip("[]").lower()
    return normalized == "localhost" or normalized == "::1" or normalized.startswith("127.")


def _replace_url_loopback(url: str, lan_ip: str) -> str:
    try:
        parsed = urlsplit(str(url))
        if not parsed.hostname or not _is_loopback(parsed.hostname):
            return str(url)
        netloc = parsed.netloc
        if "@" in netloc:
            user_info, _ = netloc.rsplit("@", 1)
            user_info += "@"
        else:
            user_info = ""
        port = f":{parsed.port}" if parsed.port else ""
        return urlunsplit((parsed.scheme, f"{user_info}{lan_ip}{port}", parsed.path, parsed.query, parsed.fragment))
    except (TypeError, ValueError):
        return str(url)


def _rewrite_media_path(source_dir: Path, value: str, media_dir: Path) -> str:
    value = str(value or "").strip()
    if not value or value.lower().startswith(("http://", "https://", "data:", "blob:")):
        return value

    raw_path = value
    if value.lower().startswith("file:"):
        parsed = urlsplit(value)
        raw_path = parsed.path
        if parsed.netloc:
            raw_path = f"//{parsed.netloc}{raw_path}"
        elif re.match(r"^/[A-Za-z]:[/\\]", raw_path):
            raw_path = raw_path[1:]
        from urllib.request import url2pathname
        raw_path = url2pathname(raw_path)

    candidate = Path(os.path.expandvars(os.path.expanduser(raw_path)))
    if not candidate.is_absolute():
        candidate = source_dir / candidate
    candidate = candidate.resolve()
    if not candidate.is_file():
        raise FileNotFoundError(f"Alert-Datei nicht gefunden: {value}")

    digest = hashlib.sha256(str(candidate).encode("utf-8", "replace")).hexdigest()[:12]
    suffix = candidate.suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        suffix = ".bin"
    name = f"{digest}-{candidate.name}"
    # Avoid path traversal through an original filename.
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    destination = media_dir / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate, destination)
    return f"media/{name}"


def _rewrite_overlay_urls(bundle_root: Path):
    origin_expr = (
        '(window.location.protocol !== "file:" && window.location.origin !== "null") '
        '? window.location.origin : "http://127.0.0.1:8765"'
    )
    alerts_path = bundle_root / "alerts.html"
    alerts = alerts_path.read_text(encoding="utf-8")
    alerts = alerts.replace(
        'const BRIDGE_BASE_URL = "http://127.0.0.1:8765";',
        f"const BRIDGE_BASE_URL = {origin_expr};",
    )
    alerts_path.write_text(alerts, encoding="utf-8")

    livechat_path = bundle_root / "cng-multichat-livechat.html"
    livechat = livechat_path.read_text(encoding="utf-8")
    livechat = livechat.replace(
        'const CONFIG={maxMessages:14,bridge:"http://127.0.0.1:8765/events"};',
        f'const BRIDGE_BASE_URL={origin_expr};\nconst CONFIG={{maxMessages:14,bridge:BRIDGE_BASE_URL+"/events"}};',
    )
    livechat = livechat.replace(
        'new EventSource("http://127.0.0.1:8765/events")',
        'new EventSource(BRIDGE_BASE_URL+"/events")',
    )
    livechat_path.write_text(livechat, encoding="utf-8")

    horizontal_path = bundle_root / "cng-multichat-overlay_horizontal.html"
    horizontal = horizontal_path.read_text(encoding="utf-8")
    horizontal = horizontal.replace(
        'const CONFIG={\n  maxMessages:14,\n  bridge:"http://127.0.0.1:8765/events",',
        f'const BRIDGE_BASE_URL={origin_expr};\nconst CONFIG={{\n  maxMessages:14,\n  bridge:BRIDGE_BASE_URL+"/events",',
    )
    horizontal = horizontal.replace(
        '"http://127.0.0.1:8765/config"',
        'BRIDGE_BASE_URL+"/config"',
    )
    horizontal_path.write_text(horizontal, encoding="utf-8")

    checks = {
        "alerts.html": ("127.0.0.1:8765",),
        "cng-multichat-livechat.html": ("127.0.0.1:8765",),
        "cng-multichat-overlay_horizontal.html": ("127.0.0.1:8765",),
    }
    for filename, obsolete in checks.items():
        content = (bundle_root / filename).read_text(encoding="utf-8")
        # The local fallback is intentionally retained for file:// use.
        if filename != "alerts.html" and "BRIDGE_BASE_URL" not in content:
            raise RuntimeError(f"Bridge-Adresse wurde in {filename} nicht auf Pi-Betrieb vorbereitet.")
        if obsolete and not content:
            raise RuntimeError(f"Overlay-Datei ist leer: {filename}")


def create_bundle(
    base_dir,
    *,
    target_host,
    accounts,
    bridge_config,
    command_bot_config,
    alert_settings,
    display_settings,
    obs_settings,
    destination=None,
    source_windows_ip=None,
):
    """Create a tar.gz bundle from a snapshot of Manager's in-memory settings.

    Returns ``(archive_path, source_windows_ip, cleanup_callback)``. The caller
    must call the cleanup callback after the SSH upload completes.
    """
    base_dir = Path(base_dir).resolve()
    local_ip = source_windows_ip or _windows_local_ip(target_host)
    scratch = Path(tempfile.mkdtemp(prefix="cng-multichat-pi-"))
    bundle_root = scratch / "bundle"
    bundle_root.mkdir()
    archive_path = Path(destination).resolve() if destination else scratch / "cng-multichat-pi.tar.gz"

    try:
        for filename in REQUIRED_FILES:
            source = base_dir / filename
            if not source.is_file():
                raise FileNotFoundError(f"Für den Pi-Export fehlt die Datei: {source}")
            shutil.copy2(source, bundle_root / filename)

        accounts_out = {"version": 3, "accounts": _json_copy(accounts or [])}
        bridge_out = _json_copy(bridge_config or {})
        command_out = _json_copy(command_bot_config or {})
        alert_out = _json_copy(alert_settings or {})
        display_out = _json_copy(display_settings or {})
        obs_out = _json_copy(obs_settings or {})

        media_dir = bundle_root / "media"
        for event_name, event in alert_out.items():
            if not isinstance(event, dict):
                continue
            for field in ("gif", "sound"):
                event[field] = _rewrite_media_path(base_dir, event.get(field, ""), media_dir)
        if isinstance(bridge_out.get("alerts"), dict):
            for event_name, event in bridge_out["alerts"].items():
                if not isinstance(event, dict):
                    continue
                configured = alert_out.get(event_name, {})
                for field in ("gif", "sound"):
                    if field in configured:
                        event[field] = configured[field]
                    else:
                        event[field] = _rewrite_media_path(base_dir, event.get(field, ""), media_dir)

        lumiastream = command_out.get("lumiastream")
        if isinstance(lumiastream, dict):
            lumiastream["api_url"] = _replace_url_loopback(lumiastream.get("api_url", ""), local_ip)
        streamerbot = command_out.get("streamerbot")
        if isinstance(streamerbot, dict) and _is_loopback(streamerbot.get("host", "")):
            streamerbot["host"] = local_ip
        if _is_loopback(obs_out.get("host", "")):
            obs_out["host"] = local_ip
        if isinstance(bridge_out.get("obs"), dict) and _is_loopback(bridge_out["obs"].get("host", "")):
            bridge_out["obs"]["host"] = local_ip

        _write_json(bundle_root / "accounts.json", accounts_out)
        _write_json(bundle_root / "unified_chat_config.json", bridge_out)
        _write_json(bundle_root / "command_bot.json", command_out)
        _write_json(bundle_root / "alert_settings.json", alert_out)
        _write_json(bundle_root / "display_settings.json", display_out)
        _write_json(bundle_root / "obs_settings.json", obs_out)

        for optional in ("cng_creator_cookie.json", "cng_followers.json"):
            source = base_dir / optional
            if source.is_file():
                shutil.copy2(source, bundle_root / optional)

        _rewrite_overlay_urls(bundle_root)
        (bundle_root / "PI-DEPLOYMENT.txt").write_text(
            "CNG MultiChat Bridge – Raspberry Pi Bereitstellung\n"
            "Ziel: Raspberry Pi OS 64-Bit.\n"
            "Die Bridge wird als systemd-Dienst installiert und beim Boot gestartet.\n"
            "Overlay: http://<PI-IP>:8765/  |  Livechat: /livechat  |  Alerts: /alerts\n"
            "Status/Stats: /stats  |  Health: /health\n"
            "Logs: sudo journalctl -u cng-multichat-bridge.service -f\n"
            "Die OBS/Lumia/Streamer.bot-Einträge, die auf localhost zeigten, wurden für\n"
            f"diesen Export auf die Windows-LAN-Adresse {local_ip} gesetzt.\n"
            "Die jeweiligen Dienste müssen LAN-Verbindungen zulassen; das wird nicht\n"
            "automatisch auf dem Windows-PC umgestellt.\n",
            encoding="utf-8",
        )

        if destination:
            archive_path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(bundle_root, arcname="cng-multichat")

        def cleanup():
            shutil.rmtree(scratch, ignore_errors=True)
            if destination is None:
                try:
                    archive_path.unlink(missing_ok=True)
                except OSError:
                    pass

        return str(archive_path), local_ip, cleanup
    except Exception:
        shutil.rmtree(scratch, ignore_errors=True)
        raise


def _known_hosts_path():
    root = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "CNG MultiChat Manager"
    root.mkdir(parents=True, exist_ok=True)
    return root / "ssh_known_hosts"


def _host_key_name(host: str, port: int) -> str:
    return host if int(port) == 22 else f"[{host}]:{int(port)}"


def fetch_host_key(host: str, port: int, timeout=8):
    import paramiko

    transport = paramiko.Transport((host, int(port)))
    try:
        transport.banner_timeout = timeout
        transport.auth_timeout = timeout
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
        fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode("ascii").rstrip("=")
        return key, f"SHA256:{fingerprint}"
    finally:
        transport.close()


def trust_host_key(host: str, port: int, key):
    import paramiko

    path = _known_hosts_path()
    host_key_name = _host_key_name(host, port)
    keys = paramiko.HostKeys()
    if path.exists():
        keys.load(str(path))
    existing = keys.lookup(host_key_name)
    if existing:
        same_key = existing.get(key.get_name())
        if same_key is None or same_key.asbytes() != key.asbytes():
            raise paramiko.SSHException(
                "Der SSH-Hostschlüssel stimmt nicht mit dem bereits gespeicherten Schlüssel überein. "
                "Möglicher Pi-Neuaufbau oder geändertes Ziel; Prüfung abgebrochen."
            )
    else:
        keys.add(host_key_name, key.get_name(), key)
        keys.save(str(path))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return str(path)


def host_key_is_trusted(host: str, port: int, key) -> bool:
    import paramiko

    path = _known_hosts_path()
    if not path.exists():
        return False
    keys = paramiko.HostKeys()
    keys.load(str(path))
    existing = keys.lookup(_host_key_name(host, port))
    if not existing:
        return False
    saved = existing.get(key.get_name())
    if saved is None or saved.asbytes() != key.asbytes():
        raise paramiko.SSHException(
            "Der SSH-Hostschlüssel stimmt nicht mit dem bereits gespeicherten Schlüssel überein. "
            "Möglicher Pi-Neuaufbau oder geändertes Ziel; Prüfung abgebrochen."
        )
    return True


def deploy_bundle(host, port, username, ssh_password, sudo_password, archive_path, on_output=None):
    """Upload over SFTP and run the installer via sudo without saving passwords."""
    import paramiko
    import shlex

    host_key_path = _known_hosts_path()
    client = paramiko.SSHClient()
    if host_key_path.exists():
        client.load_host_keys(str(host_key_path))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    stage = f"/tmp/cng-multichat-deploy-{uuid.uuid4().hex}"
    remote_archive = f"{stage}/package.tar.gz"
    connected = False
    try:
        client.connect(
            hostname=host,
            port=int(port),
            username=username,
            password=ssh_password or None,
            look_for_keys=False,
            allow_agent=False,
            timeout=12,
            banner_timeout=12,
            auth_timeout=15,
        )
        connected = True
        sftp = client.open_sftp()
        try:
            sftp.mkdir(stage, mode=0o700)
            sftp.put(str(archive_path), remote_archive)
            sftp.chmod(remote_archive, 0o600)
        finally:
            sftp.close()

        inner = (
            "set -Eeuo pipefail; "
            f"mkdir -p {shlex.quote(stage + '/unpacked')}; "
            f"tar -xzf {shlex.quote(remote_archive)} -C {shlex.quote(stage + '/unpacked')}; "
            f"bash {shlex.quote(stage + '/unpacked/cng-multichat/install_pi.sh')} {shlex.quote(username)}; "
            f"rm -rf {shlex.quote(stage)}"
        )
        command = f"sudo -S -p '' bash -c {shlex.quote(inner)}"
        stdin, stdout, stderr = client.exec_command(command, get_pty=False, timeout=1800)
        stdin.write((sudo_password if sudo_password is not None else ssh_password) + "\n")
        stdin.flush()
        stdin.channel.shutdown_write()
        channel = stdout.channel
        out_buffer = ""
        err_buffer = ""

        def emit_lines(buffer):
            lines = buffer.split("\n")
            for line in lines[:-1]:
                if line.strip() and on_output:
                    on_output(line.rstrip("\r"))
            return lines[-1]

        while True:
            while channel.recv_ready():
                out_buffer += channel.recv(4096).decode("utf-8", "replace")
            while channel.recv_stderr_ready():
                err_buffer += channel.recv_stderr(4096).decode("utf-8", "replace")
            if on_output:
                if "\n" in out_buffer:
                    out_buffer = emit_lines(out_buffer)
                if "\n" in err_buffer:
                    err_buffer = emit_lines(err_buffer)
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            time.sleep(0.1)
        if out_buffer.strip() and on_output:
            on_output(out_buffer.strip())
        if err_buffer.strip() and on_output:
            on_output(err_buffer.strip())
        status = channel.recv_exit_status()
        if status != 0:
            raise RuntimeError(f"Der Pi-Installer wurde mit Status {status} beendet.")
        if on_output:
            on_output("SSH-Installation und lokaler Bridge-Healthcheck waren erfolgreich.")
    except Exception:
        if connected:
            try:
                client.exec_command(f"rm -rf {shlex.quote(stage)}", timeout=15)
            except Exception:
                pass
        raise
    finally:
        client.close()

