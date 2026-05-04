#!/usr/bin/env bash
# install.sh — one-shot bootstrap for livechat-mcp.
#
# Installs portaudio, uv, Python deps, drops the wizard binary into
# ~/.local/bin, and finally launches the interactive setup wizard.
#
# Tested on macOS (Apple Silicon + Intel) and Linux (apt / dnf / pacman).
# Windows users: run this from inside WSL2 — the lockfile module relies on
# POSIX fcntl and is not portable to native Windows.

set -eu

# --- styling ---------------------------------------------------------------

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

# --- detect OS -------------------------------------------------------------

OS="$(uname -s)"
case "$OS" in
  Darwin)                          PLATFORM="macos" ;;
  Linux)                           PLATFORM="linux" ;;
  MINGW*|MSYS*|CYGWIN*|Windows_NT) PLATFORM="windows" ;;
  *)
    err "Unsupported OS: $OS"
    err "If you are on native Windows PowerShell, run ${B}install.ps1${R} instead."
    exit 1
    ;;
esac

cd "$(dirname "$0")"
if [ ! -f pyproject.toml ]; then
  err "pyproject.toml not found in $(pwd)."
  err "Run install.sh from the root of the livechat-mcp project directory."
  exit 1
fi

step "Bootstrapping livechat-mcp on ${PLATFORM}"

# --- step 1: portaudio -----------------------------------------------------

step "1/4 — portaudio (sounddevice dependency)"

if [ "$PLATFORM" = "macos" ]; then
  if ! command -v brew >/dev/null 2>&1; then
    err "Homebrew not found. Install from https://brew.sh and re-run."
    exit 1
  fi
  if brew list portaudio >/dev/null 2>&1; then
    ok "portaudio already installed (brew)"
  else
    brew install portaudio
    ok "portaudio installed"
  fi
elif [ "$PLATFORM" = "linux" ]; then
  if pkg-config --exists portaudio-2.0 2>/dev/null; then
    ok "portaudio already installed"
  else
    if command -v apt-get >/dev/null 2>&1; then
      warn "Installing portaudio via apt (sudo required)"
      sudo apt-get update
      sudo apt-get install -y libportaudio2 portaudio19-dev
    elif command -v dnf >/dev/null 2>&1; then
      warn "Installing portaudio via dnf (sudo required)"
      sudo dnf install -y portaudio portaudio-devel
    elif command -v pacman >/dev/null 2>&1; then
      warn "Installing portaudio via pacman (sudo required)"
      sudo pacman -S --noconfirm portaudio
    elif command -v zypper >/dev/null 2>&1; then
      warn "Installing portaudio via zypper (sudo required)"
      sudo zypper install -y portaudio-devel
    else
      err "No supported package manager found (apt/dnf/pacman/zypper)."
      err "Install portaudio manually, then re-run this script."
      exit 1
    fi
    ok "portaudio installed"
  fi
elif [ "$PLATFORM" = "windows" ]; then
  ok "portaudio bundled with sounddevice wheels on Windows — nothing to install"
fi

# --- step 2: uv ------------------------------------------------------------

step "2/4 — uv (Python project manager)"

if command -v uv >/dev/null 2>&1; then
  ok "uv already installed ($(uv --version))"
else
  warn "uv not found, installing from astral.sh"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # uv typically lands in ~/.local/bin; older versions used ~/.cargo/bin.
  for d in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
    if [ -x "$d/uv" ]; then
      export PATH="$d:$PATH"
      break
    fi
  done
  if ! command -v uv >/dev/null 2>&1; then
    err "uv installed but not on PATH. Add ~/.local/bin to your PATH and re-run."
    exit 1
  fi
  ok "uv installed ($(uv --version))"
fi

# --- step 3: Python dependencies ------------------------------------------

step "3/4 — Python dependencies (this can take a minute on first run)"

uv sync
ok "Project dependencies installed into .venv/"

# --- step 4: wizard binary -------------------------------------------------

step "4/4 — installing setup wizard"

mkdir -p "$HOME/.local/bin"
install -m 0755 bin/livechat-mcp "$HOME/.local/bin/livechat-mcp"
ok "Wizard installed to ${D}~/.local/bin/livechat-mcp${R}"

if ! printf ":%s:" "$PATH" | grep -q ":$HOME/.local/bin:"; then
  warn "~/.local/bin is not on your PATH."
  warn "Add this to your shell rc (then re-open your terminal):"
  printf "      ${B}export PATH=\"\$HOME/.local/bin:\$PATH\"${R}\n"
fi

# --- launch wizard ---------------------------------------------------------

printf "\n${B}Bootstrap complete.${R} Launching the setup wizard...\n"
printf "${D}(You can re-run it any time: ${R}${CYN}livechat-mcp setup${R}${D})${R}\n"

exec "$HOME/.local/bin/livechat-mcp" setup
