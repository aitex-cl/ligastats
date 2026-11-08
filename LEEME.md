# LigaStats v1.1 — proyecciones de tarjetas, corners y goles

## Instalación en el VPS (voceo.aitex.cl)

```bash
# desde tu PC
scp ligastats-v1.1.tar.gz root@154.12.231.28:/root/

# en el servidor
cd /root && rm -rf ligastats && tar xzf ligastats-v1.1.tar.gz && cd ligastats
bash instalar.sh
```

El instalador hace todo y al final muestra la URL y la clave del conector MCP.
Si el DNS de `stat.aitex.cl` todavía no existe, crea un registro **A → 154.12.231.28**
y luego ejecuta `bash instalar.sh --https`.

| Comando | Qué hace |
|---|---|
| `bash instalar.sh` | instala o actualiza (crea una versión nueva) |
| `bash instalar.sh --https` | publica stat.aitex.cl con certificado |
| `bash instalar.sh --rollback` | vuelve a la versión anterior (la base no se toca) |
| `bash instalar.sh --estado` | muestra el estado sin cambiar nada |

## Qué queda en el servidor

- Usuario `ligastats`, carpeta `/opt/ligastats` (versiones en `releases/`, activa en `current`,
  datos y clave en `shared/`).
- Límite de 2 GB de RAM y 2 núcleos con prioridad baja (`ligastats.slice`).
- `ligastats-mcp.service`: MCP solo en 127.0.0.1:8010; nginx lo publica en `https://stat.aitex.cl/mcp`
  con clave. Cualquier otra ruta responde 404.
- `ligastats-diario.timer`: 08:15, 14:15 y 20:15 hora de Chile (descarga, carga, liquida y genera señales).
- `ligastats-backtest.timer`: lunes 04:30 hora de Chile.
- No toca voceo, Zabbix, PostgreSQL, Node-RED ni la configuración existente de nginx.

## Conector en Claude

Customize → Connectors → Add → Custom → Web
- URL: `https://stat.aitex.cl/mcp`
- Autenticación: "No sign in" + encabezado `Authorization: Bearer <clave>`

## Comandos útiles

```bash
journalctl -u ligastats-mcp -n 50          # registro del MCP
journalctl -u ligastats-diario -n 50       # última actualización
systemctl list-timers 'ligastats-*'        # próximas ejecuciones
systemctl start ligastats-diario           # actualizar ahora
```

## Despliegue automático (GitHub → VPS)

Repositorio `aitex-cl/ligastats`. Cada merge a `main` corre las pruebas y, si pasan, despliega.

Preparación única en el VPS (después de instalar la v1.1):
```bash
bash deploy/preparar_despliegue.sh
```
Muestra tres valores para cargar en GitHub (Settings → Secrets and variables → Actions):
`VPS_HOST`, `VPS_KNOWN_HOSTS` y `VPS_SSH_KEY`.

- El usuario `despliegue` solo puede ejecutar `/opt/ligastats/bin/desplegar` (sin terminal ni túneles).
- Acepta únicamente: un commit (instala ese código), `rollback` o `estado`.
- Desde GitHub → Actions → "LigaStats CI/CD" → Run workflow se puede pedir `rollback` o `estado`.
- La clave del MCP nunca se imprime en los registros de GitHub.
- Registro de despliegues en el servidor: `/opt/ligastats/shared/logs/despliegues.log`.

## Novedades v1.1
- Foco en tarjetas (sobre todo "menos"); corners en observación; goles solo como proyección.
- Candidatas desde 55 %; cuota mínima = max(cuota justa + 5 %, piso 1,70).
- Métricas de acierto contra el mínimo que exige la cuota y probabilidad de que sea suerte.
- Parámetros ajustables desde Claude (`ver_parametros`, `ajustar_parametro`), con historial.

## Panel web (https://stat.aitex.cl/)
- Usuario `eduardo`; la clave se genera en la primera instalación y queda en el servidor:
  `grep WEB_CLAVE /opt/ligastats/shared/config.env`
- **Hoy:** candidatas de hoy y mañana con el foco aplicado (mercado principal, lados y ligas excluidas
  ajustables desde Claude) y la cuota desde la que conviene apostar (incluye el piso).
- **Resultados:** candidatas ya jugadas, acertadas o falladas, con acierto contra el mínimo exigido
  y probabilidad de que sea suerte; por liga y en detalle.
- **Mis apuestas:** apuestas registradas, resultado, acierto y CLV.
- **Hoy:** una tarjeta por partido con tres bloques (Tarjetas, Corners, Goles). En cada uno: selector de
  línea (★ = candidata), cuotas de la casa para "menos" y "más", veredicto por lado y probabilidad de la casa
  sin margen. **Guardar cuotas** registra todo el partido aunque no se apueste; **Apostar menos/más** registra
  la apuesta. Luego, botones **Ganada / Perdida / Anulada** (también en Mis apuestas); el resultado manual manda.
- **Resultados → A cuotas reales de la casa:** simula 1 unidad por línea jugada con cuotas guardadas
  (solo lados que convenían / siempre el lado del foco) y compara modelo vs. casa sin margen.
