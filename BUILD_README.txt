CNG MultiChat Manager Buildkit

Dieses Buildkit enthält den Windows-Quellcode und die Raspberry-Pi-Bereitstellung. Es ist für die Person gedacht, die ein Windows-Paket erstellt. Endnutzer verwenden das fertige Release-ZIP.

1. Entpacke den gesamten Repository-Inhalt in einen beschreibbaren Ordner.
2. Öffne dort PowerShell oder die Eingabeaufforderung.
3. Führe `build_release.bat` aus.
4. Das Community-Paket wird in einem neuen, zeitgestempelten Ordner unter `release\` abgelegt.

Die Versionsnummer steht in `VERSION`. Der Build erzeugt `CNG_MultiChat_Windows_v<VERSION>.zip` und daneben die signierte Datei mit dem Zusatz `.sig.json`. Lade beide Dateien als Assets in dasselbe stabile GitHub-Release hoch; die Tag-Version muss zur Versionsnummer passen.

Der private Herausgeberschlüssel wird mit Windows-DPAPI im Profil des Release-Erstellers abgelegt unter `%LOCALAPPDATA%\CNG MultiChat\release-signing-key.dpapi`. Er wird nicht in das Repository oder Paket kopiert. Der Schutz ist an das Windows-Profil gebunden; die Datei allein kann nicht auf einem anderen Rechner verwendet werden. Bei Verlust muss die Anwendung manuell mit einem Release aktualisiert werden, das einen neuen Herausgeberschlüssel festlegt.

Der Build benötigt Windows, Python 3 und beim ersten Durchlauf Internetzugriff, um Build-Abhängigkeiten herunterzuladen. Der Build-Starter prüft die benötigten Quelldateien, HTML-Ressourcen, Benutzeranleitung, MIT-Lizenz und den passenden Signaturschlüssel. Das fertige Windows-Paket enthält Manager, Bridge, HTML-Ressourcen, PDF-Anleitung, Pi-Übertragungsdateien und benötigte Laufzeitdateien; auf dem Ziel-PC ist Python nicht erforderlich.

Die Raspberry-Pi-Bereitstellung erfolgt aus dem Manager über SSH. Voraussetzungen und Hinweise stehen in `Raspberry-Pi-Bereitstellung.md`.
