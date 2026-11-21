"""Read-only OpenWebUI configuration snapshot for the server-only check kit."""

import argparse
import ast
import hashlib
import json
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path


CONFIG_TABLES = (
    "config", "model", "function", "tool", "group", "group_member",
    "prompt", "knowledge", "knowledge_directory", "knowledge_file",
    "skill", "automation", "user", "access_grant", "api_key",
)
VOLATILE_COLUMNS = {"created_at", "updated_at", "last_active_at", "last_seen_at"}


def snapshot_table(connection: sqlite3.Connection, name: str) -> list[dict]:
    cursor = connection.execute(f'SELECT * FROM "{name}"')
    columns = [item[0] for item in cursor.description]
    rows = []
    for values in cursor:
        full = dict(zip(columns, values))
        stable = {}
        for key, value in full.items():
            if key in VOLATILE_COLUMNS:
                continue
            if key in {"value", "params", "meta", "valves", "specs", "data", "permissions"} and isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    pass
            stable[key] = value
        canonical = json.dumps(stable, sort_keys=True, default=str, ensure_ascii=False)
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        key = str(full.get("id", full.get("key", fingerprint)))
        rows.append(
            {
                "key": key,
                "fingerprint": fingerprint,
                "full": full,
            }
        )
    return sorted(rows, key=lambda row: row["key"])


def get_health(base_url: str) -> dict:
    results = {}
    for path in ("/api/version", "/api/config"):
        try:
            with urllib.request.urlopen(base_url.rstrip("/") + path, timeout=8) as response:
                body = json.load(response)
                results[path] = {"ok": response.status == 200}
                if path == "/api/version":
                    results[path]["version"] = body.get("version")
        except Exception as error:
            results[path] = {"ok": False, "error_type": type(error).__name__}
    return results


def get_config(connection: sqlite3.Connection, key: str, default):
    row = connection.execute('SELECT value FROM "config" WHERE key = ?', (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return default


def probe_provider(url: str, path: str, api_key: str | None = None) -> dict:
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error_type": "InvalidURL"}
    endpoint = url.rstrip("/") + path
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    try:
        request = urllib.request.Request(endpoint, headers=headers)
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.load(response)
        if isinstance(body, dict):
            models = body.get("data", body.get("models", []))
            ids = sorted(str(model.get("id", model.get("name"))) for model in models if isinstance(model, dict))
            return {"ok": True, "model_ids": ids}
        return {"ok": False, "error_type": "UnexpectedResponse"}
    except urllib.error.HTTPError as error:
        return {"ok": False, "http_status": error.code}
    except Exception as error:
        return {"ok": False, "error_type": type(error).__name__}


def get_provider_probes(connection: sqlite3.Connection) -> dict:
    results = {}
    if get_config(connection, "openai.enable", False):
        urls = get_config(connection, "openai.api_base_urls", [])
        keys = get_config(connection, "openai.api_keys", [])
        configs = get_config(connection, "openai.api_configs", {})
        for index, url in enumerate(urls):
            if not configs.get(str(index), {}).get("enable", True):
                continue
            api_key = keys[index] if index < len(keys) else None
            results[f"openai:{index}"] = probe_provider(url, "/models", api_key)
    if get_config(connection, "ollama.enable", False):
        urls = get_config(connection, "ollama.base_urls", [])
        configs = get_config(connection, "ollama.api_configs", {})
        for index, url in enumerate(urls):
            if not configs.get(str(index), {}).get("enable", True):
                continue
            results[f"ollama:{index}"] = probe_provider(url, "/api/tags")
    return results


def validate_stored_code(connection: sqlite3.Connection, names: set[str]) -> dict:
    results = {}
    for table in ("function", "tool"):
        if table not in names:
            continue
        for identifier, content in connection.execute(f'SELECT id, content FROM "{table}"'):
            try:
                ast.parse(content or "")
                results[f"{table}:{identifier}"] = {"syntax_ok": True}
            except SyntaxError as error:
                results[f"{table}:{identifier}"] = {
                    "syntax_ok": False,
                    "line": error.lineno,
                }
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1")
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error("OpenWebUI database was not found")
    connection = sqlite3.connect(f"file:{args.db.as_posix()}?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not row[0].startswith("sqlite_")
        }
        result = {
            "format": 1,
            "database_tables": sorted(names),
            "tables": {name: snapshot_table(connection, name) for name in CONFIG_TABLES if name in names},
            "missing_tables": sorted(set(CONFIG_TABLES) - names),
            "health": get_health(args.base_url),
            "provider_probes": get_provider_probes(connection),
            "code_validation": validate_stored_code(connection, names),
            "functional_coverage": "service and provider model-list endpoints; stored function/tool syntax; chat completion and function/tool actions not exercised",
        }
        # PowerShell on Windows may capture stdout using a legacy code page.
        # ASCII JSON escapes preserve Unicode without depending on that code page.
        print(json.dumps(result, sort_keys=True, default=str, ensure_ascii=True))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
