#!/usr/bin/env python3
"""Create the existing LiteLLM report plus a separate Applications table.

This wrapper reads the existing alert script and config without changing them.
Preview by default; pass --send only after reviewing the generated HTML.
"""

import argparse
import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from application_diagnostic import load_application_aliases, summarize
from capacity_report import render_capacity, summarize_capacity


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
            rows.append([b["application"], "-", "-", "-", "-", "-", "-", "-", "-"])
            continue
        lat = b["p95_latency_s"]
        reasoning = (compact(b["reasoning_tokens"])
                     if b["reasoning_reported_requests"] else "-")
        if b["reasoning_reported_requests"] and b["reasoning_reported_requests"] < b["requests"]:
            reasoning += " (partial)"
        rows.append([b["application"], report.num(b["requests"]),
                     f"{report.num(b['failed'])} ({report.pct(b['failed'], b['requests'])})",
                     report.money(b["spend_usd"]),
                     compact(b["input_tokens"]), compact(b["output_tokens"]),
                     compact(b["total_tokens"]), reasoning,
                     f"{lat:.1f}s" if lat is not None else "-"])
    return (f'<h2 style="{report.H2}">Applications</h2>'
            + report.table(["Application", "Requests", "Failed", "Spend", "Input tokens",
                            "Output tokens", "Total tokens", "Reasoning tokens (in output)",
                            "p95 latency"], rows))


def insert_applications(report, original_html, applications_html):
    """Place Applications after Period over period, before Top users."""
    marker = f'<h2 style="{report.H2}">Top users (by tokens)</h2>'
    position = original_html.find(marker)
    if position < 0:
        raise ValueError("Could not locate Top users section after period comparison")
    return original_html[:position] + applications_html + "\n" + original_html[position:]


def clarify_period_comparison(report, original_html, cfg, data, days):
    """Label the two rolling windows without changing other report sections."""
    heading = f'<h2 style="{report.H2}">Period over period</h2>'
    start = original_html.find(heading)
    if start < 0:
        return original_html
    end = original_html.find(f'<h2 style="{report.H2}">Top users (by tokens)</h2>', start)
    if end < 0:
        raise ValueError("Could not locate section after period comparison")

    span = "24 hours" if days == 1 else f"{days:g} days"
    previous_label, current_label = f"Previous {span}", f"Last {span}"
    local_tz = timezone(timedelta(hours=float(cfg["timezone_offset_hours"])))
    previous_start = data["prev_start"].astimezone(local_tz)
    current_start = data["window_start"].astimezone(local_tz)
    current_end = data["window_end"].astimezone(local_tz)
    tz_label = html.escape(str(cfg["timezone_label"]))
    explanation = (
        f'<p style="{report.FONT}font-size:12px;color:#374151;margin:4px 0 8px">'
        f'{previous_label}: {previous_start:%d %b %Y %H:%M} to '
        f'{current_start:%d %b %Y %H:%M} {tz_label}<br>'
        f'{current_label}: {current_start:%d %b %Y %H:%M} to '
        f'{current_end:%d %b %Y %H:%M} {tz_label}</p>'
    )
    section = original_html[start:end]
    if '>Prior period</th>' not in section or '>This period</th>' not in section:
        raise ValueError("Could not locate comparison column labels")
    section = section.replace(heading, heading + explanation, 1)
    section = section.replace('>Prior period</th>', f'>{previous_label}</th>', 1)
    section = section.replace('>This period</th>', f'>{current_label}</th>', 1)
    return original_html[:start] + section + original_html[end:]


def replace_rendered_table(original_html, marker, next_marker, replacement):
    """Replace one rendered section; leave litellm_report.py untouched."""
    heading = original_html.find(marker)
    start = original_html.rfind('<p style=', 0, heading)
    if heading < 0 or start < 0 or start > heading:
        raise ValueError(f"Could not locate report table: {marker}")
    end = original_html.find(next_marker, heading)
    if end < 0:
        raise ValueError(f"Could not locate report section: {next_marker}")
    if not next_marker.startswith("<"):
        end = original_html.rfind('<p style=', heading, end)
        if end < 0:
            raise ValueError(f"Could not locate paragraph before: {next_marker}")
    return original_html[:start] + replacement + original_html[end:]


