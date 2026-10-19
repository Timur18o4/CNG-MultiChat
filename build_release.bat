@echo off
setlocal
cd /d "%~dp0"

set "PYI=.buildenv\Scripts\python.exe"
for /f "usebackq delims=" %%v in ("VERSION") do set "APP_VERSION=%%v"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss_fff"') do set "BUILD_TAG=%%i"
if not defined BUILD_TAG (
    echo FEHLER: Zeitstempel fuer den Build-Ausgabeordner konnte nicht erzeugt werden.
    goto :fail
)
set "RELEASE_DIR=release\CNG_MultiChat_Manager_%BUILD_TAG%"
set "PACKAGE_ZIP=%RELEASE_DIR%\CNG_MultiChat_Windows_v%APP_VERSION%.zip"

if not defined APP_VERSION (
    echo FEHLER: VERSION fehlt oder ist leer.
    goto :fail
)

if not exist "CNG_MultiChat_Manager.py" (
    echo FEHLER: CNG_MultiChat_Manager.py fehlt neben dieser Datei.
    goto :fail
)
if not exist "unified_chat_bridge.py" (
    echo FEHLER: unified_chat_bridge.py fehlt neben dieser Datei.
    goto :fail
)
for %%F in (pi_deployment.py install_pi.sh requirements-linux.txt Raspberry-Pi-Bereitstellung.md) do (
    if not exist "%%F" (
        echo FEHLER: %%F fehlt. Die Raspberry-Pi-Bereitstellung kann nicht erstellt werden.
        goto :fail
    )
)
for %%F in (alerts.html cng-multichat-livechat.html cng-multichat-overlay.html cng-multichat-overlay_horizontal.html) do (
    if not exist "%%F" (
        echo FEHLER: %%F fehlt neben dieser Datei und muss im Release enthalten sein.
        goto :fail
    )
)
if not exist "CNG_MultiChat_Installations_und_Benutzeranleitung.pdf" (
    echo FEHLER: Die Installations- und Benutzeranleitung fehlt neben dieser Datei.
    echo Die Dokumentation muss im Buildkit enthalten sein.
    goto :fail
)
if not exist "LICENSE" (
    echo FEHLER: Die MIT-Lizenzdatei LICENSE fehlt neben dieser Datei.
    echo Die Lizenz muss im Buildkit enthalten sein.
    goto :fail
)
for %%F in (VERSION update_service.py release_signing.py) do (
    if not exist "%%F" (
        echo FEHLER: %%F fehlt und wird für den signierten Updater benötigt.
        goto :fail
    )
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "if ($env:APP_VERSION -notmatch '^\d+\.\d+\.\d+$') { exit 1 }; $m = Get-Content -Raw -LiteralPath 'CNG_MultiChat_Manager.py'; $b = Get-Content -Raw -LiteralPath 'unified_chat_bridge.py'; if ($m -notmatch 'BRIDGE_EXE = os\.path\.join\(BASE_DIR, .UnifiedChatBridge\.exe.\)' -or $m -notmatch '(?s)taskkill.{0,200}/T.{0,50}/F' -or $m -notmatch 'set_windows_startup' -or $m -notmatch 'start_minimized' -or $m -notmatch 'minimize_to_tray' -or $m -notmatch 'start_update_check' -or $m -notmatch 'bridge_log_partial_line' -or $b -notmatch 'sys\.executable if getattr\(sys, .frozen., False\) else __file__') { exit 1 }"
if errorlevel 1 (
    echo FEHLER: Die Python-Quelldateien in diesem Ordner enthalten noch nicht die Verpackungs-Korrekturen.
    echo Bitte das aktuelle Buildkit entpacken und alle enthaltenen Dateien hier ueberschreiben.
    goto :fail
)

if not exist "%PYI%" (
    echo Erstelle isolierte Build-Umgebung ...
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3 -m venv .buildenv
    ) else (
        where python >nul 2>&1
        if errorlevel 1 (
            echo FEHLER: Python 3 wurde nicht gefunden. Installiere Python 3 und aktiviere "Add Python to PATH".
            goto :fail
        )
        python -m venv .buildenv
    )
    if errorlevel 1 goto :fail

)

echo Installiere oder pruefe Build-Werkzeuge und Programmabhaengigkeiten ...
"%PYI%" -m pip install --upgrade pip
if errorlevel 1 goto :fail
"%PYI%" -m pip install pyinstaller requests websocket-client TikTokLive Pillow pystray paramiko
if errorlevel 1 goto :fail

echo Pruefe den lokalen Herausgeberschluessel ...
"%PYI%" release_signing.py check
if errorlevel 1 goto :fail

echo Erzeuge Bridge-EXE ...
"%PYI%" -m PyInstaller --clean --noconfirm --onefile --console ^
    --name UnifiedChatBridge ^
    --distpath build\bridge-dist --workpath build\bridge-work --specpath build\spec ^
    --collect-all TikTokLive --copy-metadata TikTokLive --hidden-import websocket ^
    unified_chat_bridge.py
