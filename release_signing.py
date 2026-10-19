"""Keep the release signing key in Windows DPAPI and sign a versioned ZIP."""

import argparse
import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from update_service import UPDATE_FORMAT, UPDATE_PUBLIC_KEY_B64, _signed_payload


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob(data):
    buffer = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _dpapi_transform(data, *, protect):
    if os.name != "nt":
        raise RuntimeError("Der Release-Schlüssel kann nur unter Windows mit DPAPI verwendet werden.")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    input_blob, input_buffer = _blob(data)
    output_blob = DATA_BLOB()
    description = "CNG MultiChat Release Signing Key"
    if protect:
        function = crypt32.CryptProtectData
        function.argtypes = [
            ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(DATA_BLOB),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
        ]
        function.restype = wintypes.BOOL
        ok = function(ctypes.byref(input_blob), description, None, None, None, 0, ctypes.byref(output_blob))
    else:
        description_pointer = wintypes.LPWSTR()
        function = crypt32.CryptUnprotectData
        function.argtypes = [
            ctypes.POINTER(DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(DATA_BLOB),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
        ]
        function.restype = wintypes.BOOL
        ok = function(ctypes.byref(input_blob), ctypes.byref(description_pointer), None, None, None, 0,
                      ctypes.byref(output_blob))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))


def _key_path():
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        local_app_data = os.path.join(str(Path.home()), "AppData", "Local")
    return Path(local_app_data) / "CNG MultiChat" / "release-signing-key.dpapi"


def _save_encrypted_private_key(raw_key):
    path = _key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _dpapi_transform(raw_key, protect=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_bytes(encrypted)
    os.replace(temporary_path, path)
    return path


def _load_private_key():
    path = _key_path()
    if not path.is_file():
        raise RuntimeError(
            f"Der lokale Release-Schlüssel fehlt: {path}. "
            "Er muss aus dem Windows-Benutzerprofil des Release-Erstellers wiederhergestellt werden."
        )
    raw_key = _dpapi_transform(path.read_bytes(), protect=False)
    if len(raw_key) != 32:
        raise RuntimeError("Der gespeicherte Release-Schlüssel ist beschädigt.")
    return Ed25519PrivateKey.from_private_bytes(raw_key)


def _public_key_b64(private_key):
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def initialize_key():
    path = _key_path()
    if path.exists():
        private_key = _load_private_key()
    else:
        private_key = Ed25519PrivateKey.generate()
        raw_key = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        _save_encrypted_private_key(raw_key)
    print(f"Release-Schlüssel ist DPAPI-geschützt gespeichert: {path}")
    print(f"Öffentlicher Schlüssel für update_service.py: {_public_key_b64(private_key)}")


def verify_key():
    if UPDATE_PUBLIC_KEY_B64 == "TO_BE_CONFIGURED":
        raise RuntimeError("UPDATE_PUBLIC_KEY_B64 wurde in update_service.py noch nicht eingerichtet.")
    private_key = _load_private_key()
    if _public_key_b64(private_key) != UPDATE_PUBLIC_KEY_B64:
        raise RuntimeError("Der lokale DPAPI-Schlüssel passt nicht zum öffentlichen Schlüssel im Manager.")
    print("Release-Signaturschlüssel ist verfügbar und passt zum Manager.")


def write_inventory(directory):
    root = Path(directory).resolve()
    if not root.is_dir():
        raise RuntimeError(f"Paketordner wurde nicht gefunden: {root}")
    files = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).as_posix() != "package_files.json"
    )
    payload = {"format": 1, "files": files}
    (root / "package_files.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Paket-Dateiliste erstellt ({len(files)} Dateien).")


def sign_archive(archive_path, version):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError("Die Version muss dem Format X.Y.Z entsprechen.")
    archive = Path(archive_path).resolve()
    expected_name = f"CNG_MultiChat_Windows_v{version}.zip"
    if archive.name != expected_name:
        raise RuntimeError(f"Der ZIP-Dateiname muss exakt {expected_name} lauten.")
    if UPDATE_PUBLIC_KEY_B64 == "TO_BE_CONFIGURED":
        raise RuntimeError("UPDATE_PUBLIC_KEY_B64 wurde in update_service.py noch nicht eingerichtet.")

    private_key = _load_private_key()
    public_key_b64 = _public_key_b64(private_key)
    if public_key_b64 != UPDATE_PUBLIC_KEY_B64:
        raise RuntimeError(
            "Der lokale DPAPI-Schlüssel passt nicht zum im Manager fest eingebauten öffentlichen Schlüssel."
        )

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    signature = private_key.sign(_signed_payload(expected_name, version, digest))
    manifest = {
        "format": UPDATE_FORMAT,
        "version": version,
        "asset": expected_name,
        "sha256": digest,
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    manifest_path = Path(str(archive) + ".sig.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="ascii"
    )
    print(f"Release-Paket signiert: {archive.name}")
    print(f"Signaturdatei für dasselbe GitHub-Release: {manifest_path.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="DPAPI-Release-Schlüssel anlegen oder anzeigen")
    subparsers.add_parser("check", help="DPAPI-Release-Schlüssel prüfen")
    inventory = subparsers.add_parser("inventory", help="Paket-Dateiliste für sichere Datenerhaltung erzeugen")
    inventory.add_argument("directory")
    sign = subparsers.add_parser("sign", help="Versioniertes Windows-ZIP signieren")
    sign.add_argument("archive")
    sign.add_argument("version")
    args = parser.parse_args()

    try:
        if args.command == "init":
            initialize_key()
        elif args.command == "check":
            verify_key()
        elif args.command == "inventory":
            write_inventory(args.directory)
        else:
            sign_archive(args.archive, args.version)
    except Exception as ex:
        print(f"FEHLER: {ex}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
