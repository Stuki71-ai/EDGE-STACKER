# EDGE STACKER LOCAL — NBA Props with full nba_api access
# Runs at 22:00 CET (= 4:00 PM ET, both DST-aware) via Windows Task Scheduler.

# DST-proof guard: only run if it's 16:00 ET (4:00 PM ET).
# CET is always 6h ahead of ET (both observe DST), so 22:00 CET = 16:00 ET year-round.
$nowET = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow, "Eastern Standard Time")
$hourET = $nowET.Hour
if ($hourET -ne 16) {
    Write-Host "Skipping — current ET hour is $hourET, want 16"
    exit 0
}

$ErrorActionPreference = "Stop"
Set-Location "C:\Users\istva\.claude\CODE\EDGE STACKER"

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

# Reset daily state for this run (local is parallel to VPS — separate state file)
$dailyStateLocal = "daily_state_local.json"
@{ date = "1970-01-01"; total_exposure = 0; module_exposure = @{}; picks_placed = 0 } | ConvertTo-Json | Set-Content $dailyStateLocal

# Run NBA module with full nba_api
$output = python main.py --modules nba_props --json-only 2>$null
if ($output) {
    # POST to local n8n webhook
    $webhook = "http://localhost:5678/webhook/edge-stacker-nba-local"
    try {
        Invoke-RestMethod -Method Post -Uri $webhook -ContentType "application/json" -Body $output -TimeoutSec 30
        Write-Host "Posted to local n8n: $webhook"
    } catch {
        Write-Host "Webhook POST failed: $_"
    }
}
