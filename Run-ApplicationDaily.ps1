<#
Runs the existing LiteLLM report with an added Applications table.
Preview by default. Use -Send only after reviewing the generated report.
The original alert scripts and scheduled jobs are not modified.
#>
param(
    [double]$Days = 1,
    [switch]$Send,
    [string]$ReportDir = 'D:\git pull for sanjay\cw-ai-coe-litellm-alert'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$script = Join-Path $root 'application_report.py'
$aliases = Join-Path $root 'application_aliases.local.json'

if (-not (Test-Path -LiteralPath $python)) { throw "Python environment not found: $python" }
if (-not (Test-Path -LiteralPath $aliases)) { throw "Application alias file not found: $aliases" }
if (-not (Test-Path -LiteralPath (Join-Path $ReportDir 'config.json'))) {
    throw "LiteLLM report config not found in: $ReportDir"
}

$logDir = Join-Path $root 'application-reports\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("application-report_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
$arguments = @('-B', $script, '--report-dir', $ReportDir, '--days', [string]$Days)
if ($Send) { $arguments += '--send' }

& $python @arguments 2>&1 | Tee-Object -FilePath $log
$code = $LASTEXITCODE
Write-Host "Run log: $log"
if ($code -ne 0) { throw "Application report failed with exit code $code" }
