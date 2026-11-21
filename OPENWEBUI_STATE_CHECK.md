# OpenWebUI before/after state check

Run on the Windows OpenWebUI server from this repository directory. Both commands are read-only with respect to OpenWebUI. They do not update or restart the service.

```powershell
.\Run-OpenWebUIStateCheck.ps1 -Phase Before
# Perform the separately approved OpenWebUI update.
.\Run-OpenWebUIStateCheck.ps1 -Phase After
```

The commands need no per-model or per-tool inputs. They use the production database at `C:\downloads\open-webui\data\webui.db` and the local service at `http://127.0.0.1`. The script finds Python automatically. Override `-DbPath`, `-BaseUrl`, `-StoreDir`, or `-PythonExe` only when those locations differ. Python 3.10+ is required.

Each Before run creates a dated folder under `E:\apps\openwebui-private-checks`. `before.dpapi`, `after.dpapi`, and `comparison.dpapi` are encrypted with Windows DPAPI for the Windows user that ran the command. Run both phases under that same Windows account. `current-run.txt` contains only the dated folder name. **Do not push this private directory to GitHub.**

If Before reports a failed provider probe, run `.\Show-OpenWebUIStateCheck.ps1` under that same Windows account. It repeats only the failed read-only provider probe and prints the provider index, error category, and network error code, without displaying URLs or API keys. A failed model-list probe is recorded as a degraded baseline. If the same failure remains after the update, the comparison reports it as pre-existing and unverified; a new or changed failure requires review.

The check compares stored OpenWebUI configuration, model/function/tool definitions, groups, prompts, knowledge, skills, users and access grants. It ignores creation/update timestamps and normalizes JSON fields. It checks the two public service endpoints, makes read-only model-list requests to enabled OpenAI-compatible and Ollama connections, and parses stored function/tool code for syntax errors without executing it. A failed endpoint, syntax error, changed model list, or changed configuration produces `REVIEW REQUIRED` and a nonzero exit code. The encrypted report has the specific differences.

This does **not** establish that a chat completion works, that a pipeline runs, or that a function/tool action is safe and successful. Those actions are reported as `NOT VERIFIED`; a configuration match is not a release approval. Some providers do not support model-list probes and may require separate checks.

To read an encrypted comparison on the same server as the same Windows user:

```powershell
Add-Type -AssemblyName System.Security
$root = 'E:\apps\openwebui-private-checks'
$run = (Get-Content (Join-Path $root 'current-run.txt') -Raw).Trim()
$bytes = [Security.Cryptography.ProtectedData]::Unprotect(
    [IO.File]::ReadAllBytes((Join-Path $root "$run\comparison.dpapi")),
    $null,
    [Security.Cryptography.DataProtectionScope]::CurrentUser
)
try { [Text.Encoding]::UTF8.GetString($bytes) }
finally { [Array]::Clear($bytes, 0, $bytes.Length) }
```
