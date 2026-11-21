param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Before', 'After')]
    [string]$Phase,
    [string]$DbPath = 'C:\downloads\open-webui\data\webui.db',
    [string]$BaseUrl = 'http://127.0.0.1',
    [string]$StoreDir = 'E:\apps\openwebui-private-checks',
    [string]$PythonExe = ''
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$worker = Join-Path $PSScriptRoot 'openwebui_state_snapshot.py'
if (-not (Test-Path -LiteralPath $worker)) { throw "Snapshot worker missing: $worker" }
if (-not (Test-Path -LiteralPath $DbPath)) { throw "Database missing: $DbPath" }
if (-not $PythonExe) {
    $candidates = @('C:\Program Files\Python311\python.exe', 'C:\Python311\python.exe')
    $PythonExe = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $PythonExe) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($pythonCommand) { $PythonExe = $pythonCommand.Source }
    }
    if (-not $PythonExe) { throw 'Python 3.10+ was not found. Supply -PythonExe with its full path.' }
}
New-Item -ItemType Directory -Path $StoreDir -Force | Out-Null
$pointer = Join-Path $StoreDir 'current-run.txt'

function Protect-Text([string]$plainText, [string]$path) {
    $bytes = [Text.Encoding]::UTF8.GetBytes($plainText)
    try {
        $encrypted = [Security.Cryptography.ProtectedData]::Protect(
            $bytes, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser
        )
        [IO.File]::WriteAllBytes($path, $encrypted)
    } finally {
        [Array]::Clear($bytes, 0, $bytes.Length)
    }
}

