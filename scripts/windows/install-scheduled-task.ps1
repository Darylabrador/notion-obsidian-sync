# Register a Windows Task Scheduler task that runs the sync every
# SYNC_INTERVAL_MINUTES (from .env, default 10). Runs under the current user
# account with no stored password (S4U logon).
#
# To remove the task later:
#   Unregister-ScheduledTask -TaskName "NotionObsidianSync" -Confirm:$false

$ErrorActionPreference = "Stop"

$TaskName = "NotionObsidianSync"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Resolve-Path (Join-Path $ScriptDir "..\..")
$SyncScript = Join-Path $ScriptDir "sync.ps1"

if (-not (Test-Path $SyncScript)) {
    Write-Error "sync.ps1 not found next to this script."
    exit 1
}

$IntervalMinutes = 10
$envFile = Join-Path $ProjectDir ".env"
if (Test-Path $envFile) {
    $match = Select-String -Path $envFile -Pattern '^SYNC_INTERVAL_MINUTES=(.+)$' | Select-Object -Last 1
    if ($match) {
        $IntervalMinutes = [int]$match.Matches[0].Groups[1].Value.Trim()
    }
}

$powershellExe = (Get-Process -Id $PID).Path
$argumentList = "-NoProfile -ExecutionPolicy Bypass -File `"$SyncScript`""

$action = New-ScheduledTaskAction -Execute $powershellExe -Argument $argumentList -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -RepetitionDuration ([TimeSpan]::MaxValue)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description "Notion -> Obsidian sync" -Force | Out-Null

Write-Host "Scheduled task '$TaskName' installed: runs every $IntervalMinutes minute(s)."
Write-Host ""
Write-Host "Check status with:"
Write-Host "  Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host ""
Write-Host "Run it immediately with:"
Write-Host "  Start-ScheduledTask -TaskName $TaskName"
Write-Host ""
Write-Host "Remove it with:"
Write-Host "  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
