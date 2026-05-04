# bootstrap.ps1 — one-shot remote installer for livechat-mcp on Windows.
#
#   irm https://raw.githubusercontent.com/brunocramos/livechat-mcp/main/bootstrap.ps1 | iex
#
# Clones the repo to %USERPROFILE%\.local\share\livechat-mcp (configurable
# via $env:LIVECHAT_INSTALL_DIR) and runs install.ps1.

$ErrorActionPreference = "Stop"

$repoUrl    = if ($env:LIVECHAT_REPO_URL)    { $env:LIVECHAT_REPO_URL }    else { "https://github.com/brunocramos/livechat-mcp.git" }
$repoBranch = if ($env:LIVECHAT_REPO_BRANCH) { $env:LIVECHAT_REPO_BRANCH } else { "main" }
$installDir = if ($env:LIVECHAT_INSTALL_DIR) { $env:LIVECHAT_INSTALL_DIR } else { Join-Path $HOME ".local\share\livechat-mcp" }

Write-Host "`n❯ Bootstrapping livechat-mcp" -ForegroundColor Cyan

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "  ✗ git not found." -ForegroundColor Red
    Write-Host "    Install with: winget install --id Git.Git -e --source winget"
    exit 1
}

New-Item -ItemType Directory -Force -Path (Split-Path $installDir -Parent) | Out-Null

if (Test-Path (Join-Path $installDir ".git")) {
    Write-Host "  ! Repo already at $installDir — fetching latest" -ForegroundColor Yellow
    git -C $installDir fetch --quiet origin $repoBranch
    git -C $installDir checkout --quiet $repoBranch
    git -C $installDir reset --hard --quiet "origin/$repoBranch"
} else {
    Write-Host "  ! Cloning $repoUrl → $installDir" -ForegroundColor Yellow
    git clone --quiet --branch $repoBranch $repoUrl $installDir
}
Write-Host "  ✓ Source at $installDir" -ForegroundColor Green

Set-Location $installDir
& .\install.ps1
exit $LASTEXITCODE
