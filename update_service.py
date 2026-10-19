"""Signed GitHub-release updates for the packaged Windows Manager."""

import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import subprocess
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile

GITHUB_OWNER = "Timur18o4"
GITHUB_REPO = "CNG-MultiChat"
RELEASE_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
UPDATE_PUBLIC_KEY_B64 = "1JDJZJbXOmc2LrOUvYRvuuAjOYg1GABwn/ECgiuTSc0="
UPDATE_FORMAT = "cng-multichat-update-v1"
MANAGER_EXE = "CNG_MultiChat_Manager.exe"
MAX_ARCHIVE_BYTES = 1_000_000_000
MAX_UNPACKED_BYTES = 2_000_000_000
MAX_ARCHIVE_FILES = 30_000
PRESERVED_DATA_FILES = {
    "accounts.json",
    "unified_chat_config.json",
    "manager_settings.json",
    "display_settings.json",
    "alert_settings.json",
    "obs_settings.json",
    "command_bot.json",
}


class UpdateError(RuntimeError):
    pass


def read_app_version(directory):
    path = os.path.join(directory, "VERSION")
    try:
        version = Path(path).read_text(encoding="ascii").strip()
    except OSError as ex:
        raise UpdateError(f"Versionsdatei fehlt: {path}") from ex
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise UpdateError("Die Versionsdatei enthält keine gültige stabile Version.")
    return version


def _version_tuple(version):
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(version)):
        raise UpdateError(f"Ungültige Versionsnummer: {version}")
    return tuple(int(part) for part in str(version).split("."))


def _safe_https_url(url, allowed_initial_hosts=None):
    parsed = urllib.parse.urlparse(str(url or ""))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise UpdateError("GitHub lieferte eine ungültige Download-Adresse.")
    if allowed_initial_hosts and parsed.hostname.lower() not in allowed_initial_hosts:
        raise UpdateError("Der Release-Download verweist nicht auf GitHub.")
    return parsed


def _request(url, *, accept="application/vnd.github+json", timeout=12):
    _safe_https_url(url, {"api.github.com", "github.com"})
    request = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": "CNG-MultiChat-Manager-Updater",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    return urllib.request.urlopen(request, timeout=timeout)


def _check_final_download_host(response):
    parsed = _safe_https_url(response.geturl())
    host = parsed.hostname.lower()
    if host not in {"github.com", "api.github.com"} and not host.endswith(".githubusercontent.com"):
        raise UpdateError("GitHub leitete den Download auf einen unerwarteten Host um.")


def _release_asset(release, name):
    matches = [asset for asset in release.get("assets", []) if asset.get("name") == name]
    if len(matches) != 1:
        raise UpdateError(f"Das Release enthält nicht genau ein erforderliches Asset: {name}")
    asset = matches[0]
    if asset.get("state") != "uploaded":
        raise UpdateError(f"Das Release-Asset ist nicht vollständig hochgeladen: {name}")
    digest = str(asset.get("digest") or "").lower()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise UpdateError(f"GitHub stellt keine gültige SHA-256-Prüfsumme für {name} bereit.")
    _safe_https_url(asset.get("browser_download_url"), {"github.com"})
    return {
        "url": asset["browser_download_url"],
        "sha256": digest.split(":", 1)[1],
        "size": int(asset.get("size") or 0),
    }


def _signed_payload(asset_name, version, sha256):
    value = {"asset": asset_name, "sha256": sha256.lower(), "version": version}
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


def _verify_signature_manifest(raw_manifest, expected_asset, expected_version, github_sha256):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if len(raw_manifest) > 64_000:
        raise UpdateError("Die Signaturdatei ist unerwartet groß.")
    try:
        manifest = json.loads(raw_manifest.decode("utf-8"))
        version = str(manifest["version"])
        asset = str(manifest["asset"])
        sha256 = str(manifest["sha256"]).lower()
        signature = base64.b64decode(manifest["signature"], validate=True)
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as ex:
        raise UpdateError("Die Release-Signaturdatei ist ungültig.") from ex

    if manifest.get("format") != UPDATE_FORMAT:
        raise UpdateError("Das Signaturformat wird nicht unterstützt.")
    if version != expected_version or asset != expected_asset:
        raise UpdateError("Version oder Dateiname in der Signatur passen nicht zum Release.")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256) or sha256 != github_sha256:
        raise UpdateError("Die signierte Prüfsumme passt nicht zur GitHub-Prüfsumme.")

    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(UPDATE_PUBLIC_KEY_B64, validate=True))
        public_key.verify(signature, _signed_payload(asset, version, sha256))
    except (InvalidSignature, ValueError) as ex:
        raise UpdateError("Die Herausgebersignatur ist ungültig.") from ex
    return {"version": version, "asset": asset, "sha256": sha256}


