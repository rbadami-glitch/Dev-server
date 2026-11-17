"""Peak-demand measures derived from LiteLLM's per-request spend logs."""

from collections import Counter, defaultdict
from datetime import timedelta, timezone
import math

from application_diagnostic import as_object, normalize_alias


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
        app = alias_to_app.get(normalize_alias(alias), "Other users / unattributed")
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
            "other_peak_hour_requests": app_counts["Other users / unattributed"],
            "queue_by_model": queue_by_model}


def render_capacity(report, stats, timezone_label):
    """Render a new section without changing any original report section."""
    heading = f'<h2 style="{report.H2}">Capacity and peak demand</h2>'
    if not stats["requests"]:
        return heading + f'<p style="{report.FONT}font-size:12px">No per-request logs in this period.</p>'

    def at(value):
        return f"{value:%d %b %H:%M} {timezone_label}" if value else "-"

    peak_req_at, peak_req = stats["peak_requests_minute"]
    peak_in_at, peak_in = stats["peak_input_minute"]
    peak_out_at, peak_out = stats["peak_output_minute"]
    concurrent = stats["peak_concurrent"]
    metrics = [
        ["Busiest hour", at(stats["peak_hour"]), report.num(stats["peak_hour_requests"]) + " requests"],
        ["Peak requests/minute", at(peak_req_at), report.num(peak_req)],
        ["Peak input tokens/minute", at(peak_in_at), report.num(peak_in)],
        ["Peak output tokens/minute", at(peak_out_at), report.num(peak_out)],
        ["Peak concurrent requests", at(stats["peak_concurrent_at"]),
         report.num(concurrent) if concurrent is not None else "Unavailable"],
    ]
    result = heading + report.table(["Measure", "When", "Value"], metrics, 2)
    result += (f'<p style="{report.FONT}font-size:11px;color:#6b7280">'
               f'Concurrency is estimated from overlapping start/end times on '
               f'{report.num(stats["valid_intervals"])} of {report.num(stats["requests"])} logged requests. '
               f'Tokens are assigned to each request\'s start minute; output tokens are not measured as they stream.'
               f'</p>')

    hour_total = stats["peak_hour_requests"]
    share_rows = [[app, report.num(count), report.pct(count, hour_total)]
                  for app, count in stats["app_peak_hour_counts"].items()]
    other = stats["other_peak_hour_requests"]
    share_rows.append(["Other users / unattributed", report.num(other), report.pct(other, hour_total)])
    result += (f'<p style="{report.FONT}font-size:12px;color:#6b7280;margin:10px 0 2px">'
               f'Share of all requests in the busiest hour ({at(stats["peak_hour"])})</p>'
               + report.table(["Application / other", "Requests", "Share"], share_rows))

    queue_rows = [[item["model"], report.num(item["requests"]),
                   report.num(item["samples"]),
                   f'{item["p95_s"]:.2f}s' if item["p95_s"] is not None else "-"]
                  for item in stats["queue_by_model"]]
    if any(item["samples"] for item in stats["queue_by_model"]):
        result += (f'<p style="{report.FONT}font-size:12px;color:#6b7280;margin:10px 0 2px">'
                   f'Reported model queue time (p95; only measured requests)</p>'
                   + report.table(["Model", "Requests", "Queue samples", "p95 queue time"], queue_rows))
    else:
        result += (f'<p style="{report.FONT}font-size:12px;color:#6b7280;margin:10px 0 2px">'
                   f'Model queue time: unavailable in the retrieved spend logs.</p>')
    result += (f'<p style="{report.FONT}font-size:11px;color:#6b7280">'
               f'Peak demand alone does not establish whether capacity was exhausted; '
               f'compare with configured limits and measured queue time.</p>')
    return result
