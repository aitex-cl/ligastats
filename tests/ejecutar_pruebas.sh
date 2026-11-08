#!/usr/bin/env bash
# Pruebas completas de LigaStats (las corre GitHub Actions en cada cambio).
set -euo pipefail
cd "$(dirname "$0")/.."
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
PY="${PYTHON:-python}"

echo "== Sintaxis de scripts"
bash -n instalar.sh deploy/bin/desplegar deploy/preparar_despliegue.sh
if command -v shellcheck >/dev/null; then
    shellcheck -S warning instalar.sh deploy/bin/desplegar deploy/preparar_despliegue.sh
fi
if command -v systemd-analyze >/dev/null; then
    for t in deploy/systemd/*.timer; do
        cal="$(grep -Po '^OnCalendar=\K.*' "$t")"
        systemd-analyze calendar "$cal" >/dev/null
    done
fi

echo "== Datos de prueba (espejo de football-data en GitHub)"
mkdir -p "$TMP/mirror"
for lg in premier-league la-liga serie-a bundesliga ligue-1; do
    for s in 2021 2122 2223 2324 2425; do
        curl -fsS --retry 3 -o "$TMP/mirror/${lg}_${s}.csv" \
            "https://raw.githubusercontent.com/datasets/football-datasets/main/datasets/${lg}/season-${s}.csv"
    done
done
"$PY" tests/preparar_datos.py "$TMP/mirror" "$TMP/sitio" 2025-05-17

echo "== Prueba de punta a punta"
"$PY" tests/e2e.py "$TMP/sitio" "$TMP/datos"

echo "== Prueba del servidor MCP"
"$PY" tests/prueba_mcp.py "$TMP/datos"
echo "TODAS LAS PRUEBAS OK"
