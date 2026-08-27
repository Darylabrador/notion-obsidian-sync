# Run one sync pass. Intended for manual use or as the action invoked by the
# Windows Task Scheduler task. Extra arguments are forwarded to `sync`.

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Resolve-Path (Join-Path $ScriptDir "..\..")
Set-Location $ProjectDir

$exe = Join-Path $ProjectDir ".venv\Scripts\notion-obsidian-sync.exe"
if (-not (Test-Path $exe)) {
    Write-Error "Virtual environment not found. Run scripts\windows\install.ps1 first."
    exit 1
}

$logDir = Join-Path $ProjectDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir "task-scheduler.log"

& $exe sync @args *>> $logFile
exit $LASTEXITCODE
