# Uninstall the notion-obsidian-sync CLI, regardless of how it was installed
# (project .venv, pipx, or a plain `pip install` into some other
# interpreter/venv). Also removes the Task Scheduler task if one was
# installed.
#
# This script NEVER touches your Obsidian vault or the notes it synced —
# only the tool's own installation and local project state are candidates
# for removal, and even those require your confirmation (or -Yes).
#
# Usage:
#   .\scripts\windows\uninstall.ps1                # interactive
#   .\scripts\windows\uninstall.ps1 -Yes            # no prompts
#   .\scripts\windows\uninstall.ps1 -Yes -Purge     # also remove local state/logs

param(
    [switch]$Yes,
    [switch]$Purge
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Resolve-Path (Join-Path $ScriptDir "..\..")
Set-Location $ProjectDir

function Confirm-Action {
    param([string]$Prompt)
    if ($Yes) { return $true }
    $reply = Read-Host "$Prompt [y/N]"
    return $reply -match '^[Yy]$'
}

$didSomething = $false

# --- 1. Automation: Task Scheduler task -----------------------------------------

$task = Get-ScheduledTask -TaskName "NotionObsidianSync" -ErrorAction SilentlyContinue
if ($task) {
    if (Confirm-Action "Found scheduled task 'NotionObsidianSync'. Remove it?") {
        Unregister-ScheduledTask -TaskName "NotionObsidianSync" -Confirm:$false
        Write-Host "Removed scheduled task 'NotionObsidianSync'."
        $didSomething = $true
    }
}

# --- 2. pipx install -------------------------------------------------------------

$pipx = Get-Command pipx -ErrorAction SilentlyContinue
if ($pipx) {
    $pipxList = & pipx list --short 2>$null
    if ($pipxList -match '^notion-obsidian-sync\s') {
        if (Confirm-Action "Found a pipx installation of notion-obsidian-sync. Uninstall it?") {
            & pipx uninstall notion-obsidian-sync
            Write-Host "Removed pipx installation."
            $didSomething = $true
        }
    }
}

# --- 3. Project virtual environment (.venv) --------------------------------------

if (Test-Path ".venv") {
    if (Confirm-Action "Found the project virtual environment at $ProjectDir\.venv. Remove it?") {
        Remove-Item -Recurse -Force ".venv"
        Write-Host "Removed $ProjectDir\.venv."
        $didSomething = $true
    }
}

# --- 4. Any other pip install (system/user Python, outside .venv/pipx) ----------

$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    & python -m pip show notion-obsidian-sync *> $null
    if ($LASTEXITCODE -eq 0) {
        $exePath = & python -c "import sys; print(sys.executable)"
        if (Confirm-Action "Found notion-obsidian-sync installed for '$exePath'. Uninstall it?") {
            & python -m pip uninstall -y notion-obsidian-sync
            Write-Host "Removed pip installation for $exePath."
            $didSomething = $true
        }
    }
}

# --- 5. Optional: local state/logs (never the vault) -----------------------------

if ($Purge) {
    Remove-Item -Force -ErrorAction SilentlyContinue ".sync-state.sqlite", ".sync-state.sqlite-wal", ".sync-state.sqlite-shm"
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "logs"
    Write-Host "Purged local state database and logs (your synced notes in the Obsidian vault were not touched)."
    $didSomething = $true
}

Write-Host ""
if ($didSomething) {
    Write-Host "Uninstall complete."
} else {
    Write-Host "Nothing found to uninstall."
}
Write-Host "Note: your .env, and everything already synced into your Obsidian vault, were left untouched."
if (-not $Purge) {
    Write-Host "Local state (.sync-state.sqlite*, logs\) was kept - re-run with -Purge to remove it too."
}
