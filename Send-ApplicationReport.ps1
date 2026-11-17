<#
.SYNOPSIS
Copy this file beside the original Run-Report.ps1, then right-click it and
choose "Run with PowerShell" to email the report with the Applications table.
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
$expectedRecipientsHash = '0f950afa6b455b1d26cd5385318a164c214e35a4080b15c368ca255c03006031'
$exitCode = 0

try {
    if (-not (Test-Path -LiteralPath $configPath)) { throw "Config not found: $configPath" }
    if (-not (Test-Path -LiteralPath $runner)) { throw "Report runner not found: $runner" }

    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    $recipients = @($config.email_to | ForEach-Object { $_ -split '[,;\s]+' } |
        Where-Object { $_ } | ForEach-Object { $_.Trim().ToLowerInvariant() })
    $sorted = @($recipients | Sort-Object -Unique)
    $joined = $sorted -join ','
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($joined)
        $digest = [System.BitConverter]::ToString($sha256.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha256.Dispose()
    }
    if ($recipients.Count -ne 2 -or $digest -ne $expectedRecipientsHash) {
        throw "Stopped: recipient list has changed. Current recipients: $($recipients -join ', ')"
    }
    if (-not $config.resend_api_key -and -not $env:RESEND_API_KEY) {
        throw 'Resend API key is missing from config and environment.'
    }

    Write-Host "Sending the report to: $($recipients -join ', ')" -ForegroundColor Cyan
    & $runner -Send -Days 1 -ReportDir $alertDir -TryCollapsibleFailures
    Write-Host 'Email sent successfully.' -ForegroundColor Green
} catch {
    $exitCode = 1
    Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red
} finally {
    Read-Host 'Press Enter to close this window' | Out-Null
}

exit $exitCode
