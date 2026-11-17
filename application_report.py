#!/usr/bin/env python3
"""Create the existing LiteLLM report plus a separate Applications table.

This wrapper reads the existing alert script and config without changing them.
Preview by default; pass --send only after reviewing the generated HTML.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from application_diagnostic import load_application_aliases, summarize


HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "application-reports"


def compact(value):
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:,}"


def render_applications(report, summary, has_logs):
    rows = []
    for b in summary["application_stats"]:
        if not has_logs:
            rows.append([b["application"], "-", "-", "-", "-", "-", "-", "-"])
            continue
        lat = b["p95_latency_s"]
        reasoning = (compact(b["reasoning_tokens"])
                     if b["reasoning_reported_requests"] else "-")
        if b["reasoning_reported_requests"] and b["reasoning_reported_requests"] < b["requests"]:
            reasoning += " (partial)"
        rows.append([b["application"], report.num(b["requests"]),
                     f"{report.num(b['failed'])} ({report.pct(b['failed'], b['requests'])})",
                     report.money(b["spend_usd"]),
                     compact(b["input_tokens"]), compact(b["output_tokens"]), reasoning,
                     f"{lat:.1f}s" if lat is not None else "-"])
    return (f'<div style="{report.FONT}max-width:1100px;margin:0 auto;background:#ffffff;padding:0 20px 20px">'
            f'<h2 style="{report.H2}">Applications</h2>'
            + report.table(["Application", "Requests", "Failed", "Spend", "Input tokens",
                            "Output tokens", "Reasoning tokens (in output)", "p95 latency"], rows)
            + '</div>')


def group_latest_failures(report, original_html, failed_rows):
    """Trial: replace only the rendered latest-failures table, never the source report."""
    marker = "Latest failed requests ("
    heading = original_html.find(marker)
    start = original_html.rfind('<p style=', 0, heading)
    if heading < 0 or start < 0 or start > heading:
        raise ValueError("Could not locate latest failed requests table")
    end = original_html.find("<h2 ", heading)
    if end < 0:
        raise ValueError("Could not locate the section after latest failed requests")

    groups = {}
    for row in failed_rows:
        message = str(row["error"])
        # These two values vary between otherwise identical rate-limit failures.
        stable = re.sub(r"\bRemaining:\s*\d+", "Remaining: #", message, flags=re.I)
        stable = re.sub(r"\bLimit resets at:\s*[^.]+", "Limit resets at: #", stable, flags=re.I)
        signature = (row["model"], row["key"], row["user"], row["code"], stable)
        groups.setdefault(signature, []).append(row)

    rendered_rows = []
    for (model, key, user, code, message), requests in groups.items():
        request_rows = report.table(
            ["Time", "Request ID", "Original error"],
            [[r["time"], r["request_id"] or "(missing)", r["error"]] for r in requests],
            99,
        )
        details = (f'<details><summary style="cursor:pointer;color:#1d4ed8">'
                   f'Show {len(requests)} request ID(s)</summary>{request_rows}</details>')
        rendered_rows.append([requests[0]["time"], model, key, user, code,
                              message, str(len(requests)), details])
    replacement = (
        f'<p style="{report.FONT}font-size:12px;color:#6b7280;margin:10px 0 2px">'
        f'Latest failed requests: {len(failed_rows)} shown, grouped into {len(groups)} rows '
        f'(counts cover only these {len(failed_rows)} requests)</p>'
        + report.table(["Latest time", "Model", "Key", "User", "Code", "Error",
                        "Count", "Request IDs"], rendered_rows, 99)
    )
    return original_html[:start] + replacement + original_html[end:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, required=True,
                        help="Folder containing litellm_report.py and config.json")
    parser.add_argument("--days", type=float, default=None)
    parser.add_argument("--configure", action="store_true", help="Re-enter and save application aliases")
    parser.add_argument("--send", action="store_true", help="Send the extended report by email")
    parser.add_argument("--try-collapsible-failures", action="store_true",
                        help="Trial grouped latest failures with expandable request IDs")
    args = parser.parse_args()
    if args.days is not None and args.days <= 0:
        parser.error("--days must be positive")
    report_dir = args.report_dir.resolve()
    if not (report_dir / "litellm_report.py").is_file():
        parser.error("--report-dir must contain litellm_report.py")
    sys.path.insert(0, str(report_dir))
    import litellm_report as report

    cfg = report.load_config(report_dir / "config.json")
    if not cfg["litellm_api_key"]:
        parser.error("litellm_api_key is missing")
    aliases = load_application_aliases(args.configure)
    days = args.days if args.days is not None else float(cfg["report_days"])
    data = report.collect(cfg, days)
    extra = report.api_get(cfg, "/key/list", {"return_full_object": "true", "page_size": 500})
    alias_map = dict(data["key_aliases"])
    if isinstance(extra, dict):
        for key in extra.get("keys") or []:
            if isinstance(key, dict) and key.get("key_alias"):
                token = key.get("token") or key.get("key")
                if token:
                    alias_map[token] = key["key_alias"]
    current = report.aggregate(cfg, data, data["logs"])
    previous = report.aggregate(cfg, data, data["prev_logs"])
    bullets, alerts = report.build_insights(cfg, current, previous, data)
    base_html = report.render_html(cfg, data, current, previous, days, bullets, alerts)
    if args.try_collapsible_failures:
        base_html = group_latest_failures(report, base_html, current["failed_rows"])
    summary = summarize(report, data["logs"], alias_map, aliases,
                        data["window_start"], data["window_end"])
    extended_html = base_html + "\n" + render_applications(report, summary, bool(data["logs"]))
    csv_report = report.render_csv(current)

    OUTPUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = OUTPUT_DIR / f"litellm_with_applications_{stamp}.html"
    csv_path = OUTPUT_DIR / f"litellm_with_applications_{stamp}.csv"
    html_path.write_text(extended_html, encoding="utf-8")
    csv_path.write_text(csv_report, encoding="utf-8")
    print(f"Preview: {html_path}")
    counts = [f"{b['application']}={b['requests']}" for b in summary["application_stats"]]
    print(f"Application requests: {', '.join(counts)}")
    if not args.send:
        print("Preview only; no email sent.")
        return 0

    subject = (f"LiteLLM report with applications {datetime.now():%d %b %Y}: "
               f"{report.money(current['spend'])} | {report.num(current['requests'])} req")
    attachments = [(html_path.name, extended_html.encode("utf-8")),
                   (csv_path.name, csv_report.encode("utf-8"))]
    if data["logs"]:
        raw = json.dumps([{k: v for k, v in row.items() if k != "_ts"} for row in data["logs"]], default=str)
        attachments.append((f"litellm_raw_logs_{stamp}.json", raw.encode("utf-8")))
    if not report.send_resend(cfg, subject, extended_html, attachments):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
