# LiteLLM request evidence UI

This folder contains a Windows desktop UI and backend for collecting one
LiteLLM request by its LiteLLM Logs Request ID. The UI is restricted to the
INT\v_rbadami Windows account. It does not modify or restart LiteLLM or vLLM.

The GitHub repository rbadami-glitch/Dev-server is public. The tool encrypts
the complete spend-log row in memory before writing or pushing it. The private
decryption key stays on the analysis PC under
%APPDATA%\CWAI\litellm-evidence-private.pem. Never copy that private key
to the server, GitHub, email, or a screenshot. The public key in this repo
is safe to distribute. Files under encrypted-evidence/ contain ciphertext.

## One-time setup on the analysis PC

Use Python 3.12 or newer. The public key has already been generated for this
repository; keep the matching private key in the current Windows profile.

~~~powershell
Set-Location 'C:\Users\HP\Documents\etag\Documentation\dev-server\Github'
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-evidence.txt
~~~

Do not run init-key again after the first key has been created: a new key
cannot decrypt evidence encrypted to the current public key.

## One-time setup on the Windows LiteLLM server

Sign in as INT\v_rbadami. Clone or pull this Dev-server repository into a
folder accessible to that account. Use a separate Python environment so the
running LiteLLM installation is untouched:

~~~powershell
Set-Location '<your Dev-server checkout>'
.\Start-LiteLLM-Request-Evidence.cmd
~~~

The exporter reads the existing master key from
D:\git pull for sanjay\commands\litellm-proxy\.env without displaying it.
The Windows account needs read access to that file and Git push access to
https://github.com/rbadami-glitch/Dev-server.git. The tool creates a fresh
temporary Git clone for each publish/delete, so it does not alter the
existing LiteLLM checkout or unrelated Dev-server work.

## Collect and publish one request

~~~powershell
Set-Location '<your Dev-server checkout>'
.\Start-LiteLLM-Request-Evidence.cmd
~~~

1. In LiteLLM Logs, copy the Request ID for the exact request. It often
   starts with chatcmpl-. A PCB-internal ID that PCB did not forward to
   LiteLLM cannot be used.
2. Paste that one ID into the UI and click Collect and publish encrypted
   evidence. No key, prompt, answer, or decrypted metadata appears in the UI.
3. Copy the GitHub link shown by the UI and send that link for analysis. The
   filename is a SHA-256 digest of the ID; it does not contain the ID itself.

The tool fetches the exact matching row from LiteLLM /spend/logs and any
stored request/response details from /spend/logs/ui/{request_id}. The
installed LiteLLM version requires start and end dates for /spend/logs/v2,
so it cannot be queried with only the pasted ID. The exact-ID lookup does
not collect unrelated users' requests or broad server logs. Fields that
LiteLLM did not store for the call cannot be recovered.

## Decrypt and analyze on the analysis PC

Pull the repository, then decrypt the linked ciphertext file to a path
outside the public repository:

~~~powershell
Set-Location 'C:\Users\HP\Documents\etag\Documentation\dev-server\Github'
git pull --ff-only
.\.venv\Scripts\python.exe .\litellm_request_evidence.py decrypt '.\encrypted-evidence\<filename-from-link>.json' (Join-Path $env:LOCALAPPDATA 'CWAI\litellm-evidence\request.json')
~~~

Only the path to the decrypted file is printed. Treat the file as
confidential; it may contain key hashes, IP addresses, prompts, or answers.
Never commit or paste the raw JSON into GitHub, chat, email, or screenshots.
The local private key is needed to decrypt; the server does not have it.

## Delete after analysis

After analysis, return to the server UI with the same Request ID and click
Delete published evidence. The UI asks for confirmation, removes the
encrypted file from the current GitHub branch, pushes a deletion commit,
and shows the commit ID. Also delete the local decrypted copy when finished.

Git history retains earlier encrypted versions even after this deletion.
Because the public repository cannot forget past commits through a normal
delete, the private key must remain protected. Contact the repository
owner if history purging is required.

## If something fails

- No matching Request ID: use the ID from LiteLLM Logs, not a PCB-only ID.
- HTTP 400 from a copy installed before this fix: close the UI, run
  `git pull --ff-only` inside the server's Dev-server checkout, and launch
  `Start-LiteLLM-Request-Evidence.cmd` again. The old version called
  /spend/logs/v2 without its required dates.
- Could not reach LiteLLM: run the UI on the Windows LiteLLM server; it
  queries 127.0.0.1:4000.
- Git push failed: sign in to GitHub for Git on the server, then retry.
  The exporter does not leave a plaintext report or modify the service.
- Already published: use the existing encrypted file. The tool does not
  silently overwrite evidence.

## Locate the 19 November PCB schema incident

On the Windows LiteLLM server, pull this repository and run the following
command in its checkout. It matches PCB's three calls by the reported UTC
window, prompt-token counts, and server-local PCB key aliases. It refuses to
publish if any stage has zero or multiple matches.

~~~powershell
.\.venv\Scripts\python.exe .\pcb_schema_incident.py --publish
~~~

The script prints the three LiteLLM Request IDs and encrypted GitHub links.
It does not print prompts, answers, keys, or the full request rows. After the
links have been analyzed, delete each published request using the existing
evidence UI. Omit `--publish` for a read-only lookup.