def grouped_failure_rows(report, failed_rows):
    """Group matching failures while keeping every user and key distinct."""

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
            ["Time", "Request ID"],
            [[r["time"], r["request_id"] or "(missing)"] for r in requests],
            99,
        )
        request_rows = request_rows.replace('style="border-collapse:collapse;',
                                            'style="table-layout:fixed;border-collapse:collapse;', 1)
        details = (
            f'<details style="width:100%"><summary style="cursor:pointer;color:#1d4ed8;'
            f'overflow-wrap:anywhere">'
            f'<strong>Show {len(requests)} request ID{"s" if len(requests) != 1 else ""}</strong> &middot; '
            f'{html.escape(str(code or "-"))} &middot; {html.escape(str(model))} &middot; '
            f'{html.escape(str(user))}<br>'
            f'<span style="color:#374151">Key: {html.escape(str(key))} &middot; '
            f'Last seen: {html.escape(str(requests[0]["time"]))} &middot; '
            f'{html.escape(message)}</span></summary>'
            f'<div style="width:100%;overflow-wrap:anywhere;word-break:break-word">'
            f'{request_rows}</div></details>'
        )
        rendered_rows.append([details])
    return rendered_rows, len(groups)


def group_error_types(report, original_html, failed_rows):
    """Put expandable request IDs in the top Issues and reliability table."""
    rendered_rows, group_count = grouped_failure_rows(report, failed_rows)
    replacement = (
        f'<p style="{report.FONT}font-size:12px;color:#6b7280;margin:0 0 2px">'
        f'Failure groups by user: {len(failed_rows)} failed requests in this report period, '
        f'{group_count} groups</p>'
        + report.table(["Failure group — open to view request IDs"], rendered_rows, 99)
    )
    return replace_rendered_table(original_html, "Error types grouped (",
                                  "Failure rate by model", replacement)


def group_latest_failures(report, original_html, failed_rows):
    """Also group the recent failure table in the opt-in trial."""
    rendered_rows, group_count = grouped_failure_rows(report, failed_rows)
    replacement = (
        f'<p style="{report.FONT}font-size:12px;color:#6b7280;margin:10px 0 2px">'
        f'Latest failed requests: {len(failed_rows)} shown, grouped into {group_count} rows '
        f'(counts cover only these {len(failed_rows)} requests)</p>'
        + report.table(["Failure group — open to view request IDs"], rendered_rows, 99)
    )
    return replace_rendered_table(original_html, "Latest failed requests (",
                                  '<h2 ', replacement)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, required=True,
                        help="Folder containing litellm_report.py and config.json")
    parser.add_argument("--days", type=float, default=None)
    parser.add_argument("--configure", action="store_true", help="Re-enter and save application aliases")
    parser.add_argument("--send", action="store_true", help="Send the extended report by email")
    parser.add_argument("--try-collapsible-failures", action="store_true",
                        help="Trial grouped failures with expandable request IDs")
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
    current_cfg = dict(cfg)
    if args.try_collapsible_failures:
        current_cfg["max_failed_rows"] = max(len(data["logs"]), cfg["max_failed_rows"])
    current = report.aggregate(current_cfg, data, data["logs"])
    previous = report.aggregate(cfg, data, data["prev_logs"])
    bullets, alerts = report.build_insights(cfg, current, previous, data)
    base_html = report.render_html(cfg, data, current, previous, days, bullets, alerts)
    base_html = clarify_period_comparison(report, base_html, cfg, data, days)
    if args.try_collapsible_failures and current["failed_rows"]:
        base_html = group_error_types(report, base_html, current["failed_rows"])
    if args.try_collapsible_failures:
        base_html = group_latest_failures(report, base_html,
                                          current["failed_rows"][:cfg["max_failed_rows"]])
    summary = summarize(report, data["logs"], alias_map, aliases,
                        data["window_start"], data["window_end"])
    capacity = summarize_capacity(report, data["logs"], alias_map, aliases,
                                  data["window_start"], data["window_end"],
                                  cfg["timezone_offset_hours"])
    extra_sections = (render_applications(report, summary, bool(data["logs"])) + "\n"
                      + render_capacity(report, capacity, cfg["timezone_label"]))
    extended_html = insert_applications(report, base_html, extra_sections)
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
