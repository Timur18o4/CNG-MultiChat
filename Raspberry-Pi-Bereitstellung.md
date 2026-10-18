# Raspberry-Pi-Bereitstellung

## Voraussetzungen

- Raspberry Pi 5 mit Raspberry Pi OS 64-Bit und Internetzugang
- SSH auf dem Pi aktiviert
- Ein Linux-Benutzer mit SSH-Zugang und `sudo`-Rechten
- Windows-PC und Pi im selben vertrauenswürdigen Netzwerk

## Installation aus dem Manager

1. Im Manager links **Raspberry Pi** öffnen.
2. Pi-IP-Adresse, SSH-Port (normalerweise `22`), Linux-Benutzer und SSH-Passwort eingeben.
3. Falls das `sudo`-Passwort anders ist, das abweichende Passwort ebenfalls eintragen.
4. Beim ersten SSH-Kontakt den angezeigten Fingerabdruck prüfen und den Hostschlüssel bestätigen.
5. **Konfiguration sicher übertragen** wählen.

Der Manager erstellt das Paket aus den gespeicherten Konten und Einstellungen. Alert-GIFs und Sounds, die als lokale Dateien eingerichtet sind, werden in das Paket kopiert. Der Pi richtet Python-Abhängigkeiten in einer eigenen virtuellen Umgebung sowie einen systemd-Dienst ein. Der Dienst startet automatisch beim Systemstart.

Ein erneuter Export ersetzt die verwaltete Bridge-Installation und startet sie neu. Chat-Follower-Snapshot und Logs werden dabei übernommen. SSH- und `sudo`-Passwörter werden nicht dauerhaft gespeichert.

Wenn Windows- und Pi-Bridge gleichzeitig laufen, können Chat-Ereignisse, Alerts und Commands doppelt verarbeitet werden. Im Manager kann ausgewählt werden, dass die lokale Windows-Bridge nach erfolgreichem Pi-Healthcheck beendet wird.

## Zugriff

Nach erfolgreicher Installation zeigt der Manager die Pi-Adressen für Overlay, Livechat, Alerts und Stats an. OBS Browser Sources müssen die jeweilige URL mit der Pi-IP verwenden. Beispiel: `http://192.168.1.50:8765/livechat`.

Die Bridge bindet an alle Netzwerkschnittstellen und schützt ihre HTTP-Seiten nicht mit einem Login. Port `8765` deshalb nur in einem vertrauenswürdigen LAN oder über ein VPN erreichbar machen und nicht direkt per Router-Portweiterleitung ins öffentliche Internet öffnen.

Wenn OBS, LumiaStream oder Streamer.bot weiter auf dem Windows-PC laufen, ersetzt der Export lokale `localhost`-Adressen durch die LAN-IP des PCs. Die betreffenden Windows-Dienste und die Firewall müssen Verbindungen vom Pi zulassen. Der Manager ändert diese Windows-Einstellungen nicht selbst.

## Verifizierter Teststatus

Die Bereitstellung wurde am 19.10.2026 auf einem Raspberry Pi 5 mit Raspberry Pi OS 64-Bit erfolgreich abgeschlossen. Der systemd-Dienst startete und der Manager meldete den Bridge-Healthcheck als erfolgreich; Overlay-, Livechat-, Alerts- und Stats-Adressen wurden ausgegeben.

Damit sind Paketübertragung, Installation und Erreichbarkeit der Bridge über `/health` bestätigt. Ein vollständiger Ende-zu-Ende-Test aller externen Chatplattformen, Alerts und OBS-Verbindungen auf dem Pi ist damit nicht bestätigt und muss bei Bedarf separat geprüft werden.
