"""Offline tests for the evidence envelope, exact lookup, and Git lifecycle."""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import litellm_request_evidence as evidence


class EvidenceTests(unittest.TestCase):
    def test_encryption_round_trip_and_tamper_detection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
            private_path = root / "private.pem"
            public_path = root / "public.pem"
            private_path.write_bytes(private.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()))
            public_path.write_bytes(private.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
            plaintext = evidence.make_payload(
                "chatcmpl-example-123",
                {"request_id": "chatcmpl-example-123",
                 "metadata": {"reasoning_tokens": 1899, "secret": "never-public"}},
            )
            encrypted = evidence.encrypt_payload(plaintext, public_path)
            self.assertNotIn(b"never-public", encrypted)
            self.assertNotIn(b"chatcmpl-example-123", encrypted)
            self.assertEqual(evidence.decrypt_payload(encrypted, private_path), plaintext)
            envelope = json.loads(encrypted)
            envelope["ciphertext"] = envelope["ciphertext"][:-5] + "AAAAA"
            with self.assertRaises(evidence.EvidenceError):
                evidence.decrypt_payload(json.dumps(envelope).encode(), private_path)

    def test_exact_spend_log_lookup(self) -> None:
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                seen.append((self.path, self.headers.get("Authorization")))
                body = json.dumps({"data": [
                    {"request_id": "chatcmpl-target-123", "metadata": {"x": 1}},
                ]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                env_file = Path(directory) / ".env"
                env_file.write_text("LITELLM_MASTER_KEY=sk-test-only\n", encoding="utf-8")
                result = evidence.fetch_spend_record(
                    "chatcmpl-target-123", env_file=env_file,
                    gateway_url=f"http://127.0.0.1:{server.server_port}")
                self.assertEqual(result["metadata"]["x"], 1)
                self.assertIn("request_id=chatcmpl-target-123", seen[0][0])
                self.assertEqual(seen[0][1], "Bearer sk-test-only")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_git_publish_and_delete_use_encrypted_file_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bare = root / "remote.git"
            seed = root / "seed"
            subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(bare)],
                           capture_output=True, check=True)
            subprocess.run(["git", "clone", str(bare), str(seed)],
                           capture_output=True, check=True)
            (seed / "README.md").write_text("public project\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(seed), "add", "README.md"],
                           capture_output=True, check=True)
            subprocess.run(["git", "-C", str(seed), "-c", "user.name=Test",
                            "-c", "user.email=test@example.invalid", "commit", "-m", "seed"],
                           capture_output=True, check=True)
            subprocess.run(["git", "-C", str(seed), "push", "origin", "main"],
                           capture_output=True, check=True)
            path = evidence.evidence_name("chatcmpl-test-456")
            ciphertext = b'{"ciphertext":"test-encrypted-only"}\n'
            add_commit = evidence._publish_file(path, ciphertext, remote_url=str(bare))
            self.assertEqual(len(add_commit), 40)
            in_git = subprocess.run(
                ["git", "--git-dir", str(bare), "show", f"main:{path}"],
                capture_output=True, check=True).stdout
            self.assertEqual(in_git, ciphertext)
            remove_commit = evidence._publish_file(path, None, remote_url=str(bare))
            self.assertNotEqual(add_commit, remove_commit)
            remaining = subprocess.run(
                ["git", "--git-dir", str(bare), "ls-tree", "-r", "--name-only", "main"],
                capture_output=True, text=True, check=True).stdout
            self.assertNotIn(path, remaining)


if __name__ == "__main__":
    unittest.main()
