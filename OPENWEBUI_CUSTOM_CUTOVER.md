# CW Pay PDF and Excel cutover

The reviewed candidate is OpenWebUI 0.13.4 with the CW Pay PDF export and table Excel export. Its wheel is already on the Windows server at `E:\AI\ollama\OpenWebUI-Monitoring\Openwebui-candidate\open_webui-0.13.4-py3-none-any.whl`. Expected SHA-256: `B4774D87E682A34274A9BCF4C4293F0C4581C11CEA0F7D7F667727CAADFC9818`.

The live launcher is owned by Administrators and gives ordinary Users read/execute access only. The first cutover attempt stopped before restart because the ordinary user could not write it; the original launcher hash and live version remained unchanged. The cutover script now checks write access before backup or restart and refuses to run elevated, so OpenWebUI continues under its existing regular user account. A separate admin helper grants that same user temporary Modify access to this one launcher file, then removes it after the cutover.

1. From **elevated PowerShell under the same Windows account that currently runs OpenWebUI**:

```powershell
Set-Location 'C:\Users\v_rbadami\Dev-server-openwebui'
git -c http.sslBackend=openssl pull --ff-only origin main
.\Set-OpenWebUILauncherAccess.ps1 -Action Grant
```

2. From a **separate, regular (not elevated) PowerShell** under that account, distinct from the current live launcher window:

```powershell
Set-Location 'C:\Users\v_rbadami\Dev-server-openwebui'
.\Install-OpenWebUICustom.ps1
```

3. After the cutover script ends, whether it reports success or failure, return to the elevated window:

```powershell
.\Set-OpenWebUILauncherAccess.ps1 -Action Revoke
```

The ACL helper checks the running service account before granting access. It changes permissions only on `E:\apps\open-webui\start-openwebui.ps1`; it does not take ownership or change its content. It refuses to grant if the original launcher hash changed or an explicit access entry already exists for the current user. The revoke step removes the temporary explicit grant and leaves inherited Users read/execute access intact.

The script verifies the exact wheel and current launcher hashes and the live version before making changes. It restricts a dated rollback folder to the current Windows user, SYSTEM, and Administrators; saves the original launcher; makes an online SQLite backup; and runs a fresh encrypted Before state check. It replaces only the `open-webui@latest` argument in the existing launcher and adds `UV_OFFLINE=1` and `HF_HUB_OFFLINE=1`, so uv and Hugging Face cannot download packages or models during startup. The existing `DATA_DIR`, Ollama, LibreOffice, host, and port settings remain in the launcher. It stops the existing uvx process tree, starts the custom launcher, checks the version and page endpoints, confirms that the new process points to the candidate wheel, and runs the existing After state check. If startup or comparison fails, it restores the original launcher and attempts to restart it with uv and Hugging Face offline.

The script changes the live service and may cause several minutes of interruption. The server-side candidate startup test has not passed; its earlier attempt timed out while trying to fetch the default Hugging Face model. This cutover does not rerun that test. The local frontend build and focused PDF test passed. The state check does not execute actual CW Pay queries, functions, or tools; confirm the PDF and Excel actions from a real CW Pay result in the browser after a successful cutover.

After a successful switch, OpenWebUI is pinned to the custom 0.13.4 wheel. The former `@latest` automatic update behavior is suspended. A future upstream release needs a fresh custom build and reviewed cutover; the local update checker can alert to releases but cannot merge the custom changes automatically.

Rollback copies are under `E:\apps\open-webui\deployment-backups\<timestamp>`. The SQLite backup is retained for emergency recovery but is **not** automatically restored, because doing that after users resume work could discard new data. The script restores only the launcher and process on failure.
