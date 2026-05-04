#!/usr/bin/env bash
# bootstrap.sh — one-shot remote installer for livechat-mcp.
#
#   curl -LsSf https://raw.githubusercontent.com/brunocramos/livechat-mcp/main/bootstrap.sh | bash
#
# Clones the repo to ~/.local/share/livechat-mcp (configurable via
# LIVECHAT_INSTALL_DIR) and runs install.sh, which installs portaudio,
# uv, and Python deps before launching the interactive setup wizard.

set -eu

REPO_URL="${LIVECHAT_REPO_URL:-https://github.com/brunocramos/livechat-mcp.git}"
REPO_BRANCH="${LIVECHAT_REPO_BRANCH:-main}"
INSTALL_DIR="${LIVECHAT_INSTALL_DIR:-$HOME/.local/share/livechat-mcp}"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  B=$'\033[1m'; D=$'\033[2m'; R=$'\033[0m'
  GREEN=$'\033[32m'; YEL=$'\033[33m'; RED=$'\033[31m'; CYN=$'\033[36m'
else
  B=""; D=""; R=""; GREEN=""; YEL=""; RED=""; CYN=""
fi

step()  { printf "\n${B}❯ %s${R}\n" "$1"; }
ok()    { printf "  ${GREEN}✓${R} %s\n" "$1"; }
warn()  { printf "  ${YEL}!${R} %s\n" "$1"; }
err()   { printf "  ${RED}✗${R} %s\n" "$1" >&2; }

step "Bootstrapping livechat-mcp"

if ! command -v git >/dev/null 2>&1; then
  err "git not found. Install git and re-run."
  exit 1
fi

mkdir -p "$(dirname "$INSTALL_DIR")"

if [ -d "$INSTALL_DIR/.git" ]; then
  warn "Repo already at ${INSTALL_DIR} — fetching latest"
  git -C "$INSTALL_DIR" fetch --quiet origin "$REPO_BRANCH"
  git -C "$INSTALL_DIR" checkout --quiet "$REPO_BRANCH"
  git -C "$INSTALL_DIR" reset --hard --quiet "origin/$REPO_BRANCH"
else
  warn "Cloning ${REPO_URL} → ${INSTALL_DIR}"
  git clone --quiet --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi
ok "Source at ${INSTALL_DIR}"

cd "$INSTALL_DIR"
exec ./install.sh
