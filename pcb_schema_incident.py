#!/usr/bin/env python3
"""Find the 19 Nov PCB draft/review/revision calls and publish encrypted evidence.

Run on the Windows LiteLLM server. The incident window and prompt-token counts
are specific to PCB's 19 November 2026 report. No request body or private key is
printed or written to this checkout in plaintext.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import application_diagnostic as apps
import litellm_request_evidence as evidence


HERE = Path(__file__).resolve().parent
DEFAULT_REPORT_DIR = Path(r"D:\git pull for sanjay\cw-ai-coe-litellm-alert")
START = datetime(2026, 11, 19, 8, 32, 30, tzinfo=timezone.utc)
END = datetime(2026, 11, 19, 8, 35, 0, tzinfo=timezone.utc)
STAGES = (("Initial draft", 15117), ("Answer review", 25720),
          ("Revised draft", 16813))


def find_calls(rows: list[dict], report, pcb_aliases: set[str]) -> list[tuple[str, dict]]:
    matches: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for stage, expected_tokens in STAGES:
        candidates = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            request_id = row.get("request_id")
            ts = report.parse_ts(row.get("startTime"))
            if not request_id or ts is None or not START <= ts <= END:
                continue
            try:
                tokens = int(row.get("prompt_tokens") or -1)
            except (TypeError, ValueError):
                continue
            if tokens != expected_tokens:
                continue
            metadata = report.row_meta(row)
            alias = metadata.get("user_api_key_alias") or row.get("key_alias") or ""
            if apps.normalize_alias(alias) in pcb_aliases:
                candidates.append(row)
        # Every stage must have exactly one distinct PCB request. This prevents
        # publishing an unrelated user's record when timing/counts collide.
        distinct = {row["request_id"]: row for row in candidates}
        if len(distinct) != 1:
            raise RuntimeError(
                f"{stage}: expected one PCB request with {expected_tokens} prompt "
                f"tokens in the UTC window; found {len(distinct)}. Nothing published."
            )
        row = next(iter(distinct.values()))
        if row["request_id"] in seen:
            raise RuntimeError("One request matched multiple stages. Nothing published.")
        seen.add(row["request_id"])
        matches.append((stage, row))
    timestamps = [report.parse_ts(row["startTime"]) for _, row in matches]
    if timestamps != sorted(timestamps):
        raise RuntimeError("Matching calls are not in the expected order. Nothing published.")
    return matches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--aliases-file", type=Path, default=apps.ALIASES_FILE,
                        help="Server-local application alias JSON; needed for a fresh clone")
    parser.add_argument("--publish", action="store_true",
                        help="Publish each matched request with the existing encrypted exporter")
    args = parser.parse_args()
    evidence.check_operator_account()
    report_dir = args.report_dir.resolve()
    if not (report_dir / "litellm_report.py").is_file():
        parser.error("--report-dir must contain litellm_report.py")
    if not args.aliases_file.is_file():
        parser.error("The server-local application_aliases.local.json is required")
    aliases = json.loads(args.aliases_file.read_text(encoding="utf-8-sig"))
    pcb_aliases = {apps.normalize_alias(name) for name in aliases.get("PCB", [])}
    if not pcb_aliases:
        parser.error("No PCB aliases are configured in the server-local alias file")
    sys.path.insert(0, str(report_dir))
    import litellm_report as report

    cfg = report.load_config(report_dir / "config.json")
    if not cfg.get("litellm_api_key"):
        parser.error("The report configuration has no LiteLLM admin credential")
    rows, source = report.fetch_request_logs(cfg, START, END)
    matches = find_calls(rows, report, pcb_aliases)
    print("Log source:", source)
    print("UTC window:", START.isoformat(), "to", END.isoformat())
    for stage, row in matches:
        print(stage + ":", row["request_id"], "start=", row.get("startTime"),
              "end=", row.get("endTime"), "prompt_tokens=", row.get("prompt_tokens"),
              "completion_tokens=", row.get("completion_tokens"))
    if args.publish:
        for stage, row in matches:
            result = evidence.publish_request(row["request_id"])
            print(stage + " encrypted evidence:", result["url"])
        print("After analysis, delete these three encrypted files using the evidence UI.")
    else:
        print("Read-only lookup complete. Add --publish to export encrypted evidence.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, evidence.EvidenceError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
