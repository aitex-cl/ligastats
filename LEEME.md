# LigaStats v1.0 — proyecciones de goles, tarjetas y corners

## Instalación en el VPS (voceo.aitex.cl)

```bash
# desde tu PC
scp ligastats-v1.0.tar.gz root@154.12.231.28:/root/

# en el servidor
cd /root && tar xzf ligastats-v1.0.tar.gz && cd ligastats
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
