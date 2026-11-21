# CW Pay PDF and Excel cutover

The live OpenWebUI is an NSSM Windows service named `OpenWebUIService`. Its process runs as SYSTEM. The custom 0.13.4 wheel is already on the server at `E:\AI\ollama\OpenWebUI-Monitoring\Openwebui-candidate\open_webui-0.13.4-py3-none-any.whl`, with SHA-256 `B4774D87E682A34274A9BCF4C4293F0C4581C11CEA0F7D7F667727CAADFC9818`.

The first cutover attempt stopped before restart because the ordinary user could not write the Administrators-owned launcher. A proposed temporary ACL helper also stopped without changing permissions after it correctly detected that the live service runs as SYSTEM. Use neither earlier script. The service-based script below keeps the existing service account and does not grant users new access to the launcher.

From an **elevated Administrator PowerShell** on the server:

```powershell
Set-Location 'C:\Users\v_rbadami\Dev-server-openwebui'
git -c http.sslBackend=openssl pull --ff-only origin main
.\Deploy-OpenWebUIServiceCustom.ps1
```

The script verifies the wheel and launcher hashes, live version, service name, SYSTEM account, and port 80 process ancestry before making changes. It stores the original launcher and an online SQLite database backup in a dated folder restricted to the current user, SYSTEM, and Administrators. It runs a fresh encrypted Before state check. It changes only the `open-webui@latest` argument in the existing launcher and adds `UV_OFFLINE=1` and `HF_HUB_OFFLINE=1`, so uv and Hugging Face cannot fetch packages or models. It then restarts `OpenWebUIService`, checks the version and page endpoints and the wheel in the new uvx process command, and runs the existing After state check. On failure it restores the original launcher and attempts to restart the original service.

This changes the live service and can interrupt it for several minutes. The server-side candidate startup test has not passed; its earlier attempt timed out while fetching the default Hugging Face model. The local frontend build and focused PDF test passed. The state check does not execute actual CW Pay queries, functions, or tools. Confirm the PDF and Excel actions from a real CW Pay result in the browser after a successful cutover.

After a successful switch, OpenWebUI is pinned to the custom 0.13.4 wheel. The old `@latest` automatic update behavior is suspended. A future upstream release needs a new custom build and reviewed cutover; the local update checker can alert to releases but cannot merge changes automatically.

Rollback files are under `E:\apps\open-webui\deployment-backups\<timestamp>`. The SQLite backup is retained for emergency recovery but is **not** automatically restored, because restoring it after users resume work could discard new data. The script restores only the launcher and service on failure.
