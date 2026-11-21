param(
    [string]$WheelPath = 'E:\AI\ollama\OpenWebUI-Monitoring\Openwebui-candidate\open_webui-0.13.4-py3-none-any.whl'
)

$ErrorActionPreference = 'Stop'
$expectedWheelHash = 'B4774D87E682A34274A9BCF4C4293F0C4581C11CEA0F7D7F667727CAADFC9818'
$expectedLauncherHash = '9066F07E5D435F1C5B5EA7688977353C1FA2EF56641234AF5495497BBF8AD030'
$launcher = 'E:\apps\open-webui\start-openwebui.ps1'
$db = 'C:\downloads\open-webui\data\webui.db'
$url = 'http://127.0.0.1'
$backupRoot = 'E:\apps\open-webui\deployment-backups'
$backupDir = Join-Path $backupRoot (Get-Date -Format 'yyyyMMdd-HHmmss')
$originalLauncher = Join-Path $backupDir 'start-openwebui.original.ps1'
$candidateShell = $null
$oldStopped = $false
$launcherChanged = $false
$savedUvOffline = $env:UV_OFFLINE
$savedHfOffline = $env:HF_HUB_OFFLINE

function Get-LiveListener {
    $listeners = @(Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction SilentlyContinue)
    $owners = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -ne 1) { throw "Expected one process owning port 80; found $($owners.Count)." }
    return [int]$owners[0]
}

function Wait-LiveHealth([int]$seconds, [int]$previousPid, [string]$expectedWheel = '') {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $pidNow = Get-LiveListener
            if ($pidNow -ne $previousPid) {
                $version = (Invoke-RestMethod -Uri "$url/api/version" -TimeoutSec 4).version
                $config = Invoke-WebRequest -Uri "$url/api/config" -UseBasicParsing -TimeoutSec 4
                $page = Invoke-WebRequest -Uri "$url/" -UseBasicParsing -TimeoutSec 4
                if ($version -eq '0.13.4' -and $config.StatusCode -eq 200 -and $page.StatusCode -eq 200) {
                    $wheelMatched = -not $expectedWheel
                    if ($expectedWheel) {
                        $checkPid = $pidNow
                        for ($depth = 0; $depth -lt 12 -and $checkPid; $depth++) {
                            $checkProc = Get-CimInstance Win32_Process -Filter "ProcessId = $checkPid" -ErrorAction Stop
                            if (-not $checkProc) { break }
                            if ($checkProc.Name -ieq 'uvx.exe' -and
                                $checkProc.CommandLine.IndexOf($expectedWheel, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                                $wheelMatched = $true
                                break
                            }
                            $checkPid = [int]$checkProc.ParentProcessId
                        }
                    }
                    if ($wheelMatched) { return $pidNow }
                }
            }
        } catch { }
        Start-Sleep -Seconds 3
    }
    throw "OpenWebUI did not become healthy on port 80 within $seconds seconds."
}

function Start-Launcher {
    # The original launcher sets the existing DATA_DIR, Ollama path, LibreOffice path,
    # and uvx command. These two flags prevent downloads during either start path.
    $env:UV_OFFLINE = '1'
    $env:HF_HUB_OFFLINE = '1'
    return Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $launcher + '"')
    ) -WorkingDirectory (Split-Path $launcher) -WindowStyle Hidden -PassThru
}

