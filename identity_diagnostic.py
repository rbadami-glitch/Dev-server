"""Inspect identity fields for opaque LiteLLM end users, encrypted for analysis.

This reads the existing report config and request logs. It sends no email and
never writes plaintext evidence or private API keys to the public repository.
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from litellm_request_evidence import encrypt_payload


HERE = Path(__file__).resolve().parent
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def safe_value(value):
    if value is None:
        return None
    value = str(value).strip()
    if len(value) > 255 or value.lower().startswith(("sk-", "bearer ")):
        return "[redacted]"
    return value


def opaque_identity(value):
    if not isinstance(value, str) or not value.startswith("{"):
        return None
    try:
        parsed = json.loads(value)
    except ValueError:
        return None
    if isinstance(parsed, dict) and {"device_id", "session_id"} <= parsed.keys():
        return parsed
    return None


def build_diagnostic(report, data, max_samples=20):
    samples = []
    devices = set()
    candidate_counts = {"end_user_email": 0, "row_user_email": 0,
                        "metadata_end_user_email": 0, "key_owner_email": 0}
    opaque_rows = 0
    for row in data["logs"]:
        identity = opaque_identity(row.get("end_user"))
        if identity is None:
            continue
        opaque_rows += 1
        meta = report.row_meta(row)
        if not isinstance(meta, dict):
            meta = {}
        device = str(identity.get("device_id") or "")
        device_hash = hashlib.sha256(device.encode("utf-8")).hexdigest()[:16]
        fields = {
            "end_user_email": safe_value(identity.get("email") or identity.get("user_email")),
            "row_user": safe_value(row.get("user")),
            "metadata_end_user_id": safe_value(meta.get("user_api_key_end_user_id")),
            "key_owner_email": safe_value(meta.get("user_api_key_user_email")),
            "key_owner_id": safe_value(meta.get("user_api_key_user_id")),
            "key_alias": safe_value(meta.get("user_api_key_alias") or row.get("key_alias")
                                    or data["key_aliases"].get(row.get("api_key"))),
        }
        candidate_counts["end_user_email"] += bool(EMAIL.fullmatch(fields["end_user_email"] or ""))
        candidate_counts["row_user_email"] += bool(EMAIL.fullmatch(fields["row_user"] or ""))
        candidate_counts["metadata_end_user_email"] += bool(EMAIL.fullmatch(fields["metadata_end_user_id"] or ""))
        candidate_counts["key_owner_email"] += bool(EMAIL.fullmatch(fields["key_owner_email"] or ""))
        if device_hash not in devices and len(samples) < max_samples:
            samples.append({"device_hash": device_hash,
                            "request_id": safe_value(row.get("request_id")),
                            "model": safe_value(row.get("model")),
                            **fields})
        devices.add(device_hash)
    return {"log_source": data["log_source"], "total_request_rows": len(data["logs"]),
            "opaque_identity_rows": opaque_rows, "distinct_devices": len(devices),
            "candidate_email_counts": candidate_counts, "samples": samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--days", type=float, default=1)
    args = parser.parse_args()
    if args.days <= 0:
        parser.error("--days must be positive")
    report_dir = args.report_dir.resolve()
    if not (report_dir / "litellm_report.py").is_file():
        parser.error("--report-dir must contain litellm_report.py")
    sys.path.insert(0, str(report_dir))
    import litellm_report as report

    cfg = report.load_config(report_dir / "config.json")
    if not cfg["litellm_api_key"]:
        parser.error("litellm_api_key is missing")
    data = report.collect(cfg, args.days)
    diagnostic = build_diagnostic(report, data)
    diagnostic["captured_utc"] = datetime.now(timezone.utc).isoformat()
    ciphertext = encrypt_payload((json.dumps(diagnostic, separators=(",", ":")) + "\n").encode("utf-8"))
    out_dir = HERE / "encrypted-evidence"
    out_dir.mkdir(exist_ok=True)
    output = out_dir / f"identity-diagnostic-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
    with output.open("xb") as stream:
        stream.write(ciphertext)
    print(f"Encrypted diagnostic: {output}")
    print(f"Opaque end-user rows: {diagnostic['opaque_identity_rows']}; "
          f"distinct devices: {diagnostic['distinct_devices']}")
    print("No email sent and no plaintext diagnostic written.")


if __name__ == "__main__":
    main()
