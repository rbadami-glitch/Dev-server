"""Collect one LiteLLM spend-log record and publish it encrypted to GitHub.

The GitHub repository is public. Never write a plaintext record into it.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


SCRIPT_DIR = Path(__file__).resolve().parent
REMOTE_URL = "https://github.com/rbadami-glitch/Dev-server.git"
BRANCH = "main"
PUBLIC_KEY_PATH = SCRIPT_DIR / "litellm_evidence_public.pem"
PRIVATE_KEY_PATH = Path(os.environ.get("APPDATA", str(Path.home()))) / "CWAI" / "litellm-evidence-private.pem"
DEFAULT_ENV_FILE = Path(r"D:\git pull for sanjay\commands\litellm-proxy\.env")
GATEWAY_URL = "http://127.0.0.1:4000"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{8,180}$")
MAX_API_RESPONSE_BYTES = 20 * 1024 * 1024
EXPECTED_WINDOWS_ACCOUNT = r"INT\v_rbadami"


class EvidenceError(RuntimeError):
    """A safe-to-display error without credentials or request content."""


def check_operator_account() -> None:
    if os.name != "nt":
        raise EvidenceError("Publishing is restricted to the Windows LiteLLM server account.")
    try:
        account = subprocess.run(["whoami"], capture_output=True, text=True,
                                 check=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvidenceError("Could not verify the Windows account.") from exc
    if account.casefold() != EXPECTED_WINDOWS_ACCOUNT.casefold():
        raise EvidenceError("This tool is restricted to the LiteLLM operator's Windows account.")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.b64decode(data, validate=True)


def validate_request_id(request_id: str) -> str:
    request_id = request_id.strip()
    if not REQUEST_ID_PATTERN.fullmatch(request_id):
        raise EvidenceError("Enter the Request ID from LiteLLM Logs (8–180 letters, digits, . _ : or -).")
    return request_id


def evidence_name(request_id: str) -> str:
    digest = hashlib.sha256(validate_request_id(request_id).encode("utf-8")).hexdigest()
    return f"encrypted-evidence/{digest}.json"


def read_master_key(env_file: Path = DEFAULT_ENV_FILE) -> str:
    key = os.environ.get("LITELLM_MASTER_KEY", "").strip()
    if key:
        return key
    try:
        lines = env_file.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise EvidenceError("Cannot read the server-local LiteLLM .env file.") from exc
    matches = []
    for line in lines:
        match = re.match(r"^\s*(?:export\s+)?LITELLM_MASTER_KEY\s*=\s*(.*)\s*$", line)
        if match:
            matches.append(match.group(1).strip().strip("\"'"))
    if len(matches) != 1 or not matches[0]:
        raise EvidenceError("Expected exactly one nonempty LITELLM_MASTER_KEY in the .env file.")
    return matches[0]


def fetch_spend_record(request_id: str, *, env_file: Path = DEFAULT_ENV_FILE,
                       gateway_url: str = GATEWAY_URL) -> dict[str, Any]:
    """Fetch only the exact LiteLLM request ID; no broad key or user search."""
    request_id = validate_request_id(request_id)
    query = urllib.parse.urlencode({"request_id": request_id, "page": 1, "page_size": 100})
    url = gateway_url.rstrip("/") + "/spend/logs/v2?" + query
    req = urllib.request.Request(
        url,
        headers={"Authorization": "Bearer " + read_master_key(env_file),
                 "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read(MAX_API_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise EvidenceError(f"LiteLLM lookup failed with HTTP {exc.code}. No data was published.") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EvidenceError("Could not reach LiteLLM on this server. No data was published.") from exc
    if len(raw) > MAX_API_RESPONSE_BYTES:
        raise EvidenceError("The LiteLLM response is too large. No data was published.")
    try:
        parsed = json.loads(raw)
        rows = parsed.get("data", [])
    except (ValueError, AttributeError) as exc:
        raise EvidenceError("LiteLLM returned an unexpected response. No data was published.") from exc
    if not isinstance(rows, list):
        raise EvidenceError("LiteLLM returned an unexpected log list. No data was published.")
    exact = [row for row in rows if isinstance(row, dict) and row.get("request_id") == request_id]
    if len(exact) != 1:
        raise EvidenceError(
            "Exactly one matching LiteLLM Request ID was not found. "
            "A PCB-internal ID cannot be used unless PCB forwarded it to LiteLLM."
        )
    return exact[0]


def make_payload(request_id: str, record: dict[str, Any]) -> bytes:
    payload = {
        "schema_version": 1,
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "source": "LiteLLM /spend/logs/v2 exact request_id lookup",
        "request_id": validate_request_id(request_id),
        "record": record,
        "limitations": (
            "Contains the complete spend-log row returned by LiteLLM. "
            "Request and response bodies absent from that row cannot be recovered by this tool."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


def encrypt_payload(plaintext: bytes, public_key_path: Path = PUBLIC_KEY_PATH) -> bytes:
    try:
        public_key = serialization.load_pem_public_key(public_key_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise EvidenceError("The evidence public key is missing or invalid.") from exc
    if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size < 3072:
        raise EvidenceError("The evidence public key must be RSA 3072 bits or stronger.")
    data_key = os.urandom(32)
    nonce = os.urandom(12)
    aad = b"litellm-request-evidence-v1"
    ciphertext = AESGCM(data_key).encrypt(nonce, plaintext, aad)
    wrapped_key = public_key.encrypt(
        data_key,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                     algorithm=hashes.SHA256(), label=None),
    )
    envelope = {
        "schema_version": 1,
        "encryption": "AES-256-GCM + RSA-OAEP-SHA256",
        "wrapped_key": _b64(wrapped_key),
        "nonce": _b64(nonce),
        "ciphertext": _b64(ciphertext),
    }
    return (json.dumps(envelope, separators=(",", ":")) + "\n").encode("ascii")


def decrypt_payload(envelope_bytes: bytes, private_key_path: Path = PRIVATE_KEY_PATH) -> bytes:
    try:
        private_key = serialization.load_pem_private_key(private_key_path.read_bytes(), password=None)
        envelope = json.loads(envelope_bytes)
        if envelope.get("schema_version") != 1:
            raise ValueError("Unsupported evidence format")
        data_key = private_key.decrypt(
            _unb64(envelope["wrapped_key"]),
            padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                         algorithm=hashes.SHA256(), label=None),
        )
        return AESGCM(data_key).decrypt(
            _unb64(envelope["nonce"]),
            _unb64(envelope["ciphertext"]),
            b"litellm-request-evidence-v1",
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise EvidenceError("Could not decrypt the evidence with the local private key.") from exc
    except Exception as exc:
        raise EvidenceError("Evidence authentication failed or the private key is incorrect.") from exc


def initialize_keys(*, public_key_path: Path = PUBLIC_KEY_PATH,
                    private_key_path: Path = PRIVATE_KEY_PATH) -> tuple[Path, Path]:
    if public_key_path.exists() or private_key_path.exists():
        raise EvidenceError("An evidence key already exists. Refusing to replace either key.")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    private_key_path.parent.mkdir(parents=True, exist_ok=True)
    private_bytes = private_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    with private_key_path.open("xb") as stream:
        stream.write(private_bytes)
    try:
        os.chmod(private_key_path, 0o600)
        if os.name == "nt":
            account = subprocess.run(["whoami"], capture_output=True, text=True,
                                     check=True).stdout.strip()
            subprocess.run(
                ["icacls", str(private_key_path), "/inheritance:r", "/grant:r", account + ":F"],
                capture_output=True, text=True, check=True,
            )
        public_key_path.write_bytes(private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        ))
    except Exception:
        private_key_path.unlink(missing_ok=True)
        public_key_path.unlink(missing_ok=True)
        raise
    return public_key_path, private_key_path


def _git(args: list[str], *, cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=90, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceError("Git could not complete. Check server network and Git authentication.") from exc
    if result.returncode:
        raise EvidenceError(f"Git {args[0]} failed (exit {result.returncode}). "
                            "Check server Git authentication and retry.")
    return result.stdout.strip()


def _publish_file(relative_path: str, data: bytes | None, *,
                  remote_url: str = REMOTE_URL) -> str:
    """Use a fresh clone so unrelated server checkout changes are untouched."""
    for attempt in range(2):
        with tempfile.TemporaryDirectory(prefix="litellm-evidence-") as temp_dir:
            checkout = Path(temp_dir) / "repo"
            _git(["clone", "--quiet", "--depth", "1", "--branch", BRANCH,
                  remote_url, str(checkout)])
            target = checkout / relative_path
            if data is None:
                if not target.is_file():
                    raise EvidenceError("That encrypted evidence file is not on GitHub.")
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    raise EvidenceError("This request has already been published. Delete it after analysis.")
                target.write_bytes(data)
            _git(["add", "--", relative_path], cwd=checkout)
            _git(["-c", "user.name=LiteLLM Evidence",
                  "-c", "user.email=litellm-evidence@users.noreply.github.com",
                  "commit", "--quiet", "-m",
                  "Remove encrypted request evidence" if data is None
                  else "Add encrypted request evidence"], cwd=checkout)
            try:
                _git(["push", "--quiet", "origin", BRANCH], cwd=checkout)
                return _git(["rev-parse", "HEAD"], cwd=checkout)
            except EvidenceError:
                if attempt == 1:
                    raise
    raise EvidenceError("GitHub publish failed.")


def publish_request(request_id: str, *, remote_url: str = REMOTE_URL,
                    env_file: Path = DEFAULT_ENV_FILE,
                    gateway_url: str = GATEWAY_URL) -> dict[str, str]:
    check_operator_account()
    request_id = validate_request_id(request_id)
    record = fetch_spend_record(request_id, env_file=env_file, gateway_url=gateway_url)
    ciphertext = encrypt_payload(make_payload(request_id, record))
    path = evidence_name(request_id)
    commit = _publish_file(path, ciphertext, remote_url=remote_url)
    return {"path": path, "commit": commit,
            "url": "https://github.com/rbadami-glitch/Dev-server/blob/main/" + path}


def remove_request(request_id: str, *, remote_url: str = REMOTE_URL) -> dict[str, str]:
    check_operator_account()
    path = evidence_name(request_id)
    commit = _publish_file(path, None, remote_url=remote_url)
    return {"path": path, "commit": commit}


def decrypt_file(input_path: Path, output_path: Path) -> Path:
    if output_path.resolve().is_relative_to(SCRIPT_DIR):
        raise EvidenceError("Decrypted evidence must be saved outside the public repository.")
    plaintext = decrypt_payload(input_path.read_bytes())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("xb") as stream:
        stream.write(plaintext)
    os.chmod(output_path, 0o600)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-key", help="Run only on the analysis PC; never on the LiteLLM server")
    publish = commands.add_parser("publish", help="Collect and publish one encrypted request")
    publish.add_argument("request_id")
    remove = commands.add_parser("remove", help="Remove one encrypted request from GitHub HEAD")
    remove.add_argument("request_id")
    decrypt = commands.add_parser("decrypt", help="Decrypt after pulling on the analysis PC")
    decrypt.add_argument("input", type=Path)
    decrypt.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "init-key":
            public, private = initialize_keys()
            print("Public key:", public)
            print("Private key (never commit or send):", private)
        elif args.command == "publish":
            result = publish_request(args.request_id)
            print("Encrypted evidence published:", result["url"])
            print("Commit:", result["commit"])
            print("After analysis, use the UI's Delete button.")
        elif args.command == "remove":
            result = remove_request(args.request_id)
            print("Encrypted evidence removed from the current branch:", result["path"])
            print("Commit:", result["commit"])
            print("Earlier Git history still contains the encrypted file.")
        else:
            print("Decrypted evidence:", decrypt_file(args.input, args.output))
        return 0
    except EvidenceError as exc:
        print("Error:", exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
