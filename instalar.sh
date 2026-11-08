#!/usr/bin/env bash
# =============================================================================
#  LigaStats - instalador / actualizador para Ubuntu 24.04
#
#  Uso (como root, dentro de la carpeta descomprimida):
#     bash instalar.sh              instala o actualiza (nueva versión)
#     bash instalar.sh --https      solo configura stat.aitex.cl (nginx + certificado)
#     bash instalar.sh --rollback   vuelve a la versión anterior
#     bash instalar.sh --estado     muestra el estado sin cambiar nada
#
#  No toca voceo, Zabbix, PostgreSQL ni la configuración existente de nginx.
# =============================================================================
set -euo pipefail

DOMINIO="stat.aitex.cl"
PUERTO=8010
BASE=/opt/ligastats
USUARIO=ligastats
ORIGEN="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$BASE/venv"
SHARED="$BASE/shared"
ENVFILE="$SHARED/config.env"
NGINX_SITE="/etc/nginx/sites-available/$DOMINIO"
WEBROOT=/var/www/letsencrypt

verde() { printf '\033[32m%s\033[0m\n' "$*"; }
amarillo() { printf '\033[33m%s\033[0m\n' "$*"; }
rojo() { printf '\033[31m%s\033[0m\n' "$*"; }
paso() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fallar() { rojo "ERROR: $*"; exit 1; }

[[ $EUID -eq 0 ]] || fallar "ejecuta como root"

