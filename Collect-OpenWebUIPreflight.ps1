param([switch]$Publish)

$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
$reportDir = Join-Path $repo 'openwebui-deployment-reports'
New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$report = Join-Path $reportDir "preflight-$stamp.txt"

$lines = [System.Collections.Generic.List[string]]::new()
function Add-Result([string]$name, [object]$value) {
    $lines.Add("${name}: $value")
}

Add-Result 'Kind' 'Read-only OpenWebUI deployment preflight'
Add-Result 'Checked' (Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')
Add-Result 'Computer' $env:COMPUTERNAME

$launcher = 'E:\apps\open-webui\start-openwebui.ps1'
$dataDir = 'C:\downloads\open-webui\data'
Add-Result 'Launcher exists' (Test-Path -LiteralPath $launcher)
Add-Result 'Data directory exists' (Test-Path -LiteralPath $dataDir)
if (Test-Path -LiteralPath $launcher) {
    Add-Result 'Launcher SHA256' (Get-FileHash -LiteralPath $launcher -Algorithm SHA256).Hash
    $launcherText = Get-Content -LiteralPath $launcher -Raw
    Add-Result 'Launcher uses open-webui@latest' ($launcherText -match 'open-webui@latest')
    Add-Result 'Launcher references DATA_DIR' ($launcherText -match 'DATA_DIR')
}

foreach ($name in @('git', 'node', 'npm', 'uvx', 'py', 'python')) {
    $command = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
    Add-Result "$name available" ([bool]$command)
}

foreach ($root in @('C:\', 'E:\')) {
    $drive = Get-PSDrive -Name $root.Substring(0, 1) -ErrorAction SilentlyContinue
    if ($drive) { Add-Result "$root free GB" ([math]::Round($drive.Free / 1GB, 1)) }
}

try {
    $version = Invoke-RestMethod -Uri 'http://127.0.0.1/api/version' -TimeoutSec 8
    Add-Result 'Live OpenWebUI version' $version.version
} catch {
    Add-Result 'Live OpenWebUI version' 'Endpoint unavailable'
}

Add-Result 'Live service changed' 'No'
$lines | Set-Content -LiteralPath $report -Encoding UTF8
Write-Host "Preflight report: $report"
Get-Content -LiteralPath $report

if ($Publish) {
    $relativeReport = 'openwebui-deployment-reports/' + (Split-Path $report -Leaf)
    $status = & git -C $repo status --porcelain --untracked-files=normal -- . ':!openwebui-deployment-reports/'
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect Git status.' }
    if ($status) { throw 'Repository has other changes. Report saved locally; publish it from a clean checkout.' }
    & git -C $repo add -- $relativeReport
    if ($LASTEXITCODE -ne 0) { throw 'Could not stage the report.' }
    & git -C $repo commit -m "Record OpenWebUI deployment preflight $stamp"
    if ($LASTEXITCODE -ne 0) { throw 'Could not commit the report.' }
    & git -C $repo push origin HEAD:main
    if ($LASTEXITCODE -ne 0) { throw 'Could not push the report.' }
    Write-Host 'Report published to GitHub.'
}
