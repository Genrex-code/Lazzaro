
segurity.sh
#!/usr/bin/env bash
set -euo pipefail

# Abre un puerto TCP en Linux Mint/Ubuntu usando UFW.
# Uso:
#   sudo ./segurity.sh <puerto> [ip_origen]
#
# Ejemplos:
#   sudo ./segurity.sh 8080
#   sudo ./segurity.sh 8080 192.168.1.50
#
# Nota: esto solo abre el firewall de la PC. Para acceso desde Internet,
# tambien debes redirigir el puerto en el router hacia la IP local de esta PC.

usage() {
  echo "Uso: sudo $0 <puerto_tcp> [ip_origen_permitida]"
  echo "Ejemplo LAN: sudo $0 8080 192.168.1.50"
  echo "Ejemplo abierto: sudo $0 8080"
}

fail() {
  echo "Error: $*" >&2
  exit 1
}

PORT="${1:-}"
SOURCE_IP="${2:-}"

if [[ -z "$PORT" || "$PORT" == "-h" || "$PORT" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "$EUID" -ne 0 ]]; then
  fail "ejecuta este script con sudo."
fi

if ! [[ "$PORT" =~ ^[0-9]+$ ]]; then
  fail "el puerto debe ser un numero."
fi

if (( PORT < 1 || PORT > 65535 )); then
  fail "el puerto debe estar entre 1 y 65535."
fi

if ! command -v ufw >/dev/null 2>&1; then
  fail "ufw no esta instalado. Instala con: sudo apt update && sudo apt install ufw"
fi

echo "Configurando UFW..."
ufw default deny incoming
ufw default allow outgoing

if [[ -n "$SOURCE_IP" ]]; then
  echo "Abriendo TCP/$PORT solo para $SOURCE_IP..."
  ufw allow from "$SOURCE_IP" to any port "$PORT" proto tcp
else
  echo "Abriendo TCP/$PORT para cualquier origen..."
  ufw allow "$PORT/tcp"
fi

if ! ufw status | grep -qi "Status: active"; then
  echo "Activando UFW..."
  ufw --force enable
fi

echo
echo "Reglas actuales:"
ufw status numbered

echo
echo "Comprobando si hay algun servicio escuchando en TCP/$PORT..."
if command -v ss >/dev/null 2>&1; then
  ss -ltnp | grep -E ":${PORT}\b" || true
else
  echo "No se encontro 'ss' para comprobar listeners."
fi

echo
echo "Listo. Si quieres acceder desde otra PC en la misma red:"
echo "  1. Averigua la IP local con: hostname -I"
echo "  2. En la otra PC usa: http://IP_LOCAL:${PORT} o el cliente TCP que corresponda"
echo
echo "Si quieres acceso desde Internet:"
echo "  1. Configura port forwarding en el router hacia la IP local de esta PC"
echo "  2. Asegurate de que el servicio que usara el puerto tenga autenticacion segura"
#!/usr/bin/env bash
set -euo pipefail

# Abre un puerto TCP en Linux Mint/Ubuntu usando UFW.
# Uso:
#   sudo ./segurity.sh <puerto> [ip_origen]
#
# Ejemplos:
#   sudo ./segurity.sh 8080
#   sudo ./segurity.sh 8080 192.168.1.50
#
# Nota: esto solo abre el firewall de la PC. Para acceso desde Internet,
# tambien debes redirigir el puerto en el router hacia la IP local de esta PC.

usage() {
  echo "Uso: sudo $0 <puerto_tcp> [ip_origen_permitida]"
  echo "Ejemplo LAN: sudo $0 8080 192.168.1.50"
  echo "Ejemplo abierto: sudo $0 8080"
}

fail() {
  echo "Error: $*" >&2
  exit 1
}

PORT="${1:-}"
SOURCE_IP="${2:-}"

if [[ -z "$PORT" || "$PORT" == "-h" || "$PORT" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "$EUID" -ne 0 ]]; then
  fail "ejecuta este script con sudo."
fi

if ! [[ "$PORT" =~ ^[0-9]+$ ]]; then
  fail "el puerto debe ser un numero."
fi

if (( PORT < 1 || PORT > 65535 )); then
  fail "el puerto debe estar entre 1 y 65535."
fi

if ! command -v ufw >/dev/null 2>&1; then
  fail "ufw no esta instalado. Instala con: sudo apt update && sudo apt install ufw"
fi

echo "Configurando UFW..."
ufw default deny incoming
ufw default allow outgoing

if [[ -n "$SOURCE_IP" ]]; then
  echo "Abriendo TCP/$PORT solo para $SOURCE_IP..."
  ufw allow from "$SOURCE_IP" to any port "$PORT" proto tcp
else
  echo "Abriendo TCP/$PORT para cualquier origen..."
  ufw allow "$PORT/tcp"
fi

if ! ufw status | grep -qi "Status: active"; then
  echo "Activando UFW..."
  ufw --force enable
fi

echo
echo "Reglas actuales:"
ufw status numbered

echo
echo "Comprobando si hay algun servicio escuchando en TCP/$PORT..."
if command -v ss >/dev/null 2>&1; then
  ss -ltnp | grep -E ":${PORT}\b" || true
else
  echo "No se encontro 'ss' para comprobar listeners."
fi

echo
echo "Listo. Si quieres acceder desde otra PC en la misma red:"
echo "  1. Averigua la IP local con: hostname -I"
echo "  2. En la otra PC usa: http://IP_LOCAL:${PORT} o el cliente TCP que corresponda"
echo
echo "Si quieres acceso desde Internet:"
echo "  1. Configura port forwarding en el router hacia la IP local de esta PC"
echo "  2. Asegurate de que el servicio que usara el puerto tenga autenticacion segura"
