#!/usr/bin/env bash
# Installs keyd (from source, it is not in the Fedora repos) and enables the
# TTS hotkey configuration.
#
# Run as root:   sudo ./install-keyd.sh
#
# Steps:
#   1. install build tools (gcc, make, git)
#   2. clone, build and install keyd from GitHub
#   3. copy default.conf (next to this script) to /etc/keyd/default.conf
#   4. enable and start the keyd service, load the configuration
#
# Undo / emergency:
#   sudo systemctl disable --now keyd      # turn keyd off (keyboard back to normal)
#   If the keyboard ever gets stuck: log in with a USB keyboard or via SSH, or
#   switch to a text console (Ctrl+Alt+F3) and run the command above.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Please run with sudo:  sudo $0" >&2
    exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
CONF_SRC="$HERE/default.conf"
if [[ ! -f "$CONF_SRC" ]]; then
    echo "default.conf not found next to the script ($CONF_SRC)" >&2
    exit 1
fi

echo "==> 1/4  Installing build tools"
if command -v dnf >/dev/null 2>&1; then
    dnf install -y gcc make git
else
    apt-get install -y gcc make git
fi

echo "==> 2/4  Building and installing keyd"
BUILD="$(mktemp -d)"
git clone --depth 1 https://github.com/rvaiya/keyd "$BUILD/keyd"
make -C "$BUILD/keyd"
make -C "$BUILD/keyd" install
rm -rf "$BUILD"

echo "==> 3/4  Configuration to /etc/keyd/default.conf"
mkdir -p /etc/keyd
install -m 0644 "$CONF_SRC" /etc/keyd/default.conf

echo "==> 4/4  Enabling, hardening and loading the service"
# Unlimited auto-restart: keyd always comes back after a crash.
mkdir -p /etc/systemd/system/keyd.service.d
if [[ -f "$HERE/restart.conf" ]]; then
    install -m 0644 "$HERE/restart.conf" /etc/systemd/system/keyd.service.d/restart.conf
else
    printf '[Unit]\nStartLimitIntervalSec=0\n[Service]\nRestart=always\nRestartSec=1\n' \
        > /etc/systemd/system/keyd.service.d/restart.conf
fi
systemctl daemon-reload
systemctl enable --now keyd
keyd reload || true

echo
echo "Done. Status:"
systemctl --no-pager --full status keyd | head -n 8 || true
echo
echo "Test (shows remapped keys live, quit with q):  sudo keyd monitor"
echo "Then log out and back in once so KDE loads the hyper shortcuts."
