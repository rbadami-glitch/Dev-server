param(
    [string]$WheelPath = 'E:\AI\ollama\OpenWebUI-Monitoring\Openwebui-candidate\open_webui-0.13.4-py3-none-any.whl',
    [switch]$ReviewedRetry
)

$ErrorActionPreference = 'Stop'
if (-not $ReviewedRetry) {
    throw 'Cutover paused after the port 80 collision. Review the candidate startup and process cleanup before explicitly retrying.'
}
$serviceName = 'OpenWebUIService'
$launcher = 'E:\apps\open-webui\start-openwebui.ps1'
$db = 'C:\downloads\open-webui\data\webui.db'
$baseUrl = 'http://127.0.0.1'
$expectedWheelHash = 'B4774D87E682A34274A9BCF4C4293F0C4581C11CEA0F7D7F667727CAADFC9818'
$expectedLauncherHash = '9066F07E5D435F1C5B5EA7688977353C1FA2EF56641234AF5495497BBF8AD030'
$backupDir = Join-Path 'E:\apps\open-webui\deployment-backups' (Get-Date -Format 'yyyyMMdd-HHmmss')
$launcherBackup = Join-Path $backupDir 'start-openwebui.original.ps1'
$launcherChanged = $false
$serviceRestarted = $false
$previousListener = 0
$script:lastServicePid = 0

function Get-PortOwner {
    $owners = @(Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -ne 1) { throw "Expected one owner for port 80; found $($owners.Count)." }
    return [int]$owners[0]
}

function Test-ProcessChain([int]$ownerPid, [int]$servicePid, [string]$wheelMarker = '') {
    $checkPid = $ownerPid
    $serviceFound = $false
    $wheelFound = -not $wheelMarker
    for ($depth = 0; $depth -lt 12 -and $checkPid; $depth++) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $checkPid" -ErrorAction Stop
        if (-not $proc) { break }
        if ($proc.ProcessId -eq $servicePid) { $serviceFound = $true }
        if ($wheelMarker -and $proc.Name -ieq 'uvx.exe' -and $proc.CommandLine -and
            $proc.CommandLine.IndexOf($wheelMarker, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $wheelFound = $true
        }
        $checkPid = [int]$proc.ParentProcessId
    }
    return ($serviceFound -and $wheelFound)
}

function Wait-Healthy([int]$seconds, [string]$wheelMarker = '') {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $service = Get-CimInstance Win32_Service -Filter "Name = '$serviceName'" -ErrorAction Stop
            if ($service.State -eq 'Running' -and $service.ProcessId -gt 0) {
                $ownerPid = Get-PortOwner
                $version = (Invoke-RestMethod -Uri "$baseUrl/api/version" -TimeoutSec 4).version
                $config = Invoke-WebRequest -Uri "$baseUrl/api/config" -UseBasicParsing -TimeoutSec 4
                $page = Invoke-WebRequest -Uri "$baseUrl/" -UseBasicParsing -TimeoutSec 4
                if ($version -eq '0.13.4' -and $config.StatusCode -eq 200 -and $page.StatusCode -eq 200 -and
                    (Test-ProcessChain $ownerPid ([int]$service.ProcessId) $wheelMarker)) {
                    return $ownerPid
                }
            }
        } catch { }
        Start-Sleep -Seconds 3
    }
    throw "OpenWebUI did not become healthy under $serviceName within $seconds seconds."
}