function Read-ProtectedText([string]$path) {
    $encrypted = [IO.File]::ReadAllBytes($path)
    $bytes = [Security.Cryptography.ProtectedData]::Unprotect(
        $encrypted, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    try { return [Text.Encoding]::UTF8.GetString($bytes) }
    finally { [Array]::Clear($bytes, 0, $bytes.Length) }
}

if ($Phase -eq 'Before') {
    $runName = Get-Date -Format 'yyyyMMdd-HHmmss'
    $runDir = Join-Path $StoreDir $runName
    if (Test-Path -LiteralPath $runDir) { throw "Run already exists: $runDir" }
    New-Item -ItemType Directory -Path $runDir | Out-Null
} else {
    if (-not (Test-Path -LiteralPath $pointer)) { throw 'No baseline. Run the Before phase first.' }
    $runName = (Get-Content -LiteralPath $pointer -Raw).Trim()
    if ($runName -notmatch '^\d{8}-\d{6}$') { throw 'Invalid current-run pointer.' }
    $runDir = Join-Path $StoreDir $runName
    if (-not (Test-Path -LiteralPath (Join-Path $runDir 'before.dpapi'))) { throw 'Encrypted baseline is missing.' }
}

$json = & $PythonExe $worker --db $DbPath --base-url $BaseUrl
if ($LASTEXITCODE -ne 0 -or -not $json) { throw 'Read-only snapshot failed.' }
$current = $json | ConvertFrom-Json
$snapshotPath = Join-Path $runDir ($Phase.ToLowerInvariant() + '.dpapi')
Protect-Text $json $snapshotPath

if ($Phase -eq 'Before') {
    $runName | Set-Content -LiteralPath $pointer
    Write-Host "Baseline saved in $runDir"
    Write-Host "Configuration tables captured: $(@($current.tables.PSObject.Properties.Name).Count)"
    $failedServices = @($current.health.PSObject.Properties | Where-Object { -not $_.Value.ok }).Count
    $failedProviders = @($current.provider_probes.PSObject.Properties | Where-Object { -not $_.Value.ok }).Count
    $invalidCode = @($current.code_validation.PSObject.Properties | Where-Object { -not $_.Value.syntax_ok }).Count
    Write-Host "Failed service endpoints: $failedServices; failed provider model-list probes: $failedProviders; syntax errors: $invalidCode"
    Write-Host 'Chat completion, function, and tool actions: NOT VERIFIED'
    if ($failedServices -gt 0 -or $failedProviders -gt 0 -or $invalidCode -gt 0) { exit 2 }
    return
}

$before = (Read-ProtectedText (Join-Path $runDir 'before.dpapi')) | ConvertFrom-Json
$differences = [System.Collections.Generic.List[string]]::new()
$oldMissing = @($before.missing_tables | Sort-Object)
$newMissing = @($current.missing_tables | Sort-Object)
if (($oldMissing -join [char]0) -ne ($newMissing -join [char]0)) {
    $differences.Add('CHANGED database table availability')
}
$tableNames = @($before.tables.PSObject.Properties.Name) + @($current.tables.PSObject.Properties.Name) | Sort-Object -Unique
foreach ($table in $tableNames) {
    $oldRows = @($before.tables.$table)
    $newRows = @($current.tables.$table)
    $oldMap = @{}
    $newMap = @{}
    foreach ($row in $oldRows) { if ($null -ne $row.key) { $oldMap[[string]$row.key] = $row.fingerprint } }
    foreach ($row in $newRows) { if ($null -ne $row.key) { $newMap[[string]$row.key] = $row.fingerprint } }
    $keys = @($oldMap.Keys) + @($newMap.Keys) | Sort-Object -Unique
    foreach ($key in $keys) {
        if (-not $oldMap.ContainsKey($key)) { $differences.Add("ADDED $table [$key]") }
        elseif (-not $newMap.ContainsKey($key)) { $differences.Add("MISSING $table [$key]") }
        elseif ($oldMap[$key] -ne $newMap[$key]) { $differences.Add("CHANGED $table [$key]") }
    }
}
foreach ($endpoint in @('/api/version', '/api/config')) {
    if (-not $current.health.$endpoint.ok) { $differences.Add("FAILED service endpoint $endpoint") }
}
$providerNames = @($before.provider_probes.PSObject.Properties.Name) + @($current.provider_probes.PSObject.Properties.Name) | Sort-Object -Unique
foreach ($provider in $providerNames) {
    $old = $before.provider_probes.$provider
    $new = $current.provider_probes.$provider
    if ($null -eq $old) { $differences.Add("ADDED provider probe $provider") }
    elseif ($null -eq $new) { $differences.Add("MISSING provider probe $provider") }
    elseif (-not $new.ok) { $differences.Add("FAILED provider probe $provider") }
    elseif (-not $old.ok) { $differences.Add("BASELINE provider probe failed $provider") }
    else {
        $oldIds = @($old.model_ids | Sort-Object)
        $newIds = @($new.model_ids | Sort-Object)
        if (($oldIds -join [char]0) -ne ($newIds -join [char]0)) {
            $differences.Add("CHANGED provider model list $provider")
        }
    }
}
$codeNames = @($before.code_validation.PSObject.Properties.Name) + @($current.code_validation.PSObject.Properties.Name) | Sort-Object -Unique
foreach ($name in $codeNames) {
    $old = $before.code_validation.$name
    $new = $current.code_validation.$name
    if ($null -eq $old) { $differences.Add("ADDED code definition $name") }
    elseif ($null -eq $new) { $differences.Add("MISSING code definition $name") }
    elseif (-not $new.syntax_ok) { $differences.Add("INVALID code syntax $name") }
}
$status = if ($differences.Count -eq 0) { 'CONFIGURATION MATCH; FUNCTIONAL ACTIONS UNVERIFIED' } else { 'REVIEW REQUIRED' }
$report = @(
    "Status: $status",
    "Baseline: $runName",
    "Service version before: $($before.health.'/api/version'.version)",
    "Service version after: $($current.health.'/api/version'.version)",
    "Differences: $($differences.Count)",
    'Chat completion, function, and tool actions: NOT VERIFIED'
) + @($differences)
Protect-Text ($report -join [Environment]::NewLine) (Join-Path $runDir 'comparison.dpapi')
Write-Host $status
Write-Host "Differences: $($differences.Count)"
Write-Host "Encrypted report: $(Join-Path $runDir 'comparison.dpapi')"
if ($differences.Count -gt 0) { exit 2 }
