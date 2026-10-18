import json
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pi_deployment


SOURCE_DIR = Path(__file__).resolve().parent


class PiBundleTests(unittest.TestCase):
    def test_pi_installer_checks_dependencies_and_service_health_without_curl(self):
        installer = (SOURCE_DIR / "install_pi.sh").read_text(encoding="utf-8")
        requirements = (SOURCE_DIR / "requirements-linux.txt").read_text(encoding="utf-8")
        for marker in (
            "python3 -m venv",
            "pip install",
            "systemctl enable",
            "systemctl restart",
            "http://127.0.0.1:8765/health",
            'systemctl status "${SERVICE_NAME}" --no-pager -l',
            'journalctl -u "${SERVICE_NAME}" -n 50 --no-pager',
            "trap rollback EXIT",
            "rollback_healthy=0",
            "Vorversion wurde wiederhergestellt, beantwortet /health aber nicht",
        ):
            self.assertIn(marker, installer)
        self.assertNotRegex(installer, r"\bcurl(?:\.exe)?\b")
        for dependency in ("requests", "websocket-client", "TikTokLive", "yt-dlp"):
            self.assertIn(dependency, requirements)

    def test_bridge_avoids_python_312_only_nested_fstring_quotes(self):
        bridge = (SOURCE_DIR / "unified_chat_bridge.py").read_text(encoding="utf-8")
        self.assertIn('rpc_version = identify["d"].get("rpcVersion")', bridge)
        self.assertIn('authentication_configured = bool(identify["d"].get("authentication"))', bridge)
        for incompatible in (
            'f"rpcVersion={identify["d"]',
            'f"password_configured={bool(identify["d"]',
            'f"{years} {"Jahr"',
            'f"{months} {"Monat"',
            'f"{days} {"Tag"',
        ):
            self.assertNotIn(incompatible, bridge)

    def test_bridge_emits_linux_startup_checkpoints(self):
        bridge = (SOURCE_DIR / "unified_chat_bridge.py").read_text(encoding="utf-8")
        for marker in (
            "[PI STARTUP] {stage}",
            "Bridge-Modulimport gestartet",
            "TikTokLive-Import startet",
            "TikTokLive-Import beendet",
            "requests-Import startet",
            "websocket-Import startet",
            "main erreicht; lade Bridge-Konfiguration",
            "HTTP-Server gebunden auf {HOST}:{PORT}; Healthcheck verfuegbar",
        ):
            self.assertIn(marker, bridge)
        self.assertIn('if sys.platform.startswith("linux"):', bridge)

    def test_bundle_contains_snapshot_media_and_pi_safe_overlay_urls(self):
        with tempfile.TemporaryDirectory(prefix="cng-pi-test-") as temp:
            root = Path(temp)
            base = root / "manager"
            base.mkdir()
            for filename in pi_deployment.REQUIRED_FILES:
                (base / filename).write_bytes((SOURCE_DIR / filename).read_bytes())
            gif = base / "follow.gif"
            gif.write_bytes(b"GIF89a-test")
            sound = base / "follow.mp3"
            sound.write_bytes(b"fake-mp3-test")
            destination = root / "bundle.tar.gz"

            archive_path, local_ip, cleanup = pi_deployment.create_bundle(
                base,
                target_host="pi5.local",
                source_windows_ip="192.168.1.77",
                accounts=[{
                    "id": "kick-1", "platform": "kick", "channel": "creator",
                    "auth": {"access_token": "test-only-token"},
                }],
                bridge_config={
                    "obs": {"enabled": True, "host": "localhost", "port": 4455, "password": "obs-test"},
                    "alerts": {"follow": {"gif": str(gif), "sound": str(sound), "duration_seconds": 12}},
                },
                command_bot_config={
                    "command_bot": "lumiastream",
                    "lumiastream": {"api_url": "http://localhost:39231/api/send", "token": "lumia-test"},
                    "streamerbot": {"host": "127.0.0.1", "port": 8080},
                },
                alert_settings={
                    "follow": {"label": "Follow", "gif": str(gif), "sound": str(sound), "duration_seconds": 12},
                },
                display_settings={"chat_layout": "classic"},
                obs_settings={"enabled": True, "host": "127.0.0.1", "port": 4455, "password": "obs-test"},
                destination=destination,
            )
            self.addCleanup(cleanup)
            self.assertEqual(local_ip, "192.168.1.77")
            self.assertEqual(Path(archive_path), destination)

            with tarfile.open(archive_path, "r:gz") as archive:
                names = set(archive.getnames())
                self.assertIn("cng-multichat/unified_chat_bridge.py", names)
                self.assertIn("cng-multichat/requirements-linux.txt", names)
                self.assertIn("cng-multichat/install_pi.sh", names)
                self.assertTrue(any(name.startswith("cng-multichat/media/") for name in names))
                files = {
                    member.name.removeprefix("cng-multichat/"): archive.extractfile(member).read()
                    for member in archive.getmembers()
                    if member.name.startswith("cng-multichat/") and member.isfile()
                }

            accounts = json.loads(files["accounts.json"])
            command = json.loads(files["command_bot.json"])
            bridge = json.loads(files["unified_chat_config.json"])
            obs = json.loads(files["obs_settings.json"])
            alert = json.loads(files["alert_settings.json"])
            self.assertEqual(accounts["accounts"][0]["auth"]["access_token"], "test-only-token")
            self.assertEqual(command["lumiastream"]["api_url"], "http://192.168.1.77:39231/api/send")
            self.assertEqual(command["streamerbot"]["host"], "192.168.1.77")
            self.assertEqual(obs["host"], "192.168.1.77")
            self.assertEqual(bridge["obs"]["host"], "192.168.1.77")
            self.assertTrue(alert["follow"]["gif"].startswith("media/"))
            self.assertTrue(alert["follow"]["sound"].startswith("media/"))
            self.assertEqual(bridge["alerts"]["follow"]["gif"], alert["follow"]["gif"])

            for filename in (
                "alerts.html",
                "cng-multichat-livechat.html",
                "cng-multichat-overlay_horizontal.html",
            ):
                html = files[filename].decode("utf-8")
                self.assertIn("window.location.origin", html, filename)
            self.assertIn("BRIDGE_BASE_URL+\"/events\"", files["cng-multichat-livechat.html"].decode("utf-8"))
            self.assertIn("BRIDGE_BASE_URL+\"/config\"", files["cng-multichat-overlay_horizontal.html"].decode("utf-8"))

            node = shutil.which("node")
            if node:
                with tempfile.TemporaryDirectory(prefix="cng-pi-js-test-") as js_temp:
                    for filename in (
                        "alerts.html",
                        "cng-multichat-livechat.html",
                        "cng-multichat-overlay_horizontal.html",
                    ):
                        html = files[filename].decode("utf-8")
                        scripts = re.findall(r"<script\b[^>]*>(.*?)</script\s*>", html, re.I | re.S)
                        for index, script in enumerate(scripts):
                            script_path = Path(js_temp) / f"{filename}-{index}.js"
                            script_path.write_text(script, encoding="utf-8")
                            result = subprocess.run(
                                [node, "--check", str(script_path)],
                                capture_output=True,
                                text=True,
                                check=False,
                            )
                            self.assertEqual(result.returncode, 0, result.stderr)

    def test_loopback_url_rewrite_preserves_non_loopback_and_query(self):
        self.assertEqual(
            pi_deployment._replace_url_loopback(
                "http://localhost:39231/api/send?token=private", "10.0.0.12"
            ),
            "http://10.0.0.12:39231/api/send?token=private",
        )
        self.assertEqual(
            pi_deployment._replace_url_loopback("https://example.com:99/a", "10.0.0.12"),
            "https://example.com:99/a",
        )

    def test_known_host_mismatch_is_rejected(self):
        import paramiko

        with tempfile.TemporaryDirectory(prefix="cng-pi-hostkey-test-") as temp:
            known_hosts = Path(temp) / "known_hosts"
            key_one = paramiko.RSAKey.generate(1024)
            key_two = paramiko.RSAKey.generate(1024)
            with mock.patch.object(pi_deployment, "_known_hosts_path", return_value=known_hosts):
                self.assertFalse(pi_deployment.host_key_is_trusted("pi-test", 22, key_one))
                pi_deployment.trust_host_key("pi-test", 22, key_one)
                self.assertTrue(pi_deployment.host_key_is_trusted("pi-test", 22, key_one))
                with self.assertRaises(paramiko.SSHException):
                    pi_deployment.host_key_is_trusted("pi-test", 22, key_two)


if __name__ == "__main__":
    unittest.main(verbosity=2)
