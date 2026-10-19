# Änderungen

## v1.0.4 — 19.10.2026

### Windows-Manager

- Der Manager zeigt seine Version an und prüft beim Start im Hintergrund auf eine neuere stabile GitHub-Version.
- Ein Update wird nur aus dem offiziellen `CNG-MultiChat`-Release geladen und vor dem Entpacken anhand der GitHub-SHA-256-Prüfsumme sowie einer fest eingebauten Ed25519-Herausgebersignatur geprüft.
- Der Manager beendet für den Austausch die Bridge und startet danach neu. Bei einem fehlgeschlagenen Start wird die vorherige Programmversion wiederhergestellt.
- Konten, Zugangsdaten, Einstellungen und lokale Dateien bleiben beim Austausch erhalten. Bei fehlender Internetverbindung läuft die installierte Version weiter.
- Das Buildkit erstellt ein versioniertes Windows-ZIP und eine passende `.sig.json`-Signaturdatei, die gemeinsam im GitHub-Release liegen müssen.
- Jede Zeile der Manager-Live-Log-Ansicht erhält eine lokale Uhrzeit.

## v1.0.3 — 19.10.2026

### Raspberry-Pi-Bereitstellung

- Der Manager überträgt die laufende Konfiguration und lokal eingerichtete Alert-Medien per SSH auf Raspberry Pi OS 64-Bit.
- Der Pi-Installer richtet Abhängigkeiten in einer virtuellen Umgebung und einen automatisch startenden systemd-Dienst ein.
- Der Bridge-Start schreibt Linux-Startmarken ins systemd-Journal, damit ein fehlender Healthcheck eingegrenzt werden kann.
- Nach einem fehlgeschlagenen Update startet der Installer die Vorversion nur dann wieder, wenn deren `/health`-Prüfung erfolgreich ist. Andernfalls bleibt der Dienst gestoppt.
- Python-3.11-Syntaxfehler im Bridge-Start wurden behoben.
- Die Bereitstellung und der Bridge-Healthcheck wurden auf Raspberry Pi 5 mit Raspberry Pi OS 64-Bit erfolgreich bestätigt. Ein Ende-zu-Ende-Test aller externen Chatplattformen ist davon nicht umfasst.

## v1.0.2 — 17.10.2026

### Lumia Stream

- Die Bridge liest Lumia-Token und API-Adresse aus der vom Manager gespeicherten `command_bot.json`.
- Die WebSocket-Adresse wird aus der konfigurierten REST-Adresse abgeleitet. Ein fehlgeschlagener Verbindungsaufbau wird protokolliert und erneut versucht.
- Abgewiesene REST-Befehle protokollieren die API-Antwort; Tokenwerte werden maskiert. Nach einem HTTP-Fehler wird keine veraltete CNG-Antwortvariable verarbeitet.

### Plattformverbindungen

- Twitch-IRC-`RECONNECT` löst einen unmittelbaren Neuaufbau der Verbindung aus; der konfigurierte Kanal wird danach erneut verbunden.
- TikTok überwacht eingehende WebSocket-Frames einschließlich Heartbeats. Bei zwei Minuten ohne Frames wird die hängende Verbindung geschlossen und automatisch neu aufgebaut.
- Kick erfasst Heartbeats auch während einer Token-Erneuerung. Thread-sichere Logausgabe verhindert, dass parallele Plattformmeldungen eine Kick-Fehlerzeile vermischen.

### Alert-Einstellungen

- Ältere Alert-Einstellungen werden beim Start in die aktuelle Konfiguration übernommen, ohne bewusst gespeicherte leere GIF- oder Soundfelder zu überschreiben.

## v1.0.1 — 17.10.2026

- CNG-Farbwelt für Livechat und Mehrplattform-Statistik
- Statistik zusätzlich unter /stats erreichbar; /cng-status bleibt als Alias verfügbar
- Windows-Autostart über die Manager-Einstellungen
- Option für minimierten Start im Infobereich
- Option, Fenster beim Minimieren ins Tray auszublenden
- Neue Seite „Über / Kanäle“ mit direkten Links zu den Profilen von Timur18o4
- Farbwelt und Navigation des Managers an den aktuellen CNG-Webauftritt angepasst
- Beim Beenden im Tray wird die Bridge zuverlässig mit beendet, auch während eines laufenden Startvorgangs
- Follow-/Unfollow-GIFs und -Sounds werden über die lokale Bridge an das Alert-Overlay ausgeliefert
- Alert-Overlay liest die vom Manager gespeicherten Medienpfade aus `alert_settings.json`; Änderungen gelten auch bei bereits laufender Bridge
- Aktualisierte deutsche Installations- und Benutzeranleitung
- Community-Standardwerte ohne lokale Medienpfade oder vorausgefüllte externe Zugangsdaten