def latest_update(current_version):
    """Return signed release metadata if GitHub has a newer stable version."""
    try:
        with _request(RELEASE_API) as response:
            release = json.loads(response.read(1_000_001).decode("utf-8"))
    except Exception as ex:
        raise UpdateError(f"GitHub-Releases konnten nicht geprüft werden: {ex}") from ex

    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        return None
    tag = str(release.get("tag_name", ""))
    match = re.fullmatch(r"v?(\d+\.\d+\.\d+)", tag)
    if not match:
        return None
    version = match.group(1)
    if _version_tuple(version) <= _version_tuple(current_version):
        return None

    asset_name = f"CNG_MultiChat_Windows_v{version}.zip"
    signature_name = asset_name + ".sig.json"
    archive_asset = _release_asset(release, asset_name)
    signature_asset = _release_asset(release, signature_name)
    if archive_asset["size"] <= 0 or archive_asset["size"] > MAX_ARCHIVE_BYTES:
        raise UpdateError("Die Größe des Release-Pakets liegt außerhalb des erlaubten Bereichs.")

    try:
        with _request(signature_asset["url"], accept="application/octet-stream") as response:
            _check_final_download_host(response)
            manifest_bytes = response.read(64_001)
    except Exception as ex:
        raise UpdateError(f"Die Herausgebersignatur konnte nicht geladen werden: {ex}") from ex
    signature = _verify_signature_manifest(
        manifest_bytes, asset_name, version, archive_asset["sha256"]
    )
    return {
        "version": version,
        "asset_name": asset_name,
        "archive_url": archive_asset["url"],
        "archive_sha256": archive_asset["sha256"],
        "archive_size": archive_asset["size"],
        "signature": signature,
    }


