<#
.SYNOPSIS
Copy this file beside the original Run-Report.ps1, then right-click it and
choose "Run with PowerShell" to email the combined report.
Recipients come from the active email_to setting in config.json.
The original report files are not changed. The window stays open afterward.
#>

$ErrorActionPreference = 'Stop'
$scriptFolder = Split-Path -Parent $MyInvocation.MyCommand.Path
$devRoot = 'D:\Ravi-github'
$alertDir = if (Test-Path -LiteralPath (Join-Path $scriptFolder 'litellm_report.py')) {
    $scriptFolder
} else {
    'D:\git pull for sanjay\cw-ai-coe-litellm-alert'
}
$configPath = Join-Path $alertDir 'config.json'
$runner = Join-Path $devRoot 'Run-ApplicationDaily.ps1'
$exitCode = 0

try {
    if (-not (Test-Path -LiteralPath $configPath)) { throw "Config not found: $configPath" }
    if (-not (Test-Path -LiteralPath $runner)) { throw "Report runner not found: $runner" }

    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    $recipients = @($config.email_to | ForEach-Object { $_ -split '[,;\s]+' } |
        Where-Object { $_ } | ForEach-Object { $_.Trim() })
    if ($recipients.Count -eq 0) { throw 'No recipients in config.json email_to.' }
    $invalid = @($recipients | Where-Object { $_ -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$' })
    if ($invalid.Count) { throw "Invalid email_to recipient(s): $($invalid -join ', ')" }
    if (@($recipients | Sort-Object -Unique).Count -ne $recipients.Count) {
        throw 'Duplicate recipient in config.json email_to.'
    }
    if (-not $config.resend_api_key -and -not $env:RESEND_API_KEY) {
        throw 'Resend API key is missing from config and environment.'
    }

    Write-Host 'Sending the combined report to these config.json email_to recipients:' -ForegroundColor Cyan
    $recipients | ForEach-Object { Write-Host "  $_" -ForegroundColor Cyan }
    & $runner -Send -Days 1 -ReportDir $alertDir -TryCollapsibleFailures
    Write-Host 'Email sent successfully.' -ForegroundColor Green
} catch {
    $exitCode = 1
    Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red
} finally {
    Read-Host 'Press Enter to close this window' | Out-Null
}

exit $exitCode