# ----------------------------------------------------------------------------
estado() {
    paso "Estado de LigaStats"
    echo "Versión activa : $(readlink -f $BASE/current 2>/dev/null || echo 'ninguna')"
    echo "Versiones      : $(ls -1 $BASE/releases 2>/dev/null | tr '\n' ' ')"
    for u in ligastats-mcp.service ligastats-diario.timer ligastats-backtest.timer; do
        printf '%-26s %s\n' "$u" "$(systemctl is-active $u 2>/dev/null || true)"
    done
    systemctl list-timers 'ligastats-*' --no-pager 2>/dev/null | head -5 || true
    if curl -fsS -m 5 "http://127.0.0.1:$PUERTO/salud" >/dev/null 2>&1; then verde "MCP local responde"; else amarillo "MCP local no responde"; fi
    if [[ -f /etc/letsencrypt/live/$DOMINIO/fullchain.pem ]]; then
        if [[ "$(curl -s -m 10 -o /dev/null -w '%{http_code}' "https://$DOMINIO/mcp" || true)" == "401" ]]; then
            verde "https://$DOMINIO/mcp publicado (pide clave, correcto)"
        else
            amarillo "https://$DOMINIO/mcp no responde como se espera"
        fi
    else
        amarillo "Sin certificado para $DOMINIO todavía"
    fi
    systemctl show ligastats.slice -p MemoryCurrent -p MemoryMax 2>/dev/null || true
}

# ----------------------------------------------------------------------------
https() {
    paso "Publicando https://$DOMINIO (sin tocar voceo.aitex.cl)"
    command -v nginx >/dev/null || fallar "nginx no está instalado"
    local ip_dns ips
    ip_dns="$(getent ahostsv4 "$DOMINIO" 2>/dev/null | awk 'NR==1{print $1}' || true)"
    ips="$(hostname -I)"
    if [[ -z "$ip_dns" ]]; then
        amarillo "El DNS de $DOMINIO aún no existe. Crea un registro A -> $(hostname -I | awk '{print $1}')"
        amarillo "y luego ejecuta: bash instalar.sh --https"
        return 0
    fi
    if ! grep -qw "$ip_dns" <<<"$ips"; then
        amarillo "$DOMINIO apunta a $ip_dns, que no es este servidor ($ips). Corrige el DNS y reintenta."
        return 0
    fi
    verde "DNS correcto: $DOMINIO -> $ip_dns"

    mkdir -p "$WEBROOT"
    local respaldo=""
    [[ -f "$NGINX_SITE" ]] && respaldo="$(mktemp)" && cp "$NGINX_SITE" "$respaldo"

    if [[ ! -f /etc/letsencrypt/live/$DOMINIO/fullchain.pem ]]; then
        cp "$ORIGEN/deploy/nginx/$DOMINIO.http.conf" "$NGINX_SITE"
        ln -sfn "$NGINX_SITE" "/etc/nginx/sites-enabled/$DOMINIO"
        if ! nginx -t 2>/tmp/ligastats-nginx.txt; then
            cat /tmp/ligastats-nginx.txt
            if [[ -n "$respaldo" ]]; then cp "$respaldo" "$NGINX_SITE"; else rm -f "$NGINX_SITE" "/etc/nginx/sites-enabled/$DOMINIO"; fi
            fallar "nginx -t falló; se dejó todo como estaba"
        fi
        systemctl reload nginx
        if ! command -v certbot >/dev/null; then
            apt-get install -y -q certbot >/dev/null
        fi
        local cuenta=()
        [[ -d /etc/letsencrypt/accounts ]] && [[ -n "$(ls -A /etc/letsencrypt/accounts 2>/dev/null)" ]] \
            || cuenta=(--register-unsafely-without-email)
        certbot certonly --webroot -w "$WEBROOT" -d "$DOMINIO" --non-interactive --agree-tos "${cuenta[@]}" \
            --deploy-hook "systemctl reload nginx" \
            || fallar "certbot no pudo emitir el certificado (¿puerto 80 abierto?)"
    fi

    cp "$ORIGEN/deploy/nginx/$DOMINIO.conf" "$NGINX_SITE"
    ln -sfn "$NGINX_SITE" "/etc/nginx/sites-enabled/$DOMINIO"
    if ! nginx -t 2>/tmp/ligastats-nginx.txt; then
        cat /tmp/ligastats-nginx.txt
        if [[ -n "$respaldo" ]]; then cp "$respaldo" "$NGINX_SITE"; else cp "$ORIGEN/deploy/nginx/$DOMINIO.http.conf" "$NGINX_SITE"; fi
        nginx -t && systemctl reload nginx
        fallar "nginx -t falló con la configuración HTTPS; se restauró la anterior"
    fi
    systemctl reload nginx
    sleep 1
    local codigo
    codigo="$(curl -s -m 10 -o /dev/null -w '%{http_code}' "https://$DOMINIO/mcp" || true)"
    if [[ "$codigo" == "401" ]]; then
        verde "https://$DOMINIO/mcp responde y exige clave (401 sin clave = correcto)"
    else
        amarillo "https://$DOMINIO/mcp respondió $codigo (se esperaba 401). Revisa: journalctl -u ligastats-mcp"
    fi
}

# ----------------------------------------------------------------------------
rollback() {
    paso "Volviendo a la versión anterior"
    local actual anterior
    actual="$(readlink -f $BASE/current)"
    anterior="$(ls -1d $BASE/releases/*/ | sed 's#/$##' | grep -v "^$actual$" | sort | tail -1)"
    [[ -n "$anterior" ]] || fallar "no hay versión anterior"
    ln -sfn "$anterior" "$BASE/current.tmp" && mv -Tf "$BASE/current.tmp" "$BASE/current"
    systemctl restart ligastats-mcp.service
    verde "Activa: $anterior (la base de datos no se toca)"
}

# ----------------------------------------------------------------------------
instalar() {
    paso "1/8 Comprobaciones"
    . /etc/os-release
    echo "Sistema: $PRETTY_NAME | Python: $(python3 --version)"
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || fallar "se requiere Python 3.10+"
    local ocupado
    ocupado="$(ss -tlnpH "sport = :$PUERTO" 2>/dev/null || true)"
    if [[ -n "$ocupado" ]] && ! grep -q "$USUARIO\|python" <<<"$ocupado"; then
        fallar "el puerto $PUERTO está en uso por otro servicio: $ocupado"
    fi
    if [[ -n "$ocupado" ]] && ! systemctl is-active --quiet ligastats-mcp.service; then
        fallar "el puerto $PUERTO está en uso por otro proceso python: $ocupado"
    fi
    verde "OK"

    paso "2/8 Paquetes del sistema"
    local faltan=()
    for p in python3-venv python3-pip sqlite3 curl; do dpkg -s "$p" >/dev/null 2>&1 || faltan+=("$p"); done
    if ((${#faltan[@]})); then
        apt-get update -q >/dev/null && apt-get install -y -q "${faltan[@]}" >/dev/null
        verde "Instalados: ${faltan[*]}"
    else
        verde "Ya estaban"
    fi

    paso "3/8 Usuario y carpetas"
    id "$USUARIO" >/dev/null 2>&1 || useradd --system --home-dir "$BASE" --shell /usr/sbin/nologin "$USUARIO"
    mkdir -p "$BASE/releases" "$SHARED/data" "$SHARED/logs"
    chown "$USUARIO:$USUARIO" "$BASE" "$SHARED" "$SHARED/data" "$SHARED/logs"
    chmod 750 "$BASE" "$SHARED"
    if [[ ! -f "$ENVFILE" ]]; then
        umask 077
        cat > "$ENVFILE" <<EOF
LIGASTATS_DATA=$SHARED/data
LIGASTATS_MCP_HOST=127.0.0.1
LIGASTATS_MCP_PORT=$PUERTO
LIGASTATS_MCP_HOSTS=$DOMINIO,127.0.0.1:$PUERTO,localhost:$PUERTO
LIGASTATS_MCP_TOKEN=$(openssl rand -hex 32)
EOF
        umask 022
        verde "Clave del MCP generada"
    fi
    chown "$USUARIO:$USUARIO" "$ENVFILE"; chmod 600 "$ENVFILE"
    verde "Usuario $USUARIO, carpeta $BASE"

    paso "4/8 Nueva versión del código"
    local version destino
    version="$(date +%Y%m%d-%H%M%S)"
    destino="$BASE/releases/$version"
    mkdir -p "$destino"
    cp -r "$ORIGEN/ligastats" "$ORIGEN/requirements.txt" "$ORIGEN/instalar.sh" "$ORIGEN/deploy" "$destino/"
    for f in VERSION VERSION_COMMIT; do [[ -f "$ORIGEN/$f" ]] && cp "$ORIGEN/$f" "$destino/"; done
    find "$destino" -name '__pycache__' -prune -exec rm -rf {} +
    python3 -m compileall -q "$destino/ligastats" >/dev/null || true
    chown -R root:"$USUARIO" "$destino"; chmod -R g+rX,o-rwx "$destino"
    verde "$destino"

    paso "5/8 Entorno Python (puede tardar un par de minutos la primera vez)"
    [[ -x "$VENV/bin/python" ]] || python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q --upgrade pip
    "$VENV/bin/pip" install -q -r "$destino/requirements.txt"
    chown -R root:"$USUARIO" "$VENV"; chmod -R g+rX,o-rwx "$VENV"
    verde "$("$VENV/bin/python" -c 'import pandas, numpy, scipy, mcp; print("pandas", pandas.__version__, "| numpy", numpy.__version__, "| scipy", scipy.__version__)')"

    paso "6/8 Prueba de la versión nueva antes de activarla"
    # shellcheck disable=SC2016
    runuser -u "$USUARIO" -- bash -c 'set -a; . "$1"; set +a; cd "$2" && LIGASTATS_HOME="$2" "$3" -c \
        "from ligastats import db, mcp_server, actualizar; db.inicializar(); print(\"importa y abre la base: OK\")"' \
        _ "$ENVFILE" "$destino" "$VENV/bin/python" \
        || fallar "la versión nueva no pasó la prueba; la versión activa no se tocó"
    verde "OK"

    paso "7/8 Activando versión y servicios"
    ln -sfn "$destino" "$BASE/current.tmp" && mv -Tf "$BASE/current.tmp" "$BASE/current"
    cp "$ORIGEN"/deploy/systemd/ligastats* /etc/systemd/system/
    # script que usa GitHub Actions para desplegar (se reemplaza de forma atómica)
    mkdir -p "$BASE/bin"
    install -o root -g root -m 755 "$ORIGEN/deploy/bin/desplegar" "$BASE/bin/.desplegar.nuevo"
    mv -f "$BASE/bin/.desplegar.nuevo" "$BASE/bin/desplegar"
    systemctl daemon-reload
    systemctl enable --quiet ligastats-mcp.service ligastats-diario.timer ligastats-backtest.timer
    systemctl restart ligastats-mcp.service
    systemctl start ligastats-diario.timer ligastats-backtest.timer
    local ok=0
    for _ in $(seq 1 20); do
        if curl -fsS -m 2 "http://127.0.0.1:$PUERTO/salud" >/dev/null 2>&1; then ok=1; break; fi
        sleep 1
    done
    if ((ok)); then
        verde "MCP escuchando en 127.0.0.1:$PUERTO"
    else
        journalctl -u ligastats-mcp.service -n 30 --no-pager
        rojo "El MCP no arrancó; volviendo a la versión anterior"
        rollback || true
        exit 1
    fi
    # se conservan las 3 últimas versiones
    ls -1d "$BASE"/releases/*/ | sort | head -n -3 | xargs -r rm -rf

    paso "8/8 Primera carga de datos"
    local hay
    hay="$(runuser -u "$USUARIO" -- sqlite3 "$SHARED/data/ligastats.db" 'SELECT COUNT(*) FROM partidos' 2>/dev/null || echo 0)"
    if [[ "$hay" == "0" ]]; then
        echo "Descargando 6 temporadas x 7 ligas (1 a 3 minutos)..."
        systemctl start ligastats-diario.service || amarillo "La carga inicial falló: journalctl -u ligastats-diario -n 50"
        echo "Lanzando el primer backtest en segundo plano..."
        systemctl start --no-block ligastats-backtest.service
    else
        echo "Ya hay $hay partidos cargados; no se repite la carga inicial."
    fi
    runuser -u "$USUARIO" -- sqlite3 -header -column "$SHARED/data/ligastats.db" \
        "SELECT liga, COUNT(*) partidos, MIN(fecha) desde, MAX(fecha) hasta,
                SUM(cl IS NOT NULL) corners, SUM(al IS NOT NULL) tarjetas, SUM(arbitro IS NOT NULL) arbitro,
                SUM(c_o25 IS NOT NULL) cuotas_2_5
         FROM partidos GROUP BY liga;" 2>/dev/null || true

    https

    paso "Listo"
    estado
    echo
    verde "Datos para agregar el conector en Claude (Customize > Connectors > Add > Custom > Web):"
    echo "  URL     : https://$DOMINIO/mcp"
    if [[ -t 1 ]]; then
        echo "  Header  : Authorization: Bearer $(grep LIGASTATS_MCP_TOKEN "$ENVFILE" | cut -d= -f2)"
        amarillo "Guarda la clave en un lugar seguro y no la compartas."
    else
        # despliegue automático: la clave nunca se imprime en los registros de GitHub
        echo "  Header  : Authorization: Bearer (oculta; en el servidor: grep TOKEN $ENVFILE)"
    fi
}

case "${1:-}" in
    --https) https ;;
    --rollback) rollback ;;
    --estado) estado ;;
    "") instalar ;;
    *) echo "uso: bash instalar.sh [--https|--rollback|--estado]"; exit 1 ;;
esac
