"""Peak-demand measures derived from LiteLLM's per-request spend logs."""

from collections import Counter, defaultdict
from datetime import timedelta, timezone
import math

from application_diagnostic import as_object, normalize_alias
from copilot_report import OTHER_UNATTRIBUTED, TITLE as COPILOT_TITLE, other_usage_group


def _peak(bucket):
    """Return the earliest time among buckets tied for the highest value."""
    return min(bucket.items(), key=lambda item: (-item[1], item[0])) if bucket else (None, 0)


def _model_queue_seconds(row, metadata):
    """Only accept an explicitly named model queue-time measurement."""
    for source in (row, metadata, as_object(metadata.get("spend_logs_metadata"))):
        for field, scale in (("model_queue_time_ms", 0.001), ("model_queue_time_s", 1)):
            value = source.get(field)
            if value is None:
                continue
            try:
                seconds = float(value) * scale
            except (TypeError, ValueError):
                continue
            if math.isfinite(seconds) and seconds >= 0:
                return seconds
    return None


def summarize_capacity(report, rows, aliases, selected, start, end, tz_offset_hours):
    """Summarize only requests that started inside the current report window."""
    tz = timezone(timedelta(hours=float(tz_offset_hours)))
    alias_to_app = {normalize_alias(alias): app for app, names in selected.items()
                    for alias in names}
    minutes = Counter()
    hours = Counter()
    input_by_minute = Counter()
    output_by_minute = Counter()
    app_by_hour = defaultdict(Counter)
    model_requests = Counter()
    queue_samples = defaultdict(list)
    intervals = []
    request_count = 0

    for row in rows:
        ts = report.parse_ts(row.get("startTime"))
        if ts is None or not start <= ts <= end:
            continue
        request_count += 1
        local = ts.astimezone(tz)
        minute = local.replace(second=0, microsecond=0)
        hour = local.replace(minute=0, second=0, microsecond=0)
        minutes[minute] += 1
        hours[hour] += 1
        input_by_minute[minute] += int(row.get("prompt_tokens") or 0)
        output_by_minute[minute] += int(row.get("completion_tokens") or 0)

        metadata = report.row_meta(row)
        alias = (metadata.get("user_api_key_alias") or row.get("key_alias")
                 or aliases.get(row.get("api_key")) or "")
        app = alias_to_app.get(normalize_alias(alias))
        if app is None:
            app = other_usage_group(alias, metadata, row)
        app_by_hour[hour][app] += 1

        model = str(row.get("model") or "(unknown)")
        model_requests[model] += 1
        queued = _model_queue_seconds(row, metadata)
        if queued is not None:
            queue_samples[model].append(queued)

        finish = report.parse_ts(row.get("endTime"))
        if finish is not None and finish > ts:
            intervals.append((ts, finish))

    # End events sort before starts at the same instant: touching requests do not overlap.
    events = sorted(((time, delta) for begin, finish in intervals
                     for time, delta in ((begin, 1), (finish, -1))),
                    key=lambda item: (item[0], item[1]))
    concurrent = peak_concurrent = 0
    concurrent_at = None
    for time, delta in events:
        concurrent += delta
        if concurrent > peak_concurrent:
            peak_concurrent, concurrent_at = concurrent, time.astimezone(tz)

    peak_hour, hour_requests = _peak(hours)
    app_counts = app_by_hour[peak_hour] if peak_hour is not None else Counter()
    queue_by_model = []
    for model, count in model_requests.most_common():
        samples = sorted(queue_samples[model])
        queue_by_model.append({"model": model, "requests": count,
                               "samples": len(samples),
                               "p95_s": report.percentile(samples, .95) if samples else None})

    return {"requests": request_count,
            "peak_hour": peak_hour, "peak_hour_requests": hour_requests,
            "peak_requests_minute": _peak(minutes),
            "peak_input_minute": _peak(input_by_minute),
            "peak_output_minute": _peak(output_by_minute),
            "peak_concurrent": peak_concurrent if intervals else None,
            "peak_concurrent_at": concurrent_at, "valid_intervals": len(intervals),
            "app_peak_hour_counts": {app: app_counts[app] for app in selected},
            "copilot_peak_hour_requests": app_counts[COPILOT_TITLE],
            "openwebui_peak_hour_requests": app_counts["OpenWebUI"],
            "unattributed_peak_hour_requests": app_counts[OTHER_UNATTRIBUTED],
            "queue_by_model": queue_by_model}


def render_capacity(report, stats, timezone_label):
    """Render a new section without changing any original report section."""
    heading = f'<h2 style="{report.H2}">Capacity and peak demand</h2>'
    if not stats["requests"]:
        return heading + report.table(["Measure", "When", "Value"],
                                      [["Request logs", "-", "Unavailable"]], 2)

    def at(value):
        return f"{value:%d %b %H:%M} {timezone_label}" if value else "-"

    peak_req_at, peak_req = stats["peak_requests_minute"]
    peak_in_at, peak_in = stats["peak_input_minute"]
    peak_out_at, peak_out = stats["peak_output_minute"]
    concurrent = stats["peak_concurrent"]
    metrics = [
        ["Busiest hour", at(stats["peak_hour"]), report.num(stats["peak_hour_requests"]) + " requests"],
        ["Peak requests/minute", at(peak_req_at), report.num(peak_req)],
        ["Peak input tokens/minute (request start)", at(peak_in_at), report.num(peak_in)],
        ["Peak output tokens/minute (request start)", at(peak_out_at), report.num(peak_out)],
        ["Peak concurrent requests (logged)", at(stats["peak_concurrent_at"]),
         report.num(concurrent) if concurrent is not None else "Unavailable"],
        ["Requests with valid timing", "-",
         f'{report.num(stats["valid_intervals"])} / {report.num(stats["requests"])}'],
    ]
    has_queue = any(item["samples"] for item in stats["queue_by_model"])
    if not has_queue:
        metrics.append(["Model queue time", "-", "Unavailable"])
    result = heading + report.table(["Measure", "When", "Value"], metrics, 2)

    hour_total = stats["peak_hour_requests"]
    share_rows = [[app, report.num(count), report.pct(count, hour_total)]
                  for app, count in stats["app_peak_hour_counts"].items()]
    copilot = stats["copilot_peak_hour_requests"]
    share_rows.append([COPILOT_TITLE, report.num(copilot), report.pct(copilot, hour_total)])
    for label, count in (("OpenWebUI", stats["openwebui_peak_hour_requests"]),
                         (OTHER_UNATTRIBUTED, stats["unattributed_peak_hour_requests"])):
        if count:
            share_rows.append([label, report.num(count), report.pct(count, hour_total)])
    result += (f'<h3 style="{report.FONT}font-size:13px;color:#111827;margin:14px 0 4px">'
               f'App share of busiest hour</h3>'
               + report.table(["Application / source", "Requests", "% of busiest hour"], share_rows))

    queue_rows = [[item["model"], report.num(item["requests"]),
                   report.num(item["samples"]),
                   f'{item["p95_s"]:.2f}s' if item["p95_s"] is not None else "-"]
                  for item in stats["queue_by_model"]]
    if has_queue:
        result += (f'<h3 style="{report.FONT}font-size:13px;color:#111827;margin:14px 0 4px">'
                   f'Model queue time</h3>'
                   + report.table(["Model", "Requests", "Queue samples", "p95 queue time"], queue_rows))
    return result
