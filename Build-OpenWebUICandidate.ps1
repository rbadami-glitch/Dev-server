param([switch]$Publish)

$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
$version = 'v0.13.4'
$reportDir = Join-Path $repo 'openwebui-deployment-reports'
New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$candidate = "E:\apps\open-webui-custom-candidate-0.13.4-$stamp"
$log = Join-Path $reportDir "candidate-build-$stamp.log"
$success = $false

Start-Transcript -LiteralPath $log | Out-Null
try {
    Write-Host "Candidate: $candidate"
    Write-Host "Official base: $version"
    Write-Host 'Live service will not be stopped or changed.'
    if (Test-Path -LiteralPath $candidate) {
        throw "Candidate path already exists. Review it before another run: $candidate"
    }
    foreach ($commandName in @('git', 'npm', 'py')) {
        if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) {
            throw "Required command is missing: $commandName"
        }
    }

    & git -c http.sslBackend=openssl clone --depth 1 --branch $version https://github.com/open-webui/open-webui.git $candidate
    if ($LASTEXITCODE -ne 0) { throw 'Official source clone failed.' }
    $baseCommit = & git -C $candidate rev-parse HEAD
    if ($baseCommit -ne '0666e59d26a4d056e0bacee41742486badd71cf8') {
        throw "Unexpected official source commit: $baseCommit"
    }

    $bundle = Join-Path $repo 'openwebui-custom'
    $patch = Join-Path $bundle 'cw-pay-features.patch'
    # Git for Windows may check the transported .patch out with CRLF. The
    # official source is the exact pinned commit above; ignore only whitespace
    # while matching patch context so line endings do not cause a false conflict.
    & git -C $candidate apply --check --ignore-whitespace $patch
    if ($LASTEXITCODE -ne 0) { throw 'Custom feature patch does not apply cleanly.' }
    & git -C $candidate apply --ignore-whitespace $patch
    if ($LASTEXITCODE -ne 0) { throw 'Custom feature patch failed.' }

    $utilDir = Join-Path $candidate 'src\lib\utils'
    Copy-Item -LiteralPath (Join-Path $bundle 'cwPayPdfExport.ts') -Destination $utilDir
    Copy-Item -LiteralPath (Join-Path $bundle 'cwPayPdfExport.test.ts') -Destination $utilDir

    & py -3.11 -m venv (Join-Path $candidate '.build-venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 build environment creation failed.' }
    $python = Join-Path $candidate '.build-venv\Scripts\python.exe'
    & $python -m pip install build
    if ($LASTEXITCODE -ne 0) { throw 'Python build tooling installation failed.' }

    Push-Location $candidate
    try {
        & $python -m build --wheel
        if ($LASTEXITCODE -ne 0) { throw 'Custom OpenWebUI wheel build failed.' }
        & npm run test:frontend -- --run src/lib/utils/cwPayPdfExport.test.ts
        if ($LASTEXITCODE -ne 0) { throw 'CW Pay PDF test failed.' }
        $wheel = Get-ChildItem -Path (Join-Path $candidate 'dist') -Filter 'open_webui-0.13.4-*.whl' |
            Select-Object -First 1
        if (-not $wheel) { throw 'Built wheel was not found.' }
        Write-Host "WHEEL: $($wheel.FullName)"
        Write-Host "WHEEL SHA256: $((Get-FileHash -LiteralPath $wheel.FullName -Algorithm SHA256).Hash)"
        Write-Host 'RESULT: BUILD AND FOCUSED TEST PASSED. Live service unchanged.'
        $success = $true
    } finally {
        Pop-Location
    }
} catch {
    Write-Host "RESULT: FAILED: $($_.Exception.Message)" -ForegroundColor Red
} finally {
    Stop-Transcript | Out-Null
}

Write-Host "Build log: $log"
if ($Publish) {
    $relative = 'openwebui-deployment-reports/' + (Split-Path $log -Leaf)
    & git -C $repo add -- $relative
    if ($LASTEXITCODE -ne 0) { throw 'Could not stage the build log.' }
    & git -C $repo commit -m "Record OpenWebUI candidate build $stamp"
    if ($LASTEXITCODE -ne 0) { throw 'Could not commit the build log.' }
    & git -c http.sslBackend=openssl -C $repo push origin HEAD:main
    if ($LASTEXITCODE -ne 0) { throw 'Could not push the build log.' }
    Write-Host 'Build log published to GitHub.'
}
if (-not $success) { exit 1 }
