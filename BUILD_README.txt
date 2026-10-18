CNG MultiChat Manager Buildkit

Dieses Buildkit enthält den Windows-Quellcode und die Raspberry-Pi-Bereitstellung. Es ist für die Person gedacht, die ein Windows-Paket erstellt. Endnutzer verwenden das fertige Release-ZIP.

1. Entpacke den gesamten Repository-Inhalt in einen beschreibbaren Ordner.
2. Öffne dort PowerShell oder die Eingabeaufforderung.
3. Führe `build_release.bat` aus.
4. Das Community-Paket wird in einem neuen, zeitgestempelten Ordner unter `release\` abgelegt.

Der Build benötigt Windows, Python 3 und beim ersten Durchlauf Internetzugriff, um Build-Abhängigkeiten herunterzuladen. Der Build-Starter prüft die benötigten Quelldateien, HTML-Ressourcen, Benutzeranleitung und MIT-Lizenz. Das fertige Windows-Paket enthält Manager, Bridge, HTML-Ressourcen, PDF-Anleitung, Pi-Übertragungsdateien und benötigte Laufzeitdateien; auf dem Ziel-PC ist Python nicht erforderlich.

Die Raspberry-Pi-Bereitstellung erfolgt aus dem Manager über SSH. Voraussetzungen und Hinweise stehen in `Raspberry-Pi-Bereitstellung.md`.
