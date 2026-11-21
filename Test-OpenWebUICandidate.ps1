param(
    [string]$WheelPath = 'E:\AI\ollama\OpenWebUI-Monitoring\Openwebui-candidate\open_webui-0.13.4-py3-none-any.whl',
    [int]$Port = 8082,
    [string]$TestDataDir = 'E:\apps\open-webui-candidate-test-data'
)

$ErrorActionPreference = 'Stop'
$expectedHash = 'B4774D87E682A34274A9BCF4C4293F0C4581C11CEA0F7D7F667727CAADFC9818'
$launcher = 'E:\apps\open-webui\start-openwebui.ps1'
$liveUrl = 'http://127.0.0.1/api/version'
$testUrl = "http://127.0.0.1:$Port"
$process = $null
$passed = $false
$previousDataDir = $env:DATA_DIR
$previousSecret = $env:WEBUI_SECRET_KEY
$previousOfflineMode = $env:OFFLINE_MODE
$previousHfOffline = $env:HF_HUB_OFFLINE
$previousUvOffline = $env:UV_OFFLINE
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stdoutPath = Join-Path $TestDataDir "candidate-$stamp.out.log"
$stderrPath = Join-Path $TestDataDir "candidate-$stamp.err.log"

try {
    if (-not (Test-Path -LiteralPath $WheelPath -PathType Leaf)) { throw "Wheel missing: $WheelPath" }
    $testDataFull = [IO.Path]::GetFullPath($TestDataDir).TrimEnd('\')
    $liveDataFull = [IO.Path]::GetFullPath('C:\downloads\open-webui\data').TrimEnd('\')
    if ($testDataFull.Equals($liveDataFull, [StringComparison]::OrdinalIgnoreCase) -or
        $testDataFull.StartsWith($liveDataFull + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $liveDataFull.StartsWith($testDataFull + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Candidate test data must be separate from live DATA_DIR.'
    }
    $actualHash = (Get-FileHash -LiteralPath $WheelPath -Algorithm SHA256).Hash
    if ($actualHash -ne $expectedHash) { throw "Wheel hash mismatch: $actualHash" }
    if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) { throw 'Live launcher missing.' }
    $launcherHashBefore = (Get-FileHash -LiteralPath $launcher -Algorithm SHA256).Hash
    $liveBefore = (Invoke-RestMethod -Uri $liveUrl -TimeoutSec 8).version
    if (-not $liveBefore) { throw 'Live version endpoint did not return a version.' }
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $Port is already in use. No candidate was started."
    }
    $uvx = Get-Command uvx -ErrorAction Stop | Select-Object -First 1
    New-Item -ItemType Directory -Path $TestDataDir -Force | Out-Null
    $env:DATA_DIR = $TestDataDir
    $env:WEBUI_SECRET_KEY = [guid]::NewGuid().ToString('N')
    $env:OFFLINE_MODE = 'true'
    $env:HF_HUB_OFFLINE = '1'
    $env:UV_OFFLINE = '1'
    $arguments = @(
        '--python', '3.11', '--with', 'unstructured[doc]',
        '--from', ('"' + $WheelPath + '"'),
        'open-webui', 'serve', '--host', '127.0.0.1', '--port', [string]$Port
    )
    Write-Host "Wheel SHA256 verified: $actualHash"
    Write-Host "Starting isolated candidate offline at $testUrl; live service stays on port 80."
    $process = Start-Process -FilePath $uvx.Source -ArgumentList $arguments -PassThru `
        -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    $deadline = (Get-Date).AddMinutes(4)
    $nextUpdate = (Get-Date).AddSeconds(30)
    $candidateVersion = $null
    while ((Get-Date) -lt $deadline) {
        if ($process.HasExited) { throw "Candidate exited early with code $($process.ExitCode). See $stderrPath" }
        try {
            $candidateVersion = (Invoke-RestMethod -Uri "$testUrl/api/version" -TimeoutSec 3).version
            if ($candidateVersion) { break }
        } catch { }
        if ((Get-Date) -ge $nextUpdate) {
            Write-Host 'Waiting for isolated candidate startup...'
            $nextUpdate = (Get-Date).AddSeconds(30)
        }
        Start-Sleep -Seconds 5
    }
    if (-not $candidateVersion) { throw "Candidate did not become ready within 4 minutes. See $stderrPath" }
    if ($candidateVersion -ne '0.13.4') { throw "Unexpected candidate version: $candidateVersion" }
    $config = Invoke-WebRequest -Uri "$testUrl/api/config" -UseBasicParsing -TimeoutSec 8
    $page = Invoke-WebRequest -Uri "$testUrl/" -UseBasicParsing -TimeoutSec 8
    if ($config.StatusCode -ne 200 -or $page.StatusCode -ne 200) {
        throw 'Candidate API or page did not return HTTP 200.'
    }
    $liveAfter = (Invoke-RestMethod -Uri $liveUrl -TimeoutSec 8).version
    $launcherHashAfter = (Get-FileHash -LiteralPath $launcher -Algorithm SHA256).Hash
    if ($liveAfter -ne $liveBefore -or $launcherHashAfter -ne $launcherHashBefore) {
        throw 'Live version or launcher changed during the isolated test.'
    }
    Write-Host "PASS: Candidate version $candidateVersion; API and page HTTP 200."
    Write-Host "PASS: Live version $liveAfter and launcher hash unchanged."
    $passed = $true
} catch {
    Write-Host "FAIL: $($_.Exception.Message)" -ForegroundColor Red
} finally {
    if ($process -and -not $process.HasExited) {
        & taskkill.exe /PID $process.Id /T /F | Out-Null
    }
    Start-Sleep -Seconds 2
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "WARNING: Port $Port is still listening; inspect the candidate process before another test."
    }
    $env:DATA_DIR = $previousDataDir
    $env:WEBUI_SECRET_KEY = $previousSecret
    $env:OFFLINE_MODE = $previousOfflineMode
    $env:HF_HUB_OFFLINE = $previousHfOffline
    $env:UV_OFFLINE = $previousUvOffline
    Write-Host "Candidate logs: $stdoutPath and $stderrPath"
    Write-Host 'Live OpenWebUI was not installed, updated, or stopped by this script.'
}
if (-not $passed) { exit 1 }
