#!/bin/bash
# install.sh — first-run bootstrap for superdarn-sounder (Pattern A editable install)
#
# Usage: sudo ./scripts/install.sh [--pull] [--yes]
#
#   1. Ensures uv + the sibling repos (hamsci-dsp, ka9q-python)
#   2. Creates service user superdarn:superdarn
#   3. Creates venv at /opt/git/sigmond/superdarn-sounder/venv (editable sync)
#   4. Renders config template (non-destructive)
#   5. Installs systemd unit template
#
# Idempotent: safe to re-run.

set -euo pipefail

SERVICE_USER="superdarn"
SERVICE_GROUP="superdarn"
REPO_SOURCE="/opt/git/sigmond/superdarn-sounder"
VENV_DIR="/opt/git/sigmond/superdarn-sounder/venv"
CONFIG_DIR="/etc/superdarn-sounder"
CONFIG_FILE="${CONFIG_DIR}/superdarn-sounder-config.toml"
SPOOL_DIR="/var/lib/superdarn-sounder"
LOG_DIR="/var/log/superdarn-sounder"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

ui_info()  { echo "[INFO]  $*"; }
ui_warn()  { echo "[WARN]  $*" >&2; }
ui_error() { echo "[ERROR] $*" >&2; }

DO_PULL=false
for arg in "$@"; do
    case "$arg" in
        --pull) DO_PULL=true ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    ui_error "Must run as root (sudo)"
    exit 1
fi

# --- ensure uv (canonical sigmond-suite installer) ---
_ENSURE_UV_SH="/opt/git/sigmond/sigmond/scripts/install/ensure_uv.sh"
if [[ -r "$_ENSURE_UV_SH" ]]; then
    # shellcheck source=/dev/null
    source "$_ENSURE_UV_SH"
else
    _ensure_uv() {
        if command -v uv >/dev/null 2>&1; then return 0; fi
        ui_info "uv not found -- installing system-wide to /usr/local/bin"
        command -v curl >/dev/null || { ui_error "curl not found (apt install curl)"; return 1; }
        curl -LsSf https://astral.sh/uv/install.sh | env XDG_BIN_HOME=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
        command -v uv >/dev/null || { ui_error "uv still not on PATH"; return 1; }
    }
fi
_ensure_uv || { ui_error "_ensure_uv failed"; exit 1; }

# --- ensure sibling repos (path-based editable deps in pyproject [tool.uv.sources]) ---
mkdir -p /opt/git/sigmond
for sib in hamsci-dsp ka9q-python; do
    if [[ ! -f "/opt/git/sigmond/$sib/pyproject.toml" ]]; then
        ui_info "sibling $sib not present -- cloning"
        git clone "https://github.com/mijahauan/$sib" "/opt/git/sigmond/$sib" \
            || { ui_error "Failed to clone $sib"; exit 1; }
    fi
done

# --- service user ---
if ! id -u "$SERVICE_USER" &>/dev/null; then
    ui_info "Creating service user $SERVICE_USER"
    useradd --system --shell /usr/sbin/nologin \
            --home-dir /nonexistent --no-create-home "$SERVICE_USER"
fi

# --- repo + venv ---
if [[ ! -d "$REPO_SOURCE" ]] && [[ ! -L "$REPO_SOURCE" ]]; then
    ui_info "Linking $REPO_ROOT -> $REPO_SOURCE"
    mkdir -p "$(dirname "$REPO_SOURCE")"
    ln -sfn "$REPO_ROOT" "$REPO_SOURCE"
fi

if $DO_PULL; then
    ui_info "Pulling latest from origin"
    git -C "$REPO_SOURCE" pull --ff-only
fi

if [[ ! -d "$VENV_DIR" ]]; then
    ui_info "Creating venv at $VENV_DIR"
    uv venv "$VENV_DIR" --python 3.11 --seed --quiet
fi

ui_info "Syncing superdarn-sounder + siblings (editable) into $VENV_DIR"
UV_PROJECT_ENVIRONMENT="$VENV_DIR" \
    uv sync --project "$REPO_SOURCE" --no-dev --quiet

# sigmond is lazy-imported for the SQLite sink (no-op fallback when absent).
if [[ -d /opt/git/sigmond/sigmond ]]; then
    ui_info "Installing sigmond (editable) into venv"
    uv pip install --quiet --python "$VENV_DIR/bin/python3" -e /opt/git/sigmond/sigmond
fi

if ! sudo -u "$SERVICE_USER" "$VENV_DIR/bin/python3" -c 'import superdarn_sounder' 2>/dev/null; then
    ui_error "Post-install verify failed: $SERVICE_USER cannot import superdarn_sounder"
    exit 1
fi
ui_info "Post-install verify OK"

# --- config ---
mkdir -p "$CONFIG_DIR"
if [[ ! -f "$CONFIG_FILE" ]]; then
    ui_info "Rendering config template -> $CONFIG_FILE"
    cp "$REPO_SOURCE/config/superdarn-sounder-config.toml.template" "$CONFIG_FILE"
    ui_warn "Edit $CONFIG_FILE: callsign, grid, receiver_lat/lon, [[radiod]] status + [[radiod.band]]"
else
    ui_info "Config exists at $CONFIG_FILE — not overwriting"
fi

# --- directories ---
for dir in "$SPOOL_DIR" "$LOG_DIR"; do
    mkdir -p "$dir"
    chown "$SERVICE_USER:$SERVICE_GROUP" "$dir"
done

# --- systemd ---
ui_info "Installing systemd unit template"
install -o root -g root -m 644 \
    "$REPO_SOURCE/systemd/superdarn-sounder@.service" \
    /etc/systemd/system/superdarn-sounder@.service
systemctl daemon-reload

ui_info "Install complete. Edit $CONFIG_FILE then enable an instance with:"
ui_info "  sudo systemctl enable --now superdarn-sounder@<radiod-id>"
