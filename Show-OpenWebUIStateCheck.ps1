param(
    [string]$StoreDir = 'E:\apps\openwebui-private-checks'
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$pointer = Join-Path $StoreDir 'current-run.txt'
if (-not (Test-Path -LiteralPath $pointer)) { throw 'No baseline has been saved.' }
$run = (Get-Content -LiteralPath $pointer -Raw).Trim()
if ($run -notmatch '^\d{8}-\d{6}$') { throw 'Invalid current-run pointer.' }
$path = Join-Path (Join-Path $StoreDir $run) 'before.dpapi'
if (-not (Test-Path -LiteralPath $path)) { throw 'Encrypted baseline is missing.' }

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
        Write-Host "PROVIDER MODEL-LIST FAILED: $($item.Name) $reason"
    }
}
$syntaxErrors = @($snapshot.code_validation.PSObject.Properties | Where-Object { -not $_.Value.syntax_ok }).Count
Write-Host "Stored function/tool syntax errors: $syntaxErrors"
Write-Host 'Chat completion, function, and tool actions: NOT VERIFIED'
