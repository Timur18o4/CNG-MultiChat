CNG MultiChat Manager Buildkit

Dieses Buildkit ist fuer die Person gedacht, die das Windows-Paket erstellt. Es ist nicht das Endnutzerpaket.

1. Entpacke die enthaltenen Dateien in den Quellordner und ueberschreibe vorhandene Dateien.
2. Oeffne dort PowerShell oder die Eingabeaufforderung.
3. Fuehre genau diesen Befehl aus: .\build_release.bat
4. Das fertige Community-Paket liegt danach in einem neuen, zeitgestempelten Ordner unter release\.

Der Build benoetigt Windows, Python 3 auf dem Build-PC und Internet fuer den erstmaligen Download der Build-Abhaengigkeiten. Vor dem Build prueft der Starter, ob die Verpackungs-Korrekturen, die vier benoetigten HTML-Dateien, die Benutzeranleitung und die MIT-Lizenz vorhanden sind. Der Manager beendet beim Stoppen auch den PyInstaller-Prozessbaum der Bridge. Auf dem Ziel-PC wird Python nicht benoetigt. Das Community-ZIP enthaelt Manager, Bridge, Benutzeranleitung, Lizenzdatei, alerts.html, cng-multichat-livechat.html, cng-multichat-overlay.html, cng-multichat-overlay_horizontal.html und die benoetigten Laufzeitdateien. Den gesamten entpackten Programmordner an einem beschreibbaren Ort aufbewahren.

Im Manager wird pro Plattformkonto nur ein Accountname eingegeben. Beim Speichern setzt der Manager sowohl channel als auch das von aelteren Bridge-Pfaden verwendete display_name auf diesen Wert. Unter Einstellungen koennen Windows-Autostart, minimierter Start und das Minimieren in den Infobereich (Tray) aktiviert werden. Das Schliessen ueber X blendet den Manager weiterhin ins Tray aus; beim Beenden ueber das Tray-Menue werden Manager und Bridge beendet.

Die Mehrplattform-Statistik ist unter http://127.0.0.1:8765/stats erreichbar; /cng-status bleibt als Alias verfuegbar.

Die MIT-Lizenzdatei LICENSE ist Bestandteil des Buildkits, des Quellcode-Repositorys und des fertigen Community-Pakets.