def _download_archive(update, destination):
    request = urllib.request.Request(
        update["archive_url"],
        headers={"Accept": "application/octet-stream", "User-Agent": "CNG-MultiChat-Manager-Updater"},
    )
    digest = hashlib.sha256()
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            _check_final_download_host(response)
            content_length = int(response.headers.get("Content-Length") or 0)
            if content_length and content_length != update["archive_size"]:
                raise UpdateError("Die geladene Paketgröße stimmt nicht mit GitHub überein.")
            with open(destination, "wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise UpdateError("Das Update-Paket überschreitet die erlaubte Größe.")
                    digest.update(chunk)
                    output.write(chunk)
    except Exception as ex:
        try:
            os.remove(destination)
        except OSError:
            pass
        if isinstance(ex, UpdateError):
            raise
        raise UpdateError(f"Das Update-Paket konnte nicht geladen werden: {ex}") from ex

    actual_sha256 = digest.hexdigest()
    if total != update["archive_size"] or actual_sha256 != update["archive_sha256"]:
        raise UpdateError("Das heruntergeladene Paket stimmt nicht mit der signierten Prüfsumme überein.")


def _safe_extract(archive_path, stage_dir):
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_FILES:
                raise UpdateError("Das Update enthält zu viele Dateien.")
            unpacked_size = sum(max(0, item.file_size) for item in entries)
            if unpacked_size > MAX_UNPACKED_BYTES:
                raise UpdateError("Das entpackte Update überschreitet die erlaubte Größe.")

            for item in entries:
                raw_name = item.filename.replace("\\", "/")
                relative = PurePosixPath(raw_name)
                if (
                    not raw_name
                    or relative.is_absolute()
                    or PureWindowsPath(raw_name).is_absolute()
                    or PureWindowsPath(raw_name).drive
                    or any(part in {"", ".", ".."} for part in relative.parts)
                    or any(":" in part for part in relative.parts)
                ):
                    raise UpdateError("Das Update enthält einen unsicheren Dateipfad.")
                mode = (item.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise UpdateError("Das Update enthält einen nicht erlaubten symbolischen Link.")
                destination = os.path.abspath(os.path.join(stage_dir, *relative.parts))
                if os.path.commonpath((os.path.abspath(stage_dir), destination)) != os.path.abspath(stage_dir):
                    raise UpdateError("Das Update enthält einen Dateipfad außerhalb des Paketordners.")
                if item.is_dir():
                    os.makedirs(destination, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                with archive.open(item, "r") as source, open(destination, "wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
    except zipfile.BadZipFile as ex:
        raise UpdateError("Das Update-Paket ist kein gültiges ZIP-Archiv.") from ex


def _read_package_files(old_directory):
    path = os.path.join(old_directory, "package_files.json")
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        paths = data.get("files")
        if data.get("format") != 1 or not isinstance(paths, list):
            return None
        return {str(item).replace("\\", "/") for item in paths if isinstance(item, str)}
    except (OSError, ValueError, TypeError):
        return None


def _copy_file(source, destination, *, overwrite=False):
    if os.path.islink(source) or not os.path.isfile(source):
        return
    if os.path.exists(destination) and not overwrite:
        return
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    shutil.copy2(source, destination)


def _referenced_local_files(old_directory):
    candidates = set()
    for filename in ("alert_settings.json", "unified_chat_config.json"):
        config_path = os.path.join(old_directory, filename)
        try:
            payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            continue

        stack = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                for key, child in value.items():
                    if str(key).lower() in {"gif", "sound"} and isinstance(child, str):
                        stack.append(("media", child))
                    else:
                        stack.append(child)
            elif isinstance(value, list):
                stack.extend(value)
            elif isinstance(value, tuple) and len(value) == 2 and value[0] == "media":
                media_path = value[1]
                if not media_path.strip():
                    continue
                raw = os.path.expandvars(media_path.strip())
                extension = os.path.splitext(raw)[1].lower()
                if extension not in {".gif", ".png", ".jpg", ".jpeg", ".webp", ".mp3", ".wav", ".ogg"}:
                    continue
                if os.path.isabs(raw):
                    candidate = os.path.abspath(raw)
                else:
                    candidate = os.path.abspath(os.path.join(old_directory, raw))
                try:
                    if os.path.commonpath((old_directory, candidate)) == old_directory:
                        candidates.add(candidate)
                except ValueError:
                    pass
    return candidates


def _preserve_user_data(old_directory, stage_dir):
    old_directory = os.path.abspath(old_directory)
    stage_dir = os.path.abspath(stage_dir)
    for name in PRESERVED_DATA_FILES:
        _copy_file(os.path.join(old_directory, name), os.path.join(stage_dir, name), overwrite=True)
    for name in os.listdir(old_directory):
        if name.lower().endswith(".log"):
            _copy_file(os.path.join(old_directory, name), os.path.join(stage_dir, name), overwrite=True)

    for source in _referenced_local_files(old_directory):
        relative = os.path.relpath(source, old_directory)
        _copy_file(source, os.path.join(stage_dir, relative), overwrite=True)

    managed = _read_package_files(old_directory)
    if managed is None:
        return
    for root, dirs, files in os.walk(old_directory):
        dirs[:] = [name for name in dirs if os.path.abspath(os.path.join(root, name)) != stage_dir]
        for filename in files:
            source = os.path.join(root, filename)
            relative = os.path.relpath(source, old_directory).replace(os.sep, "/")
            if relative == "package_files.json" or relative in managed:
                continue
            destination = os.path.join(stage_dir, *PurePosixPath(relative).parts)
            _copy_file(source, destination)


def download_and_stage(update, install_directory):
    """Download, verify, safely unpack, and preserve the existing user data."""
    install_directory = os.path.abspath(install_directory)
    temporary_directory = tempfile.mkdtemp(prefix="cng-multichat-download-")
    stage_directory = os.path.join(install_directory, f".cng-update-stage-{uuid.uuid4().hex}")
    archive_path = os.path.join(temporary_directory, update["asset_name"])
    try:
        _download_archive(update, archive_path)
        os.makedirs(stage_directory, exist_ok=False)
        _safe_extract(archive_path, stage_directory)
        if not os.path.isfile(os.path.join(stage_directory, MANAGER_EXE)):
            raise UpdateError("Im Paket fehlt CNG_MultiChat_Manager.exe.")
        if read_app_version(stage_directory) != update["version"]:
            raise UpdateError("Die Paketversion stimmt nicht mit dem signierten Release überein.")
        _preserve_user_data(install_directory, stage_directory)
        return stage_directory
    except Exception:
        shutil.rmtree(stage_directory, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(temporary_directory, ignore_errors=True)


def start_external_updater(stage_directory, install_directory, manager_pid, bridge_pid=None):
    """Start a detached PowerShell folder swap, then let this process exit."""
    install_directory = os.path.abspath(install_directory)
    stage_directory = os.path.abspath(stage_directory)
    payload = json.dumps(
        {
            "install": install_directory,
            "stage": stage_directory,
            "managerPid": int(manager_pid),
            "bridgePid": int(bridge_pid or 0),
            "managerExe": os.path.join(install_directory, MANAGER_EXE),
            "bridgeExe": os.path.join(install_directory, "UnifiedChatBridge.exe"),
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    payload_b64 = base64.b64encode(payload.encode("utf-16le")).decode("ascii")
    script = r"""
$ErrorActionPreference = 'Stop'
$cfg = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('__PAYLOAD__')) | ConvertFrom-Json
$install = [IO.Path]::GetFullPath([string]$cfg.install)
$stage = [IO.Path]::GetFullPath([string]$cfg.stage)
$managerExe = [IO.Path]::GetFullPath([string]$cfg.managerExe)
$logPath = Join-Path $env:TEMP 'CNGMultiChatUpdate.log'
$backup = "$install.previous-$([guid]::NewGuid().ToString('N'))"
$stageSibling = "$install.staged-$([guid]::NewGuid().ToString('N'))"
function Write-UpdateLog([string]$message) { Add-Content -LiteralPath $logPath -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $message" -Encoding UTF8 }
try {
    Write-UpdateLog 'Warte auf das Schließen des Managers.'
    $processesToWaitFor = @(
        @{ Id = [int]$cfg.managerPid; Path = $managerExe; Label = 'Manager' },
        @{ Id = [int]$cfg.bridgePid; Path = [IO.Path]::GetFullPath([string]$cfg.bridgeExe); Label = 'Bridge' }
    )
    foreach ($item in $processesToWaitFor) {
        if ($item.Id -le 0) { continue }
        $deadline = [DateTime]::UtcNow.AddSeconds(120)
        while ([DateTime]::UtcNow -lt $deadline) {
            $p = Get-Process -Id $item.Id -ErrorAction SilentlyContinue
            if (-not $p) { break }
            $sameProcess = $false
            try { $sameProcess = ($p.Path -eq $item.Path) } catch { $sameProcess = $true }
            if (-not $sameProcess) { break }
            Start-Sleep -Milliseconds 500
        }
        $p = Get-Process -Id $item.Id -ErrorAction SilentlyContinue
        if ($p) {
            $sameProcess = $false
            try { $sameProcess = ($p.Path -eq $item.Path) } catch { $sameProcess = $true }
            if ($sameProcess) { throw "$($item.Label) wurde nicht rechtzeitig beendet." }
        }
    }
    if (-not (Test-Path -LiteralPath $stage -PathType Container)) { throw 'Update-Stagingordner fehlt.' }
    Move-Item -LiteralPath $stage -Destination $stageSibling
    Move-Item -LiteralPath $install -Destination $backup
    try {
        Move-Item -LiteralPath $stageSibling -Destination $install
        Write-UpdateLog 'Neue Version liegt im Programmordner; starte Manager.'
        $newProcess = Start-Process -FilePath $managerExe -WorkingDirectory $install -PassThru
        Start-Sleep -Seconds 10
        $newProcess.Refresh()
        if ($newProcess.HasExited) { throw 'Der aktualisierte Manager wurde beim Start beendet.' }
        Remove-Item -LiteralPath $backup -Recurse -Force
        Write-UpdateLog 'Update erfolgreich abgeschlossen.'
    } catch {
        Write-UpdateLog ("Update fehlgeschlagen; stelle Vorversion wieder her: " + $_.Exception.Message)
        if (Test-Path -LiteralPath $install) {
            $failed = "$install.failed-$([guid]::NewGuid().ToString('N'))"
            Move-Item -LiteralPath $install -Destination $failed
            Remove-Item -LiteralPath $failed -Recurse -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $backup) { Move-Item -LiteralPath $backup -Destination $install }
        if (Test-Path -LiteralPath $managerExe) { Start-Process -FilePath $managerExe -WorkingDirectory $install }
        throw
    }
} catch {
    Write-UpdateLog ("Update nicht ausgeführt: " + $_.Exception.Message)
    if (Test-Path -LiteralPath $stageSibling) { Move-Item -LiteralPath $stageSibling -Destination $stage -ErrorAction SilentlyContinue }
    exit 1
}
""".replace("__PAYLOAD__", payload_b64)
    encoded_command = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded_command],
            cwd=os.path.dirname(install_directory),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except OSError as ex:
        raise UpdateError(f"Der externe Updater konnte nicht gestartet werden: {ex}") from ex
    return process
