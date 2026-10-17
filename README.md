# CNG MultiChat

CNG MultiChat ist ein Windows-Programm, das mehrere Plattform-Chats über einen Manager und eine lokale Bridge in Browseransichten bereitstellt.

## Downloads

Das fertige Windows-Paket wird auf der GitLab-Projektseite unter **Releases** bereitgestellt. Das ZIP vollständig entpacken und `CNG_MultiChat_Manager.exe` starten. Python wird auf dem Ziel-PC nicht benötigt. Die HTML-Dateien und die PDF-Anleitung gehören zum Paket.

Der Quellcode liegt im GitLab-Repository. Das fertige Windows-Paket wird zusätzlich als Release-Datei bereitgestellt.

## Aus dem Quellcode bauen

Der Quellcode umfasst den Manager, die Bridge, vier HTML-Ansichten, die Build-Datei, die MIT-Lizenz und die Benutzeranleitung. Für einen Windows-Build: den gesamten Repository-Inhalt herunterladen und entpacken, Python 3 und Internetzugriff auf dem Build-PC bereitstellen und `build_release.bat` starten. Die Build-Datei erstellt eine isolierte Umgebung, lädt die benötigten Build-Abhängigkeiten und legt das fertige Community-Paket in einem neuen Ordner unter `release\` ab.

Das Buildkit ist für die Person gedacht, die den Release erstellt. Endnutzer verwenden das fertige Windows-ZIP.

Beim Schließen über das X wird der Manager ins Tray ausgeblendet. Wenn du im Tray-Menü **Bridge und Manager beenden** wählst, werden beide Prozesse beendet.

Die Mehrplattform-Statistik mit Zuschauer- und Followerzahlen ist unter `http://127.0.0.1:8765/stats` erreichbar. Die bisherige Adresse `/cng-status` bleibt als Alias verfügbar.

## Konfiguration und Datenschutz

Konten, Tokens und lokale Einstellungen werden zur Laufzeit in JSON-Dateien neben dem Programm gespeichert. Diese Dateien sind über `.gitignore` vom Repository ausgeschlossen. Sie dürfen nicht in öffentliche Commits, Issues oder Support-Beiträge kopiert werden.

## Lizenz

Der Quellcode wird unter der MIT-Lizenz bereitgestellt. Siehe [LICENSE](LICENSE).
