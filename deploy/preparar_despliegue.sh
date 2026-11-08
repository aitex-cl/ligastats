#!/usr/bin/env bash
# =============================================================================
#  LigaStats - preparación ÚNICA del despliegue automático desde GitHub.
#  Ejecutar una sola vez, como root, en el VPS:  bash deploy/preparar_despliegue.sh
#
#  Crea el usuario 'despliegue', que por SSH solo puede ejecutar
#  /opt/ligastats/bin/desplegar (sin terminal, sin túneles), y muestra los
#  tres valores que hay que cargar como secretos en el repositorio de GitHub.
# =============================================================================
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "ejecuta como root"; exit 1; }
USUARIO=despliegue
SCRIPT=/opt/ligastats/bin/desplegar
[[ -x "$SCRIPT" ]] || { echo "Falta $SCRIPT: instala primero LigaStats con 'bash instalar.sh'"; exit 1; }

id "$USUARIO" >/dev/null 2>&1 || useradd --system --create-home --home-dir /home/$USUARIO --shell /bin/bash "$USUARIO"
passwd -l "$USUARIO" >/dev/null

# Llave nueva (si se vuelve a ejecutar, la anterior deja de servir)
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
ssh-keygen -q -t ed25519 -N "" -C "github-actions-ligastats" -f "$tmp/llave"
install -d -o "$USUARIO" -g "$USUARIO" -m 700 /home/$USUARIO/.ssh
printf 'restrict,command="sudo -n %s \\"$SSH_ORIGINAL_COMMAND\\"" %s\n' "$SCRIPT" "$(cat "$tmp/llave.pub")" \
    > /home/$USUARIO/.ssh/authorized_keys
chown "$USUARIO:$USUARIO" /home/$USUARIO/.ssh/authorized_keys
chmod 600 /home/$USUARIO/.ssh/authorized_keys

# sudo solo para el script de despliegue
echo "$USUARIO ALL=(root) NOPASSWD: $SCRIPT" > /etc/sudoers.d/ligastats-despliegue.tmp
chmod 440 /etc/sudoers.d/ligastats-despliegue.tmp
visudo -cf /etc/sudoers.d/ligastats-despliegue.tmp >/dev/null
mv /etc/sudoers.d/ligastats-despliegue.tmp /etc/sudoers.d/ligastats-despliegue

# Avisos de configuración de SSH que podrían bloquear al usuario
if grep -RqsEi '^\s*(AllowUsers|AllowGroups)\b' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/; then
    echo
    echo "ATENCIÓN: sshd tiene AllowUsers/AllowGroups. Agrega '$USUARIO' o el despliegue no podrá entrar:"
    grep -RsEi '^\s*(AllowUsers|AllowGroups)\b' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/
fi

IP="$(hostname -I | awk '{print $1}')"
echo
echo "==================== SECRETOS PARA GITHUB ===================="
echo "Repositorio > Settings > Secrets and variables > Actions > New repository secret"
echo
echo "1) Nombre: VPS_HOST"
echo "$IP"
echo
echo "2) Nombre: VPS_KNOWN_HOSTS"
for k in /etc/ssh/ssh_host_ed25519_key.pub /etc/ssh/ssh_host_ecdsa_key.pub; do
    [[ -f "$k" ]] && echo "$IP $(cut -d' ' -f1,2 "$k")"
done
echo
echo "3) Nombre: VPS_SSH_KEY   (copiar completo, incluidas las líneas BEGIN y END)"
cat "$tmp/llave"
echo "=============================================================="
echo "La llave privada no queda guardada en el servidor. Si la pierdes, vuelve a ejecutar este script."
