# EDGE STACKER LOCAL - NBA Props with full nba_api access
# Runs at 22:00 CET (= 4:00 PM ET, both DST-aware) via Windows Task Scheduler.

# DST-proof guard: only run if it's 16:00 ET (4:00 PM ET).
# CET is always 6h ahead of ET (both observe DST), so 22:00 CET = 16:00 ET year-round.
$nowET = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow, "Eastern Standard Time")
$hourET = $nowET.Hour
if ($hourET -ne 16) {
    Write-Host "Skipping - current ET hour is $hourET, want 16"
    exit 0
}

# DON'T treat native command stderr as terminating error (Python's logger
# writes INFO lines to stderr; without this, PS 7+ crashes the script).
$PSNativeCommandUseErrorActionPreference = $false
$ErrorActionPreference = "Continue"

Set-Location "C:\Users\istva\.claude\CODE\EDGE STACKER"

# Rotating log file for diagnostics
$logFile = "run_local_nba.log"
"=== Run at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File -FilePath $logFile -Encoding utf8

# Read .env for ODDS_API_KEY etc.
if (Test-Path .env) {
    Get-Content .env | ForEach-Object {
        if ($_ -match '^([^#][^=]+)=(.*)$') {
            [System.Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), 'Process')
        }
    }
}

# Activate full nba_api flow
$env:USE_NBA_API_FULL = "1"

# Reset daily state for this run (local is parallel to VPS - separate state file)
$dailyStateLocal = "daily_state_local.json"
@{ date = "1970-01-01"; total_exposure = 0; module_exposure = @{}; picks_placed = 0 } | ConvertTo-Json | Set-Content $dailyStateLocal

# Run NBA module - capture stdout to variable, stderr to log file
$output = python main.py --modules nba_props --json-only 2>>$logFile
"--- python exit code: $LASTEXITCODE ---" | Out-File -FilePath $logFile -Append -Encoding utf8

if ($output) {
    # POST to local n8n webhook
    $webhook = "http://localhost:5678/webhook/edge-stacker-nba-local"
    try {
        Invoke-RestMethod -Method Post -Uri $webhook -ContentType "application/json" -Body $output -TimeoutSec 30 | Out-Null
        "Posted to local n8n: $webhook" | Tee-Object -FilePath $logFile -Append
    } catch {
        "Webhook POST failed: $_" | Tee-Object -FilePath $logFile -Append
    }
} else {
    "No output from python - skipping webhook POST" | Tee-Object -FilePath $logFile -Append
}
