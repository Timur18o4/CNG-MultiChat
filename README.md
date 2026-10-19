# CNG MultiChat

CNG MultiChat verbindet Chats und Statistiken mehrerer Plattformen mit Browser-Ansichten für Streams. Der Windows-Manager verwaltet die Konten und startet die Bridge.

## Downloads

Das fertige Windows-Paket gibt es unter [GitHub Releases](https://github.com/Timur18o4/CNG-MultiChat/releases). ZIP vollständig entpacken und `CNG_MultiChat_Manager.exe` starten. Auf dem Ziel-PC wird keine Python-Installation benötigt. Die HTML-Dateien und die PDF-Anleitung gehören zum Paket.

Der Windows-Manager prüft beim Start im Hintergrund auf eine neuere stabile Version. Ist eine gültige, signierte Version verfügbar, lädt der Manager sie herunter, prüft sie und startet nach der Installation neu. Bei fehlender Internetverbindung läuft die aktuelle Version weiter. Konten, Tokens, Einstellungen und lokale Medien bleiben erhalten. Der Manager und die Bridge werden während des Austauschs kurz beendet.

Der vollständige Quellcode steht in diesem Repository unter MIT-Lizenz.

## Raspberry Pi

Der Manager kann die aktuelle Konfiguration per SSH auf einen Raspberry Pi 5 mit Raspberry Pi OS 64-Bit übertragen. Die Einrichtung installiert die Linux-Abhängigkeiten in einer virtuellen Umgebung und richtet einen systemd-Dienst für den automatischen Start ein. Lokal konfigurierte Alert-GIFs und Sounds werden mit übertragen. Anleitung und Grenzen stehen in [Raspberry-Pi-Bereitstellung.md](Raspberry-Pi-Bereitstellung.md).

Der Pi stellt Overlay, Livechat, Alerts und Stats über das lokale Netzwerk bereit. Die HTTP-Seiten haben keinen Login. Port `8765` nur im vertrauenswürdigen LAN oder über VPN erreichbar machen, nicht direkt öffentlich freigeben.

## Aus dem Quellcode bauen

Zum Erstellen des Windows-Pakets unter Windows Python 3 und Internetzugriff für die Build-Abhängigkeiten bereitstellen und `build_release.bat` starten. Die Versionsnummer steht in `VERSION`. Das Build-Skript erstellt eine isolierte Umgebung und legt ein versioniertes Paket samt `.sig.json`-Signatur in einem neuen Ordner unter `release\` ab. Beide Dateien müssen als Assets desselben GitHub-Releases hochgeladen werden. Die Signatur wird mit einem DPAPI-geschützten privaten Schlüssel aus dem Windows-Profil des Release-Erstellers erzeugt; der private Schlüssel gehört weder ins Repository noch ins Community-Paket. Das Paket enthält Manager, Bridge, HTML-Ressourcen, PDF-Anleitung und die Laufzeitdateien.

Das Buildkit ist für die Person gedacht, die den Release erstellt. Endnutzer verwenden das fertige Windows-ZIP.

Beim Schließen über das X wird der Manager ins Tray ausgeblendet. Über **Bridge und Manager beenden** im Tray-Menü werden beide Prozesse beendet. Die Mehrplattform-Statistik ist unter `http://127.0.0.1:8765/stats` erreichbar; `/cng-status` bleibt als Alias verfügbar.

## Konfiguration und Datenschutz

Konten, Tokens und lokale Einstellungen werden zur Laufzeit in JSON-Dateien neben dem Programm gespeichert. Diese Dateien sind über `.gitignore` vom Repository ausgeschlossen. Sie dürfen nicht in öffentliche Commits, Issues oder Support-Beiträge kopiert werden.

## Lizenz

Der Quellcode wird unter MIT-Lizenz bereitgestellt. Siehe [LICENSE](LICENSE).
