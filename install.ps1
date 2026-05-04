# install.ps1 — Windows bootstrap for livechat-mcp.
#
# Run from PowerShell at the repo root:
#   .\install.ps1
#
# The wizard (livechat-mcp setup) is a bash script. On Windows we run it via
# Git Bash. If Git is not installed, the script offers to install it via winget.

$ErrorActionPreference = "Stop"

function Step($msg) { Write-Host "`n❯ $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }
function Err($msg)  { Write-Host "  ✗ $msg" -ForegroundColor Red }

Set-Location $PSScriptRoot
if (-not (Test-Path "pyproject.toml")) {
    Err "pyproject.toml not found in $PSScriptRoot."
    Err "Run install.ps1 from the root of the livechat-mcp project."
    exit 1
}

Step "Bootstrapping livechat-mcp on Windows"

# --- step 1: portaudio -----------------------------------------------------
Step "1/4 — portaudio"
Ok "portaudio is bundled in the sounddevice wheel on Windows — nothing to install"

# --- step 2: uv ------------------------------------------------------------
Step "2/4 — uv (Python project manager)"

$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if ($uvCmd) {
    Ok "uv already installed ($((& uv --version)))"
} else {
    Warn "uv not found, installing via the official PowerShell installer"
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

    # uv lands in %USERPROFILE%\.local\bin on Windows. Add to PATH for this session.
    $uvBin = Join-Path $HOME ".local\bin"
    if (Test-Path (Join-Path $uvBin "uv.exe")) {
        $env:PATH = "$uvBin;$env:PATH"
    }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Err "uv installed but not on PATH. Open a new PowerShell window and re-run."
        exit 1
    }
    Ok "uv installed ($((& uv --version)))"
}

# --- step 3: project deps --------------------------------------------------
Step "3/4 — Python dependencies (this can take a minute on first run)"
& uv sync
if ($LASTEXITCODE -ne 0) { Err "uv sync failed."; exit 1 }
Ok "Project dependencies installed into .venv\"

# --- step 4: launch wizard via Git Bash ------------------------------------
Step "4/4 — install setup wizard"

$bash = Get-Command bash -ErrorAction SilentlyContinue
if (-not $bash) {
    Warn "Git Bash not found. The interactive setup wizard is a bash script."
    Write-Host ""
    Write-Host "  Install Git for Windows (which includes Git Bash):" -ForegroundColor White
    Write-Host "    winget install --id Git.Git -e --source winget" -ForegroundColor White
    Write-Host ""
    Write-Host "  Then re-run install.ps1, or open Git Bash and run:" -ForegroundColor White
    Write-Host "    ./install.sh" -ForegroundColor White
    exit 1
}

# Drop the wizard binary somewhere on PATH for Git Bash users.
$localBin = Join-Path $HOME ".local\bin"
New-Item -ItemType Directory -Force -Path $localBin | Out-Null
Copy-Item -Force "bin\livechat-mcp" (Join-Path $localBin "livechat-mcp")
Ok "Wizard installed to $localBin\livechat-mcp"

# Convert the path for bash and exec the wizard.
$bashPath = (& $bash.Path -c "cygpath -u '$localBin/livechat-mcp'").Trim()

Write-Host ""
Write-Host "Bootstrap complete. Launching the setup wizard via Git Bash..." -ForegroundColor White
Write-Host "(You can re-run it any time: bash -c '~/.local/bin/livechat-mcp setup')" -ForegroundColor DarkGray

& $bash.Path "$bashPath" setup
exit $LASTEXITCODE
