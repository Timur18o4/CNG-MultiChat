#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="cng-multichat-bridge.service"
INSTALL_DIR="/opt/cng-multichat"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"
DEPLOY_USER="${1:-}"
SOURCE_DIR="${BASH_SOURCE[0]%/*}"
if [[ "${SOURCE_DIR}" == "${BASH_SOURCE[0]}" ]]; then
    SOURCE_DIR="."
fi
BACKUP_DIR="${INSTALL_DIR}.previous"
DEPLOYMENT_IN_PROGRESS=0
BACKUP_CREATED=0

fail() {
    printf '[INSTALL] ERROR: %s\n' "$*" >&2
    exit 1
}

[[ "${EUID}" -eq 0 ]] || fail "Installationsskript muss mit sudo laufen."
[[ "${DEPLOY_USER}" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] || fail "Ungültiger Linux-Benutzername."
id "$DEPLOY_USER" >/dev/null 2>&1 || fail "SSH-Benutzer '${DEPLOY_USER}' existiert nicht."
command -v systemctl >/dev/null 2>&1 || fail "systemd wurde auf diesem Linux-System nicht gefunden."
command -v apt-get >/dev/null 2>&1 || fail "Dieses Installationspaket erwartet Raspberry Pi OS/Debian mit apt-get."
command -v python3 >/dev/null 2>&1 || fail "python3 fehlt."

python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
    || fail "Python 3.10 oder neuer wird benötigt."

for required in unified_chat_bridge.py requirements-linux.txt install_pi.sh \
    alerts.html cng-multichat-livechat.html cng-multichat-overlay.html \
    cng-multichat-overlay_horizontal.html accounts.json \
    unified_chat_config.json command_bot.json alert_settings.json \
    display_settings.json obs_settings.json; do
    [[ -f "${SOURCE_DIR}/${required}" ]] || fail "Paketdatei fehlt: ${required}"
done

printf '[INSTALL] Prüfe Python-Abhängigkeiten für ARM/Linux ...\n'
mkdir -p "$(dirname -- "${INSTALL_DIR}")"
rm -rf "${INSTALL_DIR}.new"
mkdir -p "${INSTALL_DIR}.new"
cp -a "${SOURCE_DIR}/." "${INSTALL_DIR}.new/"
rm -rf "${INSTALL_DIR}.new/.venv"

# Build the virtual environment at its final path. Python virtual environments
# contain absolute paths and are not safely relocatable after creation.
if [[ -d "${INSTALL_DIR}" ]]; then
    systemctl stop "${SERVICE_NAME}" >/dev/null 2>&1 || true
    rm -rf "${BACKUP_DIR}"
    mv "${INSTALL_DIR}" "${BACKUP_DIR}"
    BACKUP_CREATED=1
fi
DEPLOYMENT_IN_PROGRESS=1
mv "${INSTALL_DIR}.new" "${INSTALL_DIR}"

rollback() {
    local status=$?
    if [[ ${status} -ne 0 && ${DEPLOYMENT_IN_PROGRESS} -eq 1 ]]; then
        printf '[INSTALL] Bereinigung nach fehlgeschlagenem Start ...\n' >&2
        systemctl stop "${SERVICE_NAME}" >/dev/null 2>&1 || true
        if [[ ${BACKUP_CREATED} -eq 1 && -d "${BACKUP_DIR}" ]]; then
            printf '[INSTALL] Stelle die vorherige Version wieder her ...\n' >&2
            rm -rf "${INSTALL_DIR}"
            mv "${BACKUP_DIR}" "${INSTALL_DIR}"
            systemctl daemon-reload >/dev/null 2>&1 || true
            systemctl start "${SERVICE_NAME}" >/dev/null 2>&1 || true
            local rollback_healthy=0
            for _ in $(seq 1 10); do
                if systemctl is-active --quiet "${SERVICE_NAME}" && \
                   "${INSTALL_DIR}/.venv/bin/python" -c \
                    'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=1).read()' \
                    >/dev/null 2>&1; then
                    rollback_healthy=1
                    break
                fi
                sleep 1
            done
            if [[ "${rollback_healthy}" -ne 1 ]]; then
                systemctl stop "${SERVICE_NAME}" >/dev/null 2>&1 || true
                printf '[INSTALL] Vorversion wurde wiederhergestellt, beantwortet /health aber nicht; Dienst bleibt gestoppt.\n' >&2
            else
                printf '[INSTALL] Vorversion wurde wiederhergestellt und ist erreichbar.\n' >&2
            fi
        else
            systemctl disable "${SERVICE_NAME}" >/dev/null 2>&1 || true
            rm -f "${SERVICE_FILE}"
            rm -rf "${INSTALL_DIR}"
            systemctl daemon-reload >/dev/null 2>&1 || true
        fi
    fi
}
trap rollback EXIT

if [[ -d "${BACKUP_DIR}/logs" ]]; then
    mkdir -p "${INSTALL_DIR}/logs"
    cp -a "${BACKUP_DIR}/logs/." "${INSTALL_DIR}/logs/"
fi
if [[ -f "${BACKUP_DIR}/cng_followers.json" ]]; then
    cp -a "${BACKUP_DIR}/cng_followers.json" "${INSTALL_DIR}/cng_followers.json"
fi

if ! python3 -m venv "${INSTALL_DIR}/.venv"; then
    printf '[INSTALL] python3-venv fehlt oder ist unvollständig; installiere es ...\n'
    rm -rf "${INSTALL_DIR}/.venv"
    apt-get update
    apt-get install -y python3-venv python3-pip
    python3 -m venv "${INSTALL_DIR}/.venv"
fi
"${INSTALL_DIR}/.venv/bin/python" -m pip install \
    --disable-pip-version-check --no-cache-dir \
    -r "${INSTALL_DIR}/requirements-linux.txt"
"${INSTALL_DIR}/.venv/bin/python" -c \
    'import requests, websocket, TikTokLive, yt_dlp; print("[INSTALL] Abhängigkeiten erfolgreich importiert.")'

DEPLOY_GROUP="$(id -gn "${DEPLOY_USER}")"
chown -R "${DEPLOY_USER}:${DEPLOY_GROUP}" "${INSTALL_DIR}"
chmod 750 "${INSTALL_DIR}"
for secret_file in accounts.json command_bot.json obs_settings.json unified_chat_config.json cng_creator_cookie.json; do
    if [[ -f "${INSTALL_DIR}/${secret_file}" ]]; then
        chmod 600 "${INSTALL_DIR}/${secret_file}"
    fi
done

cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=CNG MultiChat Bridge
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${DEPLOY_USER}
WorkingDirectory=${INSTALL_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${INSTALL_DIR}/.venv/bin/python ${INSTALL_DIR}/unified_chat_bridge.py
Restart=always
RestartSec=5
UMask=0027
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

printf '[INSTALL] Warte auf Bridge-Healthcheck ...\n'
healthy=0
for _ in $(seq 1 30); do
    if systemctl is-active --quiet "${SERVICE_NAME}" && \
       "${INSTALL_DIR}/.venv/bin/python" -c \
        'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=1).read()' \
        >/dev/null 2>&1; then
        healthy=1
        break
    fi
    sleep 1
done
if [[ "${healthy}" -ne 1 ]]; then
    printf '[INSTALL] systemd-Status der Bridge:\n' >&2
    systemctl status "${SERVICE_NAME}" --no-pager -l >&2 || true
    printf '[INSTALL] Letzte Bridge-Journalzeilen:\n' >&2
    journalctl -u "${SERVICE_NAME}" -n 50 --no-pager >&2 || true
    fail "Bridge wurde nicht rechtzeitig über /health erreichbar."
fi

trap - EXIT
rm -rf "${BACKUP_DIR}" 2>/dev/null || true
printf '[INSTALL] Bridge installiert und gestartet.\n'
printf '[INSTALL] Overlay: http://<PI-IP>:8765/\n'
printf '[INSTALL] Logs: journalctl -u %s -f\n' "${SERVICE_NAME}"
