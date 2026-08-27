# Install notion-obsidian-sync on Windows: create a virtualenv, install the
# package, and scaffold a .env file. Safe to re-run; never overwrites an
# existing .env.

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Resolve-Path (Join-Path $ScriptDir "..\..")
Set-Location $ProjectDir

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Error "Python was not found on PATH. Install Python 3.11+ from python.org and re-run this script."
    exit 1
}

$versionOutput = & python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"
$parts = $versionOutput -split "\."
if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 11)) {
    Write-Error "Python 3.11+ is required (found $versionOutput)."
    exit 1
}

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}

Write-Host "Installing notion-obsidian-sync..."
& ".\.venv\Scripts\pip.exe" install -q --upgrade pip
& ".\.venv\Scripts\pip.exe" install -q -e .

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example - edit it with your Notion token and vault path."
} else {
    Write-Host ".env already exists, leaving it untouched."
}

Write-Host ""
Write-Host "Install complete. Next steps:"
Write-Host "  1. Edit .env with your Notion integration token and Obsidian vault path."
Write-Host "  2. .\.venv\Scripts\notion-obsidian-sync.exe doctor"
Write-Host "  3. .\.venv\Scripts\notion-obsidian-sync.exe dry-run"
Write-Host "  4. .\.venv\Scripts\notion-obsidian-sync.exe sync"
Write-Host ""
Write-Host "To automate periodic syncs, run scripts\windows\install-scheduled-task.ps1"
