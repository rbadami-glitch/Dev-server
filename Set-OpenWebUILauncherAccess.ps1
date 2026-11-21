param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Grant', 'Revoke')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'
$launcher = 'E:\apps\open-webui\start-openwebui.ps1'
$expectedOriginalHash = '9066F07E5D435F1C5B5EA7688977353C1FA2EF56641234AF5495497BBF8AD030'
$candidateWheel = 'E:\AI\ollama\OpenWebUI-Monitoring\Openwebui-candidate\open_webui-0.13.4-py3-none-any.whl'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Open PowerShell as Administrator under the same Windows account that runs OpenWebUI.'
}
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) { throw "Launcher missing: $launcher" }

if ($Action -eq 'Grant') {
    $owners = @(Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction Stop |
        Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -ne 1) { throw 'Expected exactly one port 80 owner.' }
    $liveProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($owners[0])" -ErrorAction Stop
    $liveOwner = Invoke-CimMethod -InputObject $liveProcess -MethodName GetOwner -ErrorAction Stop
    if (($liveOwner.Domain + '\' + $liveOwner.User) -ine $identity.Name) {
        throw "Current account $($identity.Name) differs from the live OpenWebUI account. No ACL was changed."
    }
}

$acl = Get-Acl -LiteralPath $launcher
$sid = $identity.User.Value
$explicitCurrentUser = @($acl.Access | Where-Object {
    if ($_.IsInherited) { return $false }
    try {
        $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq $sid
    } catch { $false }
})

if ($Action -eq 'Grant') {
    if ((Get-FileHash -LiteralPath $launcher -Algorithm SHA256).Hash -ne $expectedOriginalHash) {
        throw 'Original launcher hash changed. Review it before granting access.'
    }
    if ($explicitCurrentUser.Count -gt 0) {
        throw 'Current account already has an explicit launcher ACL entry. Review it manually; no ACL was changed.'
    }
    & icacls.exe $launcher /grant ('*' + $sid + ':(M)')
    if ($LASTEXITCODE -ne 0) { throw 'Could not grant temporary Modify access.' }
    Write-Host 'Temporary launcher Modify access granted to the existing OpenWebUI account.'
} else {
    if ($explicitCurrentUser.Count -eq 0) {
        Write-Host 'No explicit current-user ACL entry exists; nothing to revoke.'
        return
    }
    if ((Get-FileHash -LiteralPath $launcher -Algorithm SHA256).Hash -ne $expectedOriginalHash) {
        $owners = @(Get-NetTCPConnection -LocalPort 80 -State Listen -ErrorAction Stop |
            Select-Object -ExpandProperty OwningProcess -Unique)
        if ($owners.Count -ne 1) { throw 'Custom launcher is active but port 80 is not uniquely owned. Keep the grant for recovery.' }
        $checkPid = [int]$owners[0]
        $wheelMatched = $false
        for ($depth = 0; $depth -lt 12 -and $checkPid; $depth++) {
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $checkPid" -ErrorAction Stop
            if (-not $proc) { break }
            if ($proc.Name -ieq 'uvx.exe' -and $proc.CommandLine -and
                $proc.CommandLine.IndexOf($candidateWheel, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                $wheelMatched = $true
                break
            }
            $checkPid = [int]$proc.ParentProcessId
        }
        if (-not $wheelMatched) { throw 'Custom launcher is active but the candidate wheel process was not found. Keep the grant for recovery.' }
        if ((Invoke-RestMethod -Uri 'http://127.0.0.1/api/version' -TimeoutSec 8).version -ne '0.13.4') {
            throw 'Custom service health could not be confirmed. Keep the grant for recovery.'
        }
    }
    & icacls.exe $launcher /remove:g ('*' + $sid)
    if ($LASTEXITCODE -ne 0) { throw 'Could not revoke temporary Modify access.' }
    Write-Host 'Temporary launcher access revoked. Inherited Users read/execute access remains.'
}
