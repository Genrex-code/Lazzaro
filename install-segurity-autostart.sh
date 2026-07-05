#!/usr/bin/env bash
set -euo pipefail

# Instala un servicio systemd para ejecutar segurity.sh en cada arranque.
# Uso:
#   sudo ./install-segurity-autostart.sh <puerto> [ip_origen]
#
# Ejemplos:
#   sudo ./install-segurity-autostart.sh 8080
#   sudo ./install-segurity-autostart.sh 8080 192.168.1.50
#
# Para desinstalar:
#   sudo ./install-segurity-autostart.sh --remove

SERVICE_NAME="segurity-open-port.service"
CONFIG_FILE="/etc/default/segurity-open-port"
SCRIPT_TARGET="/usr/local/sbin/segurity-open-port.sh"
BOOT_WRAPPER="/usr/local/sbin/segurity-open-port-at-boot.sh"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"

usage() {
  echo "Uso: sudo $0 <puerto_tcp> [ip_origen_permitida]"
  echo "Ejemplo LAN: sudo $0 8080 192.168.1.50"
  echo "Ejemplo abierto: sudo $0 8080"
  echo "Desinstalar: sudo $0 --remove"
}

fail() {
  echo "Error: $*" >&2
  exit 1
}

require_root() {
  if [[ "$EUID" -ne 0 ]]; then
    fail "ejecuta este script con sudo."
  fi
}

remove_service() {
  require_root

  systemctl disable --now "$SERVICE_NAME" >/dev/null 2>&1 || true
  rm -f "$SERVICE_FILE" "$BOOT_WRAPPER" "$SCRIPT_TARGET" "$CONFIG_FILE"
  systemctl daemon-reload

  echo "Servicio de arranque eliminado."
  echo "Nota: las reglas UFW ya creadas no se borraron automaticamente."
  echo "Puedes revisarlas con: sudo ufw status numbered"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "${1:-}" == "--remove" ]]; then
  remove_service
  exit 0
fi

PORT="${1:-}"
SOURCE_IP="${2:-}"

if [[ -z "$PORT" ]]; then
  usage
  exit 0
fi

require_root

if ! [[ "$PORT" =~ ^[0-9]+$ ]]; then
  fail "el puerto debe ser un numero."
fi

if (( PORT < 1 || PORT > 65535 )); then
  fail "el puerto debe estar entre 1 y 65535."
fi

if ! command -v systemctl >/dev/null 2>&1; then
  fail "systemd/systemctl no esta disponible en este sistema."
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_SOURCE="${SCRIPT_DIR}/segurity.sh"

if [[ ! -f "$SCRIPT_SOURCE" ]]; then
  fail "no se encontro segurity.sh junto a este instalador."
fi

install -m 0755 "$SCRIPT_SOURCE" "$SCRIPT_TARGET"

cat > "$CONFIG_FILE" <<EOF
SEGURITY_PORT="${PORT}"
SEGURITY_SOURCE_IP="${SOURCE_IP}"
EOF
chmod 0644 "$CONFIG_FILE"

cat > "$BOOT_WRAPPER" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

source /etc/default/segurity-open-port

if [[ -n "${SEGURITY_SOURCE_IP:-}" ]]; then
  exec /usr/local/sbin/segurity-open-port.sh "$SEGURITY_PORT" "$SEGURITY_SOURCE_IP"
fi

exec /usr/local/sbin/segurity-open-port.sh "$SEGURITY_PORT"
EOF
chmod 0755 "$BOOT_WRAPPER"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Open configured TCP port with UFW at boot
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=${BOOT_WRAPPER}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "$SERVICE_FILE"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo
echo "Servicio instalado y ejecutado correctamente."
echo "Se ejecutara automaticamente en cada arranque:"
echo "  ${SERVICE_NAME}"
echo
echo "Comandos utiles:"
echo "  sudo systemctl status ${SERVICE_NAME}"
echo "  sudo journalctl -u ${SERVICE_NAME} -n 80 --no-pager"
echo "  sudo ./install-segurity-autostart.sh --remove"
