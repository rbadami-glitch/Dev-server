#!/usr/bin/env python3
"""Collect a sanitized LiteLLM timeline for the 20 Nov PCB latency incident.

Run on the Windows LiteLLM server. The optional publication encrypts the
sanitized timeline before it reaches the public Dev-server GitHub repository.
It does not collect prompts, answers, API keys, or full spend-log rows.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import application_diagnostic as apps
import capacity_report
import litellm_request_evidence as evidence


DEFAULT_REPORT_DIR = Path(r"D:\git pull for sanjay\cw-ai-coe-litellm-alert")
START = datetime(2026, 11, 20, 15, 20, 0, tzinfo=timezone.utc)
END = datetime(2026, 11, 20, 15, 36, 0, tzinfo=timezone.utc)
PCB_REQUEST_ID = "98d2ca8a-26f6-4967-8184-d751c2d1c4da"
OUTPUT_PATH = "encrypted-evidence/pcb-latency-20261120-1525.json"


def nonnegative_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def identifier_paths(value: Any, needle: str, path: str = "", depth: int = 0) -> list[str]:
    """Report paths containing the PCB ID, never the stored values themselves."""
    if depth > 8:
        return []
    if isinstance(value, str):
        return [path] if needle.casefold() in value.casefold() else []
    if isinstance(value, dict):
        found = []
        for key, item in value.items():
            if str(key).casefold() in {"api_key", "token", "authorization", "messages", "response"}:
                continue
            found.extend(identifier_paths(item, needle, f"{path}.{key}" if path else str(key), depth + 1))
        return found
    if isinstance(value, list):
        found = []
        for index, item in enumerate(value[:100]):
            found.extend(identifier_paths(item, needle, f"{path}[{index}]", depth + 1))
        return found
    return []


def collect_timeline(rows: list[dict], report, pcb_aliases: set[str]) -> dict:
    timeline = []
    seen = set()
    pcb_matches = 0
    id_matches = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        start = report.parse_ts(row.get("startTime"))
        if start is None or not START <= start <= END:
            continue
        request_id = row.get("request_id")
        if request_id and request_id in seen:
            continue
        if request_id:
            seen.add(request_id)
        end = report.parse_ts(row.get("endTime"))
        metadata = report.row_meta(row)
        alias = metadata.get("user_api_key_alias") or row.get("key_alias") or ""
        is_pcb = apps.normalize_alias(alias) in pcb_aliases
        if is_pcb:
            pcb_matches += 1
        lookup_fields = {"request_id": request_id, "session_id": row.get("session_id"),
                         "end_user": row.get("end_user"), "metadata": metadata}
        pcb_id_paths = identifier_paths(lookup_fields, PCB_REQUEST_ID)
        if pcb_id_paths:
            id_matches += 1
        record = {
            "request_id": request_id if is_pcb else None,
            "start_utc": start.isoformat(),
            "end_utc": end.isoformat() if end else None,
            "duration_s": round((end - start).total_seconds(), 3) if end and end >= start else None,
            "pcb_key_alias_match": is_pcb,
            "pcb_request_id_saved_paths": pcb_id_paths,
            "model_group": row.get("model_group"),
            "upstream_model": row.get("model"),
            "prompt_tokens": row.get("prompt_tokens"),
            "completion_tokens": row.get("completion_tokens"),
            "total_tokens": row.get("total_tokens"),
            "reasoning_tokens": apps.reasoning_tokens(row, metadata),
            "status": row.get("status"),
            "attempted_retries": metadata.get("attempted_retries"),
            "recorded_error": bool(metadata.get("error_information")),
            "explicit_model_queue_s": capacity_report._model_queue_seconds(row, metadata),
            "litellm_overhead_ms": nonnegative_number(metadata.get("litellm_overhead_time_ms")),
            "completion_start_utc": row.get("completionStartTime"),
        }
        timeline.append(record)
    timeline.sort(key=lambda item: item["start_utc"])
    return {"schema_version": 1,
            "incident": "PCB Smart Chat 20 November 2026 latency investigation",
            "pcb_request_id": PCB_REQUEST_ID,
            "window_start_utc": START.isoformat(),
            "window_end_utc": END.isoformat(),
            "pcb_request_id_seen_in_saved_fields": id_matches > 0,
            "rows_with_pcb_request_id": id_matches,
            "pcb_alias_matched_requests": pcb_matches,
            "all_gateway_requests_in_window": len(timeline),
            "rows": timeline,
            "limitations": [
                "PCB request ID may not have been forwarded to LiteLLM; time and alias alone do not prove task ownership.",
                "LiteLLM duration includes upstream wait and generation; it is not a model queue measurement.",
                "A non-streaming completionStartTime at the end is not a measured first-token timestamp.",
                "GPU utilization and historical vLLM queue metrics are not in LiteLLM spend rows.",
            ]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--aliases-file", type=Path, default=apps.ALIASES_FILE)
    parser.add_argument("--publish", action="store_true",
                        help="Encrypt and publish the sanitized timeline for analysis")
    args = parser.parse_args()
    evidence.check_operator_account()
    if not (args.report_dir / "litellm_report.py").is_file():
        parser.error("--report-dir must contain litellm_report.py")
    if not args.aliases_file.is_file():
        parser.error("The server-local application_aliases.local.json is required")
    selected = json.loads(args.aliases_file.read_text(encoding="utf-8-sig"))
    pcb_aliases = {apps.normalize_alias(value) for value in selected.get("PCB", [])}
    if not pcb_aliases:
        parser.error("No PCB aliases are configured in the server-local alias file")
    sys.path.insert(0, str(args.report_dir.resolve()))
    import litellm_report as report

    cfg = report.load_config(args.report_dir / "config.json")
    if not cfg.get("litellm_api_key"):
        parser.error("The report configuration has no LiteLLM admin credential")
    rows, source = report.fetch_request_logs(cfg, START, END)
    result = collect_timeline(rows, report, pcb_aliases)
    result["log_source"] = source
    result["collection_warning_count"] = len(report.WARNINGS)
    print("UTC window:", START.isoformat(), "to", END.isoformat())
    print("Log source:", source)
    print("PCB-alias requests:", result["pcb_alias_matched_requests"])
    print("All gateway requests in window:", result["all_gateway_requests_in_window"])
    print("PCB request ID found in saved fields:", result["pcb_request_id_seen_in_saved_fields"])
    print("Collection warnings:", result["collection_warning_count"])
    if result["pcb_alias_matched_requests"] == 0:
        raise RuntimeError("No PCB-alias requests found. No evidence published.")
    if args.publish:
        ciphertext = evidence.encrypt_payload(json.dumps(result, separators=(",", ":")).encode("utf-8"))
        commit = evidence._publish_file(OUTPUT_PATH, ciphertext)
        print("Encrypted evidence:", "https://github.com/rbadami-glitch/Dev-server/blob/main/" + OUTPUT_PATH)
        print("Commit:", commit)
        print("Delete this encrypted file from GitHub after analysis.")
    else:
        print("Read-only lookup complete. Add --publish to export encrypted evidence.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, evidence.EvidenceError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
