#!/bin/bash
# Kernel deployment script for Debian 12
# Usage: sudo bash setup.sh
set -euo pipefail

APP_DIR=/opt/kernel
APP_USER=kernel

if ! id -u "$APP_USER" &>/dev/null; then
    useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

mkdir -p "$APP_DIR/data"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

if ! command -v uv &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

cd "$APP_DIR"
sudo -u "$APP_USER" uv sync --frozen --python 3.11

cp "$APP_DIR/deploy/kernel.service" /etc/systemd/system/kernel.service
systemctl daemon-reload
systemctl enable kernel

echo "Setup complete. Next steps:"
echo "  1. Copy config.toml, SOUL.md to $APP_DIR/"
echo "  2. Edit $APP_DIR/config.toml with your tokens/keys"
echo "  3. systemctl start kernel"
echo "  4. journalctl -u kernel -f"
