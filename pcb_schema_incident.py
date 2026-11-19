#!/usr/bin/env python3
"""Find the 19 Nov PCB draft/review/revision calls and publish encrypted evidence.

Run on the Windows LiteLLM server. The incident window and prompt-token counts
are specific to PCB's 19 November 2026 report. No request body or private key is
printed or written to this checkout in plaintext.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import urllib.error
import urllib.request
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


def build_enum_probe(model: str, marker: str) -> dict:
    """Keep the only valid citation marker in the schema, not in the prompt."""
    cell = {"type": "string", "enum": [marker]}
    citation = {"type": "object", "additionalProperties": False,
                "required": ["cell"], "properties": {"cell": cell}}
    claim = {"type": "object", "additionalProperties": False,
             "required": ["evidence"], "properties": {
                 "evidence": {"type": "array", "minItems": 1, "maxItems": 1,
                              "items": citation}}}
    schema = {"type": "object", "additionalProperties": False,
              "required": ["claims"], "properties": {
                  "claims": {"type": "array", "minItems": 1, "maxItems": 1,
                             "items": claim}}}
    return {"model": model,
            "messages": [{"role": "user", "content":
                          "Return one claim with one evidence cell. Set cell to NOT_ALLOWED."}],
            "temperature": 0, "stream": False, "max_tokens": 1024,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "pcb_enum_canary", "strict": True, "schema": schema}}}


def probe_enum(cfg: dict, model: str) -> tuple[bool, str | None]:
    marker = "allowed_" + secrets.token_hex(12)
    body = json.dumps(build_enum_probe(model, marker)).encode("utf-8")
    url = cfg["litellm_base_url"].rstrip("/") + "/v1/chat/completions"
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": "Bearer " + cfg["litellm_api_key"],
        "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            reply = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Enum probe failed with HTTP {exc.code}; no response body printed.") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("Enum probe could not reach LiteLLM; no response body printed.") from exc
    try:
        choice = reply["choices"][0]
        answer = json.loads(choice["message"]["content"])
        actual = answer["claims"][0]["evidence"][0]["cell"]
        return actual == marker, choice.get("finish_reason")
    except (KeyError, IndexError, TypeError, ValueError):
        return False, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--aliases-file", type=Path, default=apps.ALIASES_FILE,
                        help="Server-local application alias JSON; needed for a fresh clone")
    parser.add_argument("--publish", action="store_true",
                        help="Publish each matched request with the existing encrypted exporter")
    parser.add_argument("--probe-enum", action="store_true",
                        help="Send one synthetic nested-enum request through this LiteLLM route")
    parser.add_argument("--model", default=None,
                        help="Override the matched public LiteLLM model group for the enum probe")
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
    if args.probe_enum:
        groups = {row.get("model_group") for _, row in matches}
        if args.model is None and (len(groups) != 1 or None in groups):
            raise RuntimeError("Could not identify one public model group for the matched calls.")
        model = args.model or next(iter(groups))
        respected, finish = probe_enum(cfg, model)
        print("Enum probe public model group:", model)
        print("Synthetic nested citation enum respected:", respected,
              "finish_reason:", finish)
        if not respected:
            return 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, evidence.EvidenceError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
