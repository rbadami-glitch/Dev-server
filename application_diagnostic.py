#!/usr/bin/env python3
"""Collect encrypted LiteLLM application usage diagnostics without sending email.

Run on the LiteLLM server with the Dev-server Python environment:
    python application_diagnostic.py --report-dir "D:\\git pull for sanjay\\cw-ai-coe-litellm-alert"

The output is encrypted with the Dev-server evidence public key before it is
written. No API keys, raw request rows, or plaintext results are stored here.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from litellm_request_evidence import encrypt_payload


HERE = Path(__file__).resolve().parent
APP_PATTERN = re.compile(r"pcb|fed[\s_-]*intel|wren", re.IGNORECASE)


def summarize(report, rows, aliases, start, end):
    groups = defaultdict(lambda: {"requests": 0, "failed": 0, "spend_usd": 0.0,
                                  "input_tokens": 0, "output_tokens": 0,
                                  "latencies_s": []})
    total_rows = 0
    unmatched_rows = 0
    for row in rows:
        ts = report.parse_ts(row.get("startTime"))
        if ts is None or not start <= ts <= end:
            continue
        total_rows += 1
        meta = report.row_meta(row)
        alias = (meta.get("user_api_key_alias") or row.get("key_alias")
                 or aliases.get(row.get("api_key")) or "")
        if not APP_PATTERN.search(str(alias)):
            unmatched_rows += 1
            continue
        bucket = groups[str(alias)]
        bucket["requests"] += 1
        bucket["failed"] += str(row.get("status") or "success").lower() in ("failure", "failed", "error")
        bucket["spend_usd"] += float(row.get("spend") or 0)
        bucket["input_tokens"] += int(row.get("prompt_tokens") or 0)
        bucket["output_tokens"] += int(row.get("completion_tokens") or 0)
        finish = report.parse_ts(row.get("endTime"))
        if finish and finish >= ts:
            bucket["latencies_s"].append((finish - ts).total_seconds())
    matching_aliases = sorted({str(alias) for alias in aliases.values() if APP_PATTERN.search(str(alias))})
    stats = []
    for alias, bucket in sorted(groups.items()):
        latencies = sorted(bucket.pop("latencies_s"))
        bucket["spend_usd"] = round(bucket["spend_usd"], 6)
        bucket["latency_samples"] = len(latencies)
        bucket["p95_latency_s"] = round(report.percentile(latencies, .95), 3) if latencies else None
        stats.append({"alias": alias, **bucket})
    return {"request_rows_in_window": total_rows, "rows_without_candidate_alias": unmatched_rows,
            "matching_key_aliases": matching_aliases, "application_alias_stats": stats}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, required=True,
                        help="Folder containing litellm_report.py and its config.json")
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
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)
    rows, source = report.fetch_request_logs(cfg, start, end)
    response = report.api_get(cfg, "/key/list", {"return_full_object": "true", "page_size": 500})
    aliases = {}
    if isinstance(response, dict):
        for item in response.get("keys") or []:
            if isinstance(item, dict) and item.get("key_alias"):
                token = item.get("token") or item.get("key")
                if token:
                    aliases[token] = item["key_alias"]
    payload = {"generated_utc": end.isoformat(), "window_start_utc": start.isoformat(),
               "window_end_utc": end.isoformat(), "log_source": source,
               "collection_warning_count": len(report.WARNINGS),
               **summarize(report, rows, aliases, start, end)}
    ciphertext = encrypt_payload((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
    output_dir = HERE / "encrypted-evidence"
    output_dir.mkdir(exist_ok=True)
    output = output_dir / f"application-usage-{end:%Y%m%d-%H%M%S}.json"
    with output.open("xb") as file:
        file.write(ciphertext)
    print(f"Encrypted diagnostic: {output}")
    print(f"Candidate aliases: {len(payload['matching_key_aliases'])}; "
          f"matched groups: {len(payload['application_alias_stats'])}")
    print("No email was sent. No plaintext diagnostic was written.")


if __name__ == "__main__":
    main()
