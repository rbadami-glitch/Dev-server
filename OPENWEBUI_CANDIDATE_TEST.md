# Isolated OpenWebUI candidate test

The wheel copied to `E:\AI\ollama\OpenWebUI-Monitoring\Openwebui-candidate` has SHA-256 `B4774D87E682A34274A9BCF4C4293F0C4581C11CEA0F7D7F667727CAADFC9818`.

From the Dev-server checkout on the Windows server:

```powershell
git -c http.sslBackend=openssl pull --ff-only origin main
.\Test-OpenWebUICandidate.ps1
```

The script checks the wheel hash before starting it. It uses a separate `DATA_DIR` at `E:\apps\open-webui-candidate-test-data`, binds only to `127.0.0.1:8082`, waits for its version endpoint, checks its config and page endpoints, compares the live version and launcher hash before/after, and stops the candidate process tree. It sets `UV_OFFLINE=1`, `OFFLINE_MODE=true`, and `HF_HUB_OFFLINE=1` for the candidate process so uv cannot fetch packages and OpenWebUI cannot fetch the default Hugging Face embedding model. If required packages are not already cached, the test fails quickly instead of downloading them. The test data and startup logs remain on the server. If it fails, share the `FAIL:` line and the end of the candidate error log; do not rerun blindly.

This is a startup smoke test. It does not exercise CW Pay, live connections, existing users, tools, or production data. The focused PDF test passed locally on the exact patched source.

## Production boundary

The live launcher currently runs `open-webui@latest` against `C:\downloads\open-webui\data`. It will not use this wheel without a launcher change. A launcher that runs the local wheel would pin OpenWebUI to 0.13.4; it would not preserve the current automatic `@latest` update behavior by itself. Production deployment therefore requires a separate reviewed cutover and update-monitor plan. No script in this repository changes the live launcher or data as part of this candidate test.