function Stop-ServiceAndWait {
    $serviceInfo = Get-CimInstance Win32_Service -Filter "Name = '$serviceName'" -ErrorAction Stop
    $rootPid = if ($serviceInfo.ProcessId -gt 0) { [int]$serviceInfo.ProcessId } else { $script:lastServicePid }
    if ($serviceInfo.State -eq 'Stopped') {
        $portOwners = @(Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique)
        if ($portOwners.Count -eq 1) {
            $checkPid = [int]$portOwners[0]
            $orphanRoot = 0
            $webuiCommandFound = $false
            for ($depth = 0; $depth -lt 12 -and $checkPid; $depth++) {
                $checkProc = Get-CimInstance Win32_Process -Filter "ProcessId = $checkPid" -ErrorAction SilentlyContinue
                if (-not $checkProc) { break }
                if ($checkProc.Name -ieq 'uvx.exe' -and $checkProc.CommandLine -and
                    ($checkProc.CommandLine.Contains('open-webui@latest') -or
                     $checkProc.CommandLine.IndexOf($WheelPath, [StringComparison]::OrdinalIgnoreCase) -ge 0)) {
                    $webuiCommandFound = $true
                }
                if ($checkProc.Name -ieq 'nssm.exe') { $orphanRoot = [int]$checkProc.ProcessId }
                $checkPid = [int]$checkProc.ParentProcessId
            }
            if ($webuiCommandFound -and $orphanRoot -gt 0) { $rootPid = $orphanRoot }
        }
    }
    if ($rootPid -le 0) { throw 'Service process tree could not be identified; refusing to stop it.' }
    $script:lastServicePid = $rootPid
    $before = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $known = [System.Collections.Generic.List[object]]::new()
    $knownFrontier = @($rootPid)
    for ($depth = 0; $depth -lt 12 -and $knownFrontier.Count -gt 0; $depth++) {
        $children = @($before | Where-Object { $knownFrontier -contains [int]$_.ParentProcessId })
        foreach ($child in $children) { $known.Add($child) }
        $knownFrontier = @($children | Select-Object -ExpandProperty ProcessId)
    }
    if ($serviceInfo.State -ne 'Stopped') {
        Stop-Service -Name $serviceName -Force -ErrorAction Stop
        (Get-Service -Name $serviceName).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(45))
    }

    # NSSM can report Stopped while spawned PowerShell/uvx/Python children remain.
    # Walk the old root's parent IDs even after NSSM exits, then stop only those
    # SYSTEM-owned application processes before another copy is allowed to start.
    $deadline = (Get-Date).AddSeconds(30)
    $clearSince = $null
    while ((Get-Date) -lt $deadline) {
        $all = @(Get-CimInstance Win32_Process -ErrorAction Stop)
        $frontier = @($rootPid)
        $descendants = [System.Collections.Generic.List[object]]::new()
        for ($depth = 0; $depth -lt 12 -and $frontier.Count -gt 0; $depth++) {
            $children = @($all | Where-Object { $frontier -contains [int]$_.ParentProcessId })
            foreach ($child in $children) { $descendants.Add($child) }
            $frontier = @($children | Select-Object -ExpandProperty ProcessId)
        }
        $ordered = @($known.ToArray()) + @($descendants.ToArray())
        [array]::Reverse($ordered)
        foreach ($proc in $ordered) {
            if ($proc.Name -notin @('powershell.exe', 'uvx.exe', 'uv.exe', 'open-webui.exe', 'python.exe')) {
                continue
            }
            $owner = Invoke-CimMethod -InputObject $proc -MethodName GetOwner -ErrorAction SilentlyContinue
            if (($owner.Domain + '\' + $owner.User) -ine 'NT AUTHORITY\SYSTEM') { continue }
            $stillThere = Get-CimInstance Win32_Process -Filter "ProcessId = $($proc.ProcessId)" -ErrorAction SilentlyContinue
            if ($stillThere -and $stillThere.CreationDate -eq $proc.CreationDate) {
                Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
        $rootProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $rootPid" -ErrorAction SilentlyContinue
        if ($rootProcess -and $rootProcess.Name -ieq 'nssm.exe' -and
            (Get-Service -Name $serviceName).Status -eq 'Stopped') {
            $rootOwner = Invoke-CimMethod -InputObject $rootProcess -MethodName GetOwner -ErrorAction SilentlyContinue
            if (($rootOwner.Domain + '\' + $rootOwner.User) -ieq 'NT AUTHORITY\SYSTEM') {
                Stop-Process -Id $rootPid -Force -ErrorAction SilentlyContinue
            }
        }
        $listeners = @(Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction SilentlyContinue)
        $remaining = @($ordered | Where-Object {
            $active = Get-CimInstance Win32_Process -Filter "ProcessId = $($_.ProcessId)" -ErrorAction SilentlyContinue
            $active -and $active.CreationDate -eq $_.CreationDate -and
                $active.Name -in @('powershell.exe', 'uvx.exe', 'uv.exe', 'open-webui.exe', 'python.exe')
        })
        if ($listeners.Count -eq 0 -and $remaining.Count -eq 0 -and -not
            (Get-CimInstance Win32_Process -Filter "ProcessId = $rootPid" -ErrorAction SilentlyContinue)) {
            if (-not $clearSince) { $clearSince = Get-Date }
            if (((Get-Date) - $clearSince).TotalSeconds -ge 6) { return }
        } else {
            $clearSince = $null
        }
        Start-Sleep -Seconds 2
    }
    throw 'Old OpenWebUI child processes or port 80 remained active after NSSM stopped. No second instance was started.'
}

function Start-ServiceAndWait([string]$wheelMarker = '') {
    Start-Service -Name $serviceName -ErrorAction Stop
    (Get-Service -Name $serviceName).WaitForStatus('Running', [TimeSpan]::FromSeconds(45))
    $script:lastServicePid = [int](Get-CimInstance Win32_Service -Filter "Name = '$serviceName'" -ErrorAction Stop).ProcessId
    return Wait-Healthy 180 $wheelMarker
}

try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this service cutover from an elevated Administrator PowerShell.'
    }
    if (-not (Test-Path -LiteralPath $WheelPath -PathType Leaf)) { throw "Wheel missing: $WheelPath" }
    if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) { throw "Launcher missing: $launcher" }
    if (-not (Test-Path -LiteralPath $db -PathType Leaf)) { throw "Database missing: $db" }
    if ((Get-FileHash -LiteralPath $WheelPath -Algorithm SHA256).Hash -ne $expectedWheelHash) {
        throw 'Wheel hash differs from the locally reviewed artifact.'
    }
    if ((Get-FileHash -LiteralPath $launcher -Algorithm SHA256).Hash -ne $expectedLauncherHash) {
        throw 'Live launcher changed since the read-only preflight. No change was made.'
    }
    $service = Get-CimInstance Win32_Service -Filter "Name = '$serviceName'" -ErrorAction Stop
    if (-not $service -or $service.State -ne 'Running' -or $service.ProcessId -le 0) {
        throw "$serviceName is not running. No change was made."
    }
    if ($service.StartName -notin @('LocalSystem', 'NT AUTHORITY\SYSTEM')) {
        throw "Unexpected service account: $($service.StartName). No change was made."
    }
    $previousListener = Get-PortOwner
    if (-not (Test-ProcessChain $previousListener ([int]$service.ProcessId))) {
        throw 'Port 80 is not owned by the expected OpenWebUIService process tree.'
    }
    $ownerProc = Get-CimInstance Win32_Process -Filter "ProcessId = $previousListener" -ErrorAction Stop
    $owner = Invoke-CimMethod -InputObject $ownerProc -MethodName GetOwner -ErrorAction Stop
    if (($owner.Domain + '\' + $owner.User) -ine 'NT AUTHORITY\SYSTEM') {
        throw 'Port 80 is not running as SYSTEM. No change was made.'
    }
    if ((Invoke-RestMethod -Uri "$baseUrl/api/version" -TimeoutSec 8).version -ne '0.13.4') {
        throw 'Live OpenWebUI version changed. No change was made.'
    }
    $originalText = Get-Content -LiteralPath $launcher -Raw
    if ([regex]::Matches($originalText, 'open-webui@latest').Count -ne 1) {
        throw 'Launcher must contain exactly one open-webui@latest command.'
    }
    $writeProbe = [IO.File]::Open($launcher, [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
    $writeProbe.Dispose()

    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    $userSid = $identity.User.Value
    & icacls.exe $backupDir /inheritance:r /grant:r `
        ('*' + $userSid + ':(OI)(CI)F') '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not restrict the backup folder. No live change was made.' }
    Copy-Item -LiteralPath $launcher -Destination $launcherBackup -ErrorAction Stop
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
    $pythonExe = (Get-Command python -ErrorAction Stop | Select-Object -First 1).Source
    & $pythonExe -c $backupPy $db (Join-Path $backupDir 'webui.db')
    if ($LASTEXITCODE -ne 0) { throw 'SQLite online backup failed. No live change was made.' }
    Write-Host "Launcher and database backups saved in $backupDir"

    & (Join-Path $PSScriptRoot 'Run-OpenWebUIStateCheck.ps1') -Phase Before
    if ($LASTEXITCODE -ne 0) { throw 'Before state check failed. No live change was made.' }

    $replacement = '--from "' + $WheelPath + '" open-webui'
    $customText = '$env:UV_OFFLINE = "1"' + [Environment]::NewLine +
        '$env:HF_HUB_OFFLINE = "1"' + [Environment]::NewLine +
        $originalText.Replace('open-webui@latest', $replacement)
    $launcherChanged = $true
    Set-Content -LiteralPath $launcher -Value $customText -Encoding UTF8
    Write-Host 'Custom launcher written. Stopping OpenWebUIService...'
    $serviceRestarted = $true
    Stop-ServiceAndWait
    Write-Host 'Starting OpenWebUIService with the custom wheel; uv and Hugging Face downloads are disabled...'
    $newPid = Start-ServiceAndWait $WheelPath
    Write-Host "Custom OpenWebUI is healthy on port 80 (PID $newPid)."

    & (Join-Path $PSScriptRoot 'Run-OpenWebUIStateCheck.ps1') -Phase After
    if ($LASTEXITCODE -ne 0) { throw 'After state check found a new difference.' }
    Write-Host 'SUCCESS: Custom OpenWebUI 0.13.4 is live under OpenWebUIService as SYSTEM.'
    Write-Host 'CW Pay PDF and Excel actions still need a browser check with a real result.'
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
                Copy-Item -LiteralPath $launcherBackup -Destination $launcher -Force
                $launcherRestored = $true
                Write-Host 'Original launcher restored.'
            } catch {
                Write-Host "ROLLBACK NEEDS ATTENTION: Could not restore launcher: $($_.Exception.Message)" -ForegroundColor Red
                Write-Host "Original copy: $launcherBackup"
            }
        }
    }
    if ($serviceRestarted -and $launcherRestored) {
        try {
            Stop-ServiceAndWait
            $restoredPid = Start-ServiceAndWait
            Write-Host "ROLLBACK HEALTHY: Original OpenWebUI is responding on port 80 (PID $restoredPid)."
        } catch {
            Write-Host "ROLLBACK NEEDS ATTENTION: $($_.Exception.Message)" -ForegroundColor Red
            Write-Host 'Check the service status and E:\apps\open-webui\logs\transcript.log.'
        }
    } elseif (-not $serviceRestarted) {
        Write-Host 'OpenWebUIService was not restarted by this script.'
    }
    exit 1
}
