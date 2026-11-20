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

## Build the isolated custom candidate

After the preflight report has been analyzed, run from the same server checkout:

```powershell
git -c http.sslBackend=openssl pull --ff-only origin main
.\Build-OpenWebUICandidate.ps1 -Publish
```

This clones official OpenWebUI v0.13.4 into
`E:\apps\open-webui-custom-candidate-0.13.4`, applies the custom feature bundle,
builds a wheel, runs the focused PDF test, and publishes the build transcript.
The live launcher and live data are not changed. A failed build also publishes
its transcript for diagnosis. Review this public log before sharing its link.
