# OpenWebUI deployment preflight

Run this on the Windows server from a clean checkout of this Dev-server repo:

```powershell
git -c http.sslBackend=openssl pull --ff-only origin main
.\Collect-OpenWebUIPreflight.ps1 -Publish
```

This first step only checks the server environment and current OpenWebUI
launcher. It writes a report in `openwebui-deployment-reports/`, commits that
report, and pushes it to `main` for analysis on the local PC. It does not stop,
install, or modify the live service. The report contains no launcher contents,
application data, credentials, or environment-variable values.

If the push fails, leave the report in the server checkout and share the error.
