param(
    [string]$StoreDir = 'E:\apps\openwebui-private-checks',
    [string]$DbPath = 'C:\downloads\open-webui\data\webui.db',
    [string]$PythonExe = ''
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$pointer = Join-Path $StoreDir 'current-run.txt'
if (-not (Test-Path -LiteralPath $pointer)) { throw 'No baseline has been saved.' }
$run = (Get-Content -LiteralPath $pointer -Raw).Trim()
if ($run -notmatch '^\d{8}-\d{6}$') { throw 'Invalid current-run pointer.' }
$path = Join-Path (Join-Path $StoreDir $run) 'before.dpapi'
if (-not (Test-Path -LiteralPath $path)) { throw 'Encrypted baseline is missing.' }
$worker = Join-Path $PSScriptRoot 'openwebui_state_snapshot.py'
if (-not (Test-Path -LiteralPath $worker)) { throw "Snapshot worker missing: $worker" }
if (-not $PythonExe) {
    $candidates = @('C:\Program Files\Python311\python.exe', 'C:\Python311\python.exe')
    $PythonExe = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $PythonExe) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($pythonCommand) { $PythonExe = $pythonCommand.Source }
    }
    if (-not $PythonExe) { throw 'Python 3.10+ was not found.' }
}

$bytes = [Security.Cryptography.ProtectedData]::Unprotect(
    [IO.File]::ReadAllBytes($path),
    $null,
    [Security.Cryptography.DataProtectionScope]::CurrentUser
)
try { $snapshot = [Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json }
finally { [Array]::Clear($bytes, 0, $bytes.Length) }

Write-Host "Baseline: $run"
Write-Host "Service version: $($snapshot.health.'/api/version'.version)"
foreach ($item in $snapshot.health.PSObject.Properties) {
    if (-not $item.Value.ok) { Write-Host "SERVICE FAILED: $($item.Name) $($item.Value.error_type)" }
}
foreach ($item in $snapshot.provider_probes.PSObject.Properties) {
    if (-not $item.Value.ok) {
        $reason = if ($item.Value.http_status) { "HTTP $($item.Value.http_status)" } else { $item.Value.error_type }
        Write-Host "BASELINE PROVIDER MODEL-LIST FAILED: $($item.Name) $reason"
        $currentProbeJson = & $PythonExe $worker --db $DbPath --probe-only $item.Name
        if ($LASTEXITCODE -ne 0) { throw "Current probe failed for $($item.Name)" }
        $currentProbe = $currentProbeJson | ConvertFrom-Json
        if ($null -eq $currentProbe.result) {
            Write-Host "CURRENT PROBE: $($item.Name) unavailable or disabled"
        } elseif ($currentProbe.result.ok) {
            Write-Host "CURRENT PROBE: $($item.Name) OK"
        } else {
            $details = if ($currentProbe.result.http_status) {
                "HTTP $($currentProbe.result.http_status)"
            } else {
                "$($currentProbe.result.error_type) $($currentProbe.result.reason_type) $($currentProbe.result.errno)".Trim()
            }
            Write-Host "CURRENT PROBE: $($item.Name) $details"
        }
    }
}
$syntaxErrors = @($snapshot.code_validation.PSObject.Properties | Where-Object { -not $_.Value.syntax_ok }).Count
Write-Host "Stored function/tool syntax errors: $syntaxErrors"
Write-Host 'Chat completion, function, and tool actions: NOT VERIFIED'
