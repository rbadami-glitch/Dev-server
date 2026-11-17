#!/usr/bin/env python3
"""Encrypt the latest local application report preview for GitHub review.

Reads application-reports/*.html and writes only encrypted ciphertext to
encrypted-evidence/. It does not send mail or push to GitHub.
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from litellm_request_evidence import encrypt_payload


HERE = Path(__file__).resolve().parent
PREVIEW_DIR = HERE / "application-reports"
ENCRYPTED_DIR = HERE / "encrypted-evidence"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Specific local HTML preview; defaults to newest")
    args = parser.parse_args()
    if args.input:
        source = args.input.resolve()
        if source.parent != PREVIEW_DIR.resolve() or source.suffix.lower() != ".html":
            parser.error("--input must be an HTML file inside application-reports")
    else:
        previews = sorted(PREVIEW_DIR.glob("litellm_with_applications_*.html"),
                          key=lambda path: path.stat().st_mtime, reverse=True)
        if not previews:
            parser.error("No application HTML preview found in application-reports")
        source = previews[0]
    if not source.is_file():
        parser.error(f"Preview file not found: {source}")
    ciphertext = encrypt_payload(source.read_bytes())
    ENCRYPTED_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output = ENCRYPTED_DIR / f"application-preview-{stamp}.json"
    with output.open("xb") as stream:
        stream.write(ciphertext)
    print(f"Encrypted preview: {output}")
    print("No email was sent. The plaintext HTML remains local and ignored by Git.")


if __name__ == "__main__":
    main()
