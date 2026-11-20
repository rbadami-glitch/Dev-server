"""CW AI Copilot totals from individual LiteLLM request records."""

import re

from application_diagnostic import normalize_alias, reasoning_tokens


EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TITLE = "CW AI Copilot"
OTHER_UNATTRIBUTED = "Unattributed / other"


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


def request_email(row, metadata):
    return (_email(row.get("end_user")) or _email(row.get("user"))
            or _email(metadata.get("user_api_key_end_user_id")))


def other_usage_group(alias, metadata, row):
    """Split requests outside the three configured application aliases."""
    if _is_openwebui(alias, metadata, row):
        return "OpenWebUI"
    return TITLE if request_email(row, metadata) else OTHER_UNATTRIBUTED


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
    active_users = set()
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
        group = other_usage_group(alias, metadata, row)
        if group == "OpenWebUI":
            excluded["openwebui"] += 1
            continue
        if group == OTHER_UNATTRIBUTED:
            excluded["unattributed"] += 1
            continue
        user = request_email(row, metadata)
        _add_request(report, total, row, metadata, started)
        active_users.add(user)
    return {"application": TITLE, **_finish(report, total),
            "active_users": len(active_users), "excluded": excluded}


def render_copilot_active_users(report, summary):
    """Show a bounded user count; the original Top users table remains unchanged."""
    return (f'<p style="{report.FONT}font-size:12px;color:#111827;margin:4px 0 10px">'
            f'<strong>{TITLE} active users: {report.num(summary["active_users"])}</strong></p>')
