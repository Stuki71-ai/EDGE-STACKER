# LOCAL CLV close-capture — re-queries Odds API for picks emitted
# by run_local_nba.ps1, records line/odds at game-near time.
# Scheduled twice: 18:30 ET and 18:55 ET (earliest NBA tip is 18:10 ET).

$PSNativeCommandUseErrorActionPreference = $false
$ErrorActionPreference = "Continue"

Set-Location "C:\Users\istva\.claude\CODE\EDGE STACKER"

# Load .env
if (Test-Path .env) {
    Get-Content .env | ForEach-Object {
        if ($_ -match '^([^#][^=]+)=(.*)$') {
            [System.Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), 'Process')
        }
    }
}

$logFile = "clv_capture.log"
"=== CLV capture run at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File -FilePath $logFile -Append -Encoding utf8
python clv_capture.py 2>>$logFile
"--- exit code: $LASTEXITCODE ---" | Out-File -FilePath $logFile -Append -Encoding utf8
