"""CW AI CO Pilot usage from individual LiteLLM request records."""

from collections import defaultdict
import re

from application_diagnostic import normalize_alias, reasoning_tokens


EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TITLE = "CW AI CO Pilot"


def _email(value):
    if isinstance(value, str) and EMAIL.fullmatch(value.strip()):
        return value.strip().lower()
    return None


def _is_openwebui(alias, metadata, row):
    """Recognize the dedicated OpenWebUI key without changing its report rows."""
    markers = (alias, metadata.get("user_api_key_team_alias"),
               row.get("team_id"), row.get("user"))
    for marker in markers:
        if not isinstance(marker, str):
            continue
        collapsed = re.sub(r"[^a-z0-9]", "", marker.casefold())
        if "openwebui" in collapsed:
            return True
    return False


def _new_bucket():
    return {"requests": 0, "failed": 0, "spend_usd": 0.0,
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            "reasoning_tokens": 0, "reasoning_reported_requests": 0,
            "latencies_s": []}


def _add_request(report, bucket, row, metadata, started):
    bucket["requests"] += 1
    bucket["failed"] += str(row.get("status") or "success").lower() in ("failure", "failed", "error")
    bucket["spend_usd"] += float(row.get("spend") or 0)
    bucket["input_tokens"] += int(row.get("prompt_tokens") or 0)
    bucket["output_tokens"] += int(row.get("completion_tokens") or 0)
    bucket["total_tokens"] += int(row.get("total_tokens") or 0)
    reasoning = reasoning_tokens(row, metadata)
    if reasoning is not None:
        bucket["reasoning_tokens"] += reasoning
        bucket["reasoning_reported_requests"] += 1
    ended = report.parse_ts(row.get("endTime"))
    if ended is not None and ended >= started:
        bucket["latencies_s"].append((ended - started).total_seconds())


def _finish(report, bucket):
    latencies = sorted(bucket.pop("latencies_s"))
    bucket["spend_usd"] = round(bucket["spend_usd"], 6)
    bucket["latency_samples"] = len(latencies)
    bucket["p95_latency_s"] = round(report.percentile(latencies, .95), 3) if latencies else None
    return bucket


def summarize_copilot(report, rows, alias_map, applications, start, end):
    """Include email-identified individual use; exclude three apps and OpenWebUI."""
    application_aliases = {normalize_alias(alias)
                           for names in applications.values() for alias in names}
    total = _new_bucket()
    by_user = defaultdict(_new_bucket)
    excluded = {"applications": 0, "openwebui": 0, "unattributed": 0}
    for row in rows:
        started = report.parse_ts(row.get("startTime"))
        if started is None or not start <= started <= end:
            continue
        metadata = report.row_meta(row)
        if not isinstance(metadata, dict):
            metadata = {}
        alias = (metadata.get("user_api_key_alias") or row.get("key_alias")
                 or alias_map.get(row.get("api_key")) or "")
        if normalize_alias(alias) in application_aliases:
            excluded["applications"] += 1
            continue
        if _is_openwebui(alias, metadata, row):
            excluded["openwebui"] += 1
            continue
        user = (_email(row.get("end_user")) or _email(row.get("user"))
                or _email(metadata.get("user_api_key_end_user_id")))
        if not user:
            excluded["unattributed"] += 1
            continue
        _add_request(report, total, row, metadata, started)
        _add_request(report, by_user[user], row, metadata, started)
    users = [{"user": user, **_finish(report, bucket)} for user, bucket in by_user.items()]
    users.sort(key=lambda item: (-item["total_tokens"], item["user"]))
    return {"application": TITLE, **_finish(report, total),
            "active_users": len(users), "users": users, "excluded": excluded}


def render_copilot_users(report, summary, compact):
    """Add the individual breakdown without altering existing report tables."""
    rows = []
    for user in summary["users"]:
        reasoning = (compact(user["reasoning_tokens"])
                     if user["reasoning_reported_requests"] else "-")
        if 0 < user["reasoning_reported_requests"] < user["requests"]:
            reasoning += " (partial)"
        latency = user["p95_latency_s"]
        rows.append([user["user"], report.num(user["requests"]),
                     f"{report.num(user['failed'])} ({report.pct(user['failed'], user['requests'])})",
                     report.money(user["spend_usd"]), compact(user["input_tokens"]),
                     compact(user["output_tokens"]), compact(user["total_tokens"]),
                     reasoning, f"{latency:.1f}s" if latency is not None else "-"])
    heading = (f'<h2 style="{report.H2}">{TITLE} users '
               f'({report.num(summary["active_users"])} active)</h2>')
    table = report.table(
        ["User", "Requests", "Failed", "Spend", "Input tokens",
         "Output tokens", "Total tokens", "Reasoning tokens (in output)",
         "p95 latency"], rows)
    table = table.replace('style="border-collapse:collapse;',
                          'style="table-layout:fixed;border-collapse:collapse;', 1)
    table = table.replace('<td style="',
                          '<td style="overflow-wrap:anywhere;word-break:break-word;')
    return heading + table