if errorlevel 1 goto :fail

echo Erzeuge Manager-EXE ...
"%PYI%" -m PyInstaller --clean --noconfirm --onedir --windowed ^
    --name CNG_MultiChat_Manager ^
    --distpath "%RELEASE_DIR%" --workpath build\manager-work --specpath build\spec ^
    --collect-all TikTokLive --copy-metadata TikTokLive ^
    --collect-all pystray --collect-all PIL --hidden-import websocket ^
    --collect-all paramiko --collect-all cryptography --collect-all bcrypt --collect-all nacl ^
    CNG_MultiChat_Manager.py
if errorlevel 1 goto :fail

copy /y "build\bridge-dist\UnifiedChatBridge.exe" "%RELEASE_DIR%\CNG_MultiChat_Manager\UnifiedChatBridge.exe" >nul
if errorlevel 1 goto :fail

for %%F in (unified_chat_bridge.py pi_deployment.py install_pi.sh requirements-linux.txt Raspberry-Pi-Bereitstellung.md) do (
    copy /y "%%F" "%RELEASE_DIR%\CNG_MultiChat_Manager\%%F" >nul
    if errorlevel 1 goto :fail
)
copy /y "VERSION" "%RELEASE_DIR%\CNG_MultiChat_Manager\VERSION" >nul
if errorlevel 1 goto :fail

for %%F in (alerts.html cng-multichat-livechat.html cng-multichat-overlay.html cng-multichat-overlay_horizontal.html) do (
    copy /y "%%F" "%RELEASE_DIR%\CNG_MultiChat_Manager\%%F" >nul
    if errorlevel 1 goto :fail
)
copy /y "CNG_MultiChat_Installations_und_Benutzeranleitung.pdf" "%RELEASE_DIR%\CNG_MultiChat_Manager\CNG_MultiChat_Installations_und_Benutzeranleitung.pdf" >nul
if errorlevel 1 goto :fail
copy /y "LICENSE" "%RELEASE_DIR%\CNG_MultiChat_Manager\LICENSE" >nul
if errorlevel 1 goto :fail

(
    echo CNG MultiChat Manager - Windows-Paket
    echo.
    echo Start: CNG_MultiChat_Manager.exe
    echo Der Manager startet die Bridge bei Bedarf selbst.
    echo Unter Raspberry Pi kann die aktuelle Bridge-Konfiguration per SSH auf Raspberry Pi OS 64-Bit uebertragen werden.
    echo HTML-Ressourcen: alerts.html, cng-multichat-livechat.html, cng-multichat-overlay.html und cng-multichat-overlay_horizontal.html
    echo Bitte den gesamten Ordner entpackt lassen und an einen beschreibbaren Ort verschieben.
    echo Konfigurationen und Logs werden neben dem Programm abgelegt.
    echo Autostart, minimierter Start und Minimieren ins Tray koennen im Manager unter Einstellungen aktiviert werden.
    echo Der Manager prueft beim Start automatisch auf stabile Updates. Updates werden nur mit gueltiger Herausgebersignatur installiert.
    echo Fuer neue Versionen muessen Windows-ZIP und zugehoerige .sig.json-Datei gemeinsam im GitHub-Release liegen.
    echo Schliessen ueber X blendet den Manager ins Tray aus; beim Beenden ueber das Tray-Menue werden Manager und Bridge beendet.
    echo Python muss auf dem Ziel-PC nicht installiert sein.
    echo Benutzeranleitung: CNG_MultiChat_Installations_und_Benutzeranleitung.pdf
    echo Lizenz: MIT (siehe LICENSE)
) > "%RELEASE_DIR%\CNG_MultiChat_Manager\Liesmich.txt"

"%PYI%" release_signing.py inventory "%RELEASE_DIR%\CNG_MultiChat_Manager"
if errorlevel 1 goto :fail

powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path '%RELEASE_DIR%\CNG_MultiChat_Manager\*' -DestinationPath '%PACKAGE_ZIP%' -Force"
if errorlevel 1 goto :fail
"%PYI%" release_signing.py sign "%PACKAGE_ZIP%" "%APP_VERSION%"
if errorlevel 1 goto :fail

echo.
echo FERTIG.
echo Startdatei: %CD%\%RELEASE_DIR%\CNG_MultiChat_Manager\CNG_MultiChat_Manager.exe
echo Community-ZIP: %CD%\%PACKAGE_ZIP%
echo Signatur: %CD%\%PACKAGE_ZIP%.sig.json
echo Beide Dateien muessen im selben GitHub-Release liegen, damit automatische Updates funktionieren.
echo.
pause
exit /b 0

:fail
echo.
echo BUILD FEHLGESCHLAGEN. Die Fehlermeldung steht weiter oben.
pause
exit /b 1