function Stop-CandidateIfPresent {
    $owners = @(Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($ownerPid in $owners) {
        $checkPid = [int]$ownerPid
        for ($depth = 0; $depth -lt 12 -and $checkPid; $depth++) {
            $checkProc = Get-CimInstance Win32_Process -Filter "ProcessId = $checkPid" -ErrorAction SilentlyContinue
            if (-not $checkProc) { break }
            if ($checkProc.Name -ieq 'uvx.exe' -and $checkProc.CommandLine -and
                $checkProc.CommandLine.IndexOf($WheelPath, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                & taskkill.exe /PID $checkProc.ProcessId /T /F | Out-Null
                Start-Sleep -Seconds 2
                return
            }
            $checkPid = [int]$checkProc.ParentProcessId
        }
    }
}

try {
    if (-not (Test-Path -LiteralPath $WheelPath -PathType Leaf)) { throw "Wheel missing: $WheelPath" }
    if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) { throw "Launcher missing: $launcher" }
    if (-not (Test-Path -LiteralPath $db -PathType Leaf)) { throw "Database missing: $db" }
    if ((Get-FileHash -LiteralPath $WheelPath -Algorithm SHA256).Hash -ne $expectedWheelHash) {
        throw 'Candidate wheel hash differs from the locally built and reviewed artifact.'
    }
    if ((Get-FileHash -LiteralPath $launcher -Algorithm SHA256).Hash -ne $expectedLauncherHash) {
        throw 'Live launcher changed since preflight. Review it before deploying.'
    }
    try {
        $writeProbe = [IO.File]::Open($launcher, [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
        $writeProbe.Dispose()
    } catch {
        throw 'Current Windows account cannot write the live launcher. Run only under the same service account with an elevated token; no live change was made.'
    }
    $oldPid = Get-LiveListener
    $oldVersion = (Invoke-RestMethod -Uri "$url/api/version" -TimeoutSec 8).version
    if ($oldVersion -ne '0.13.4') { throw "Live version changed: $oldVersion" }
    $liveProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $oldPid" -ErrorAction Stop
    $liveOwner = Invoke-CimMethod -InputObject $liveProcess -MethodName GetOwner -ErrorAction Stop
    $currentOwner = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    if (($liveOwner.Domain + '\' + $liveOwner.User) -ine $currentOwner) {
        throw "Deployment account $currentOwner differs from the account running OpenWebUI. No live change was made."
    }

    $ancestorPid = $oldPid
    $uvxPid = $null
    for ($step = 0; $step -lt 12 -and $ancestorPid; $step++) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $ancestorPid" -ErrorAction Stop
        if (-not $proc) { break }
        if ($proc.ProcessId -eq $PID) { throw 'Run this script from a different PowerShell window than the live launcher.' }
        if ($proc.Name -ieq 'uvx.exe') { $uvxPid = [int]$proc.ProcessId }
        $ancestorPid = [int]$proc.ParentProcessId
    }
    if (-not $uvxPid) { throw 'Could not identify the live uvx process. No restart will be attempted.' }

    $originalText = Get-Content -LiteralPath $launcher -Raw
    if ([regex]::Matches($originalText, 'open-webui@latest').Count -ne 1) {
        throw 'Launcher does not contain exactly one open-webui@latest command.'
    }
    if (Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1) {
        $pythonExe = (Get-Command python | Select-Object -First 1).Source
    } else { throw 'Python is required for the SQLite online backup.' }

    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    $userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $backupDir /inheritance:r /grant:r `
        ('*' + $userSid + ':(OI)(CI)F') '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not restrict the rollback folder to this user, SYSTEM, and Administrators.' }
    Copy-Item -LiteralPath $launcher -Destination $originalLauncher -ErrorAction Stop
    $backupPy = @'
import sqlite3, sys
from pathlib import Path
source = sqlite3.connect('file:' + Path(sys.argv[1]).as_posix() + '?mode=ro', uri=True)
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
finally:
    target.close()
    source.close()
'@
    & $pythonExe -c $backupPy $db (Join-Path $backupDir 'webui.db')
    if ($LASTEXITCODE -ne 0) { throw 'SQLite online backup failed. Live service was not stopped.' }
    Write-Host "Rollback launcher and database backup saved in $backupDir"

    & (Join-Path $PSScriptRoot 'Run-OpenWebUIStateCheck.ps1') -Phase Before
    if ($LASTEXITCODE -ne 0) { throw 'Before state check failed. Live service was not stopped.' }

    $replacement = '--from "' + $WheelPath + '" open-webui'
    $customText = '$env:UV_OFFLINE = "1"' + [Environment]::NewLine +
        '$env:HF_HUB_OFFLINE = "1"' + [Environment]::NewLine +
        $originalText.Replace('open-webui@latest', $replacement)
    $launcherChanged = $true
    Set-Content -LiteralPath $launcher -Value $customText -Encoding UTF8

    Write-Host "Stopping current OpenWebUI process tree at uvx PID $uvxPid..."
    $oldStopped = $true
    & taskkill.exe /PID $uvxPid /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the old uvx process tree.' }
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        $listeners = @(Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction SilentlyContinue)
        if ($listeners.Count -eq 0) { break }
        Start-Sleep -Seconds 2
    }
    if (@(Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction SilentlyContinue).Count -ne 0) {
        throw 'Port 80 remained occupied after stopping the old process tree.'
    }

    Write-Host 'Starting the custom OpenWebUI wheel with package downloads disabled...'
    $candidateShell = Start-Launcher
    $newPid = Wait-LiveHealth 180 $oldPid $WheelPath
    Write-Host "Custom OpenWebUI is responding on port 80 (PID $newPid)."

    & (Join-Path $PSScriptRoot 'Run-OpenWebUIStateCheck.ps1') -Phase After
    if ($LASTEXITCODE -ne 0) { throw 'After state check found a new difference.' }
    Write-Host 'SUCCESS: Custom OpenWebUI 0.13.4 is live. Configuration comparison found no new differences.'
    Write-Host 'CW Pay PDF and Excel actions still require a browser check with a real CW Pay result.'
    Write-Host "Rollback files: $backupDir"
} catch {
    Write-Host "DEPLOYMENT FAILED: $($_.Exception.Message)" -ForegroundColor Red
    $launcherRestored = -not $launcherChanged
    if ($launcherChanged) {
        $currentHash = try { (Get-FileHash -LiteralPath $launcher -Algorithm SHA256).Hash } catch { '' }
        if ($currentHash -eq $expectedLauncherHash) {
            $launcherRestored = $true
            Write-Host 'Original launcher is unchanged; no restore was needed.'
        } else {
            try {
                Copy-Item -LiteralPath $originalLauncher -Destination $launcher -Force
                $launcherRestored = $true
                Write-Host 'Original launcher restored.'
            } catch {
                Write-Host "ROLLBACK NEEDS ATTENTION: Could not restore original launcher: $($_.Exception.Message)" -ForegroundColor Red
                Write-Host "Original launcher backup: $originalLauncher"
            }
        }
    }
    if ($oldStopped) {
        Stop-CandidateIfPresent
        if ($candidateShell -and -not $candidateShell.HasExited) {
            & taskkill.exe /PID $candidateShell.Id /T /F | Out-Null
        }
        $occupied = @(Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction SilentlyContinue)
        if (-not $launcherRestored) {
            Write-Host 'Original launcher could not be restored; automatic restart was skipped.' -ForegroundColor Red
        } elseif ($occupied.Count -eq 0) {
            Write-Host 'Restarting the original OpenWebUI launcher offline...'
            $rollbackShell = Start-Launcher
            try {
                $restoredPid = Wait-LiveHealth 180 $oldPid
                Write-Host "ROLLBACK HEALTHY: Original OpenWebUI is responding on port 80 (PID $restoredPid)."
            } catch {
                Write-Host "ROLLBACK NEEDS ATTENTION: $($_.Exception.Message)" -ForegroundColor Red
                Write-Host 'Inspect E:\apps\open-webui\logs\transcript.log from the server.'
            }
        } else {
            Write-Host 'Port 80 is occupied; original launcher is restored, but no second copy was started.'
        }
    }
    exit 1
} finally {
    $env:UV_OFFLINE = $savedUvOffline
    $env:HF_HUB_OFFLINE = $savedHfOffline
}
