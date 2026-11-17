#!/usr/bin/env python3
"""Collect encrypted LiteLLM application usage diagnostics without sending email.

Run on the LiteLLM server with the Dev-server Python environment:
    python application_diagnostic.py --report-dir "D:\\git pull for sanjay\\cw-ai-coe-litellm-alert"

The output is encrypted with the Dev-server evidence public key before it is
written. No API keys, raw request rows, or plaintext results are stored here.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALIASES_FILE = HERE / "application_aliases.local.json"
APPLICATIONS = ("PCB", "FedIntel", "Wren AI")


def normalize_alias(value):
    return " ".join(str(value).split()).casefold()


def prompt_application_aliases():
    """Read key aliases, never private keys, from the operator's terminal."""
    selected = {}
    print("Paste LiteLLM key aliases, not private keys. Separate multiple aliases with commas.")
    for app in APPLICATIONS:
        while True:
            entered = input(f"{app} key alias: ").strip()
            names = [part.strip() for part in entered.split(",") if part.strip()]
            if not names:
                print("Enter at least one alias for this application.")
                continue
            if any(name.lower().startswith(("sk-", "bearer ")) for name in names):
                print("That looks like a private key. Enter its alias instead.")
                continue
            selected[app] = names
            break
    normalized = [normalize_alias(alias) for names in selected.values() for alias in names]
    if len(normalized) != len(set(normalized)):
        raise ValueError("Each alias must be entered only once")
    return selected


def load_application_aliases(configure=False):
    """Reuse the server-local alias file; prompt only when it is absent or reset."""
    if not configure and ALIASES_FILE.exists():
        selected = json.loads(ALIASES_FILE.read_text(encoding="utf-8-sig"))
        if not isinstance(selected, dict) or set(selected) != set(APPLICATIONS):
            raise ValueError(f"Invalid application alias file: {ALIASES_FILE}")
        if any(not isinstance(names, list) or not names or
               not all(isinstance(name, str) and name.strip() for name in names)
               for names in selected.values()):
            raise ValueError(f"Invalid application aliases in: {ALIASES_FILE}")
        normalized = [normalize_alias(name) for names in selected.values() for name in names]
        if len(normalized) != len(set(normalized)):
            raise ValueError(f"Duplicate application aliases in: {ALIASES_FILE}")
        return selected
    selected = prompt_application_aliases()
    ALIASES_FILE.write_text(json.dumps(selected, indent=2) + "\n", encoding="utf-8")
    print(f"Saved application aliases locally: {ALIASES_FILE}")
    return selected


def summarize(report, rows, aliases, selected, start, end):
    alias_to_app = {normalize_alias(alias): app for app, names in selected.items() for alias in names}
    groups = {app: {"requests": 0, "failed": 0, "spend_usd": 0.0,
                    "input_tokens": 0, "output_tokens": 0, "latencies_s": []}
              for app in selected}
    total_rows = 0
    unmatched_rows = 0
    seen_aliases = {app: set() for app in selected}
    for row in rows:
        ts = report.parse_ts(row.get("startTime"))
        if ts is None or not start <= ts <= end:
            continue
        total_rows += 1
        meta = report.row_meta(row)
        alias = (meta.get("user_api_key_alias") or row.get("key_alias")
                 or aliases.get(row.get("api_key")) or "")
        app = alias_to_app.get(normalize_alias(alias))
        if app is None:
            unmatched_rows += 1
            continue
        seen_aliases[app].add(str(alias))
        bucket = groups[app]
        bucket["requests"] += 1
        bucket["failed"] += str(row.get("status") or "success").lower() in ("failure", "failed", "error")
        bucket["spend_usd"] += float(row.get("spend") or 0)
        bucket["input_tokens"] += int(row.get("prompt_tokens") or 0)
        bucket["output_tokens"] += int(row.get("completion_tokens") or 0)
        finish = report.parse_ts(row.get("endTime"))
        if finish and finish >= ts:
            bucket["latencies_s"].append((finish - ts).total_seconds())
    listed_aliases = sorted({str(alias) for alias in aliases.values()
                             if normalize_alias(alias) in alias_to_app})
    stats = []
    for app, bucket in groups.items():
        latencies = sorted(bucket.pop("latencies_s"))
        bucket["spend_usd"] = round(bucket["spend_usd"], 6)
        bucket["latency_samples"] = len(latencies)
        bucket["p95_latency_s"] = round(report.percentile(latencies, .95), 3) if latencies else None
        stats.append({"application": app, "matched_log_aliases": sorted(seen_aliases[app]), **bucket})
    return {"request_rows_in_window": total_rows, "rows_without_selected_alias": unmatched_rows,
            "selected_aliases": selected, "matching_listed_key_aliases": listed_aliases,
            "application_stats": stats}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, required=True,
                        help="Folder containing litellm_report.py and its config.json")
    parser.add_argument("--days", type=float, default=1)
    parser.add_argument("--configure", action="store_true", help="Re-enter and save application aliases")
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
    selected = load_application_aliases(args.configure)
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
               **summarize(report, rows, aliases, selected, start, end)}
    from litellm_request_evidence import encrypt_payload
    ciphertext = encrypt_payload((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
    output_dir = HERE / "encrypted-evidence"
    output_dir.mkdir(exist_ok=True)
    output = output_dir / f"application-usage-{end:%Y%m%d-%H%M%S}.json"
    with output.open("xb") as file:
        file.write(ciphertext)
    print(f"Encrypted diagnostic: {output}")
    print(f"Matching listed key aliases: {len(payload['matching_listed_key_aliases'])}; "
          f"matched requests: {payload['request_rows_in_window'] - payload['rows_without_selected_alias']}")
    print("No email was sent. No plaintext diagnostic was written.")


if __name__ == "__main__":
    main()
