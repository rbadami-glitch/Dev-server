$ErrorActionPreference = 'Stop'
$serviceName = 'OpenWebUIService'
$launcher = 'E:\apps\open-webui\start-openwebui.ps1'
$originalCopy = 'E:\apps\open-webui\deployment-backups\20261121-040151\start-openwebui.original.ps1'
$expectedHash = '9066F07E5D435F1C5B5EA7688977353C1FA2EF56641234AF5495497BBF8AD030'
$knownOrphanNssmPid = 81184
$launcherTemporarilyChanged = $false

try {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this recovery from an elevated Administrator PowerShell.'
    }
    foreach ($path in @($launcher, $originalCopy)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "File missing: $path" }
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $expectedHash) {
            throw "Original launcher hash mismatch: $path"
        }
    }
    $service = Get-CimInstance Win32_Service -Filter "Name = '$serviceName'" -ErrorAction Stop
    if ($service.State -ne 'Stopped' -or $service.ProcessId -ne 0) {
        throw "Service state changed to $($service.State), PID $($service.ProcessId). Reinspect before recovery."
    }
    $owners = @(Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction Stop |
        Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -ne 1) { throw 'Port 80 does not have exactly one owner.' }
    $checkPid = [int]$owners[0]
    $orphanFound = $false
    $latestFound = $false
    for ($depth = 0; $depth -lt 12 -and $checkPid; $depth++) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $checkPid" -ErrorAction Stop
        if (-not $proc) { break }
        if ($proc.Name -ieq 'uvx.exe' -and $proc.CommandLine -like '*open-webui@latest*') {
            $latestFound = $true
        }
        if ($proc.Name -ieq 'nssm.exe' -and $proc.ProcessId -eq $knownOrphanNssmPid) {
            $orphanFound = $true
        }
        $checkPid = [int]$proc.ParentProcessId
    }
    if (-not ($orphanFound -and $latestFound)) {
        throw 'Port 80 owner no longer matches the identified orphaned @latest NSSM tree.'
    }
    if ((Invoke-RestMethod -Uri 'http://127.0.0.1/api/version' -TimeoutSec 8).version -ne '0.13.4') {
        throw 'Original OpenWebUI is no longer responding as version 0.13.4.'
    }

    $originalText = Get-Content -LiteralPath $launcher -Raw
    $temporaryText = '$env:UV_OFFLINE = "1"' + [Environment]::NewLine +
        '$env:HF_HUB_OFFLINE = "1"' + [Environment]::NewLine + $originalText
    $launcherTemporarilyChanged = $true
    Set-Content -LiteralPath $launcher -Value $temporaryText -Encoding UTF8
    Write-Host 'Temporary offline flags written to original launcher for this one restart.'

    & taskkill.exe /PID $knownOrphanNssmPid /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the orphaned NSSM process tree.' }
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if (@(Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction SilentlyContinue).Count -eq 0) { break }
        Start-Sleep -Seconds 2
    }
    if (@(Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction SilentlyContinue).Count -ne 0) {
        throw 'Port 80 remained occupied after stopping the orphan. Service was not started.'
    }

    Start-Service -Name $serviceName -ErrorAction Stop
    (Get-Service -Name $serviceName).WaitForStatus('Running', [TimeSpan]::FromSeconds(45))
    $deadline = (Get-Date).AddMinutes(3)
    $healthy = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $live = Invoke-RestMethod -Uri 'http://127.0.0.1/api/version' -TimeoutSec 4
            $running = Get-CimInstance Win32_Service -Filter "Name = '$serviceName'" -ErrorAction Stop
            if ($live.version -eq '0.13.4' -and $running.State -eq 'Running' -and $running.ProcessId -gt 0) {
                $portOwners = @(Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction Stop |
                    Select-Object -ExpandProperty OwningProcess -Unique)
                if ($portOwners.Count -eq 1) {
                    $processIdToCheck = [int]$portOwners[0]
                    for ($depth = 0; $depth -lt 12 -and $processIdToCheck; $depth++) {
                        $processToCheck = Get-CimInstance Win32_Process -Filter "ProcessId = $processIdToCheck" -ErrorAction Stop
                        if (-not $processToCheck) { break }
                        if ($processToCheck.ProcessId -eq $running.ProcessId) {
                            $healthy = $true
                            break
                        }
                        $processIdToCheck = [int]$processToCheck.ParentProcessId
                    }
                    if ($healthy) { break }
                }
            }
        } catch { }
        Start-Sleep -Seconds 3
    }
    if (-not $healthy) { throw 'Original service did not become healthy within 3 minutes.' }
    Write-Host 'RECOVERY HEALTHY: Original OpenWebUI 0.13.4 is running under OpenWebUIService.'
} catch {
    Write-Host "RECOVERY NEEDS ATTENTION: $($_.Exception.Message)" -ForegroundColor Red
} finally {
    if ($launcherTemporarilyChanged) {
        try {
            Copy-Item -LiteralPath $originalCopy -Destination $launcher -Force -ErrorAction Stop
            if ((Get-FileHash -LiteralPath $launcher -Algorithm SHA256).Hash -ne $expectedHash) {
                throw 'Restored launcher hash mismatch.'
            }
            Write-Host 'Original launcher restored byte-for-byte.'
        } catch {
            Write-Host "LAUNCHER RESTORE NEEDS ATTENTION: $($_.Exception.Message)" -ForegroundColor Red
        }
    }
    $finalService = Get-CimInstance Win32_Service -Filter "Name = '$serviceName'" -ErrorAction SilentlyContinue
    Write-Host "Final service state: $($finalService.State); PID $($finalService.ProcessId)"
}
