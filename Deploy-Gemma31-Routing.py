"""Preview, install, or roll back the LiteLLM Gemma 31B chat routing change.

Run from a copy of this file beside force_gemma_chat.py. Apply modifies files
only. It never starts, stops, or restarts LiteLLM or Ollama.
"""

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_TARGET = Path(r"D:\git pull for sanjay\commands\litellm-proxy")
MODULE_NAME = "force_gemma_chat.py"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_text(path):
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    newline = "\r\n" if b"\r\n" in raw else "\n"
    return text, bom, newline


def encode_text(text, bom, newline):
    data = text.replace("\n", newline).encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise ValueError(f"Expected exactly one {label}; found {text.count(old)}")
    return text.replace(old, new, 1)


def split_model_blocks(text):
    marker_codestral = "  - model_name: codestral\n"
    marker_devstral = "  - model_name: devstral\n"
    if text.count(marker_codestral) != 1 or text.count(marker_devstral) != 1:
        raise ValueError("Expected one codestral and one devstral model block")
    c_start = text.index(marker_codestral)
    d_start = text.index(marker_devstral)
    if c_start >= d_start:
        raise ValueError("Unexpected model block order")
    next_section = re.search(r"(?m)^[A-Za-z_][A-Za-z_0-9]*:\s*$", text[d_start:])
    if next_section is None:
        raise ValueError("Cannot find the section after the model list")
    d_end = d_start + next_section.start()
    return text[:c_start], text[c_start:d_start], text[d_start:d_end], text[d_end:]


def transform_config(text):
    prefix, codestral, devstral, suffix = split_model_blocks(text)
    codestral = replace_once(
        codestral,
        "      model: openai/codestral:22b\n",
        "      model: ollama_chat/gemma4:31b\n",
        "codestral upstream model",
    )
    codestral = replace_once(
        codestral,
        "      api_base: os.environ/OLLAMA_OPENAI_BASE_URL\n",
        "      api_base: http://10.104.74.10:11434\n",
        "codestral API base",
    )
    codestral = replace_once(
        codestral,
        "      api_key: os.environ/OLLAMA_OPENAI_API_KEY\n",
        "",
        "codestral upstream API key reference",
    )
    codestral = replace_once(
        codestral,
        "      max_tokens: 16384\n",
        "      max_tokens: 16384\n"
        "      additional_drop_params:\n"
        "        - reasoning_effort\n",
        "codestral max tokens",
    )
    devstral = replace_once(
        devstral,
        "      model: ollama_chat/devstral\n",
        "      model: ollama_chat/gemma4:31b\n",
        "devstral upstream model",
    )
    return prefix + codestral + devstral + suffix


def transform_runner(text):
    text = replace_once(
        text,
        "    from litellm.proxy.proxy_server import app\n",
        "    from litellm.proxy.proxy_server import app\n"
        "    from force_gemma_chat import ForceGemmaChat\n",
        "LiteLLM app import",
    )
    return replace_once(
        text,
        "    uvicorn.run(app, host=args.host, port=args.port)\n",
        "    uvicorn.run(ForceGemmaChat(app), host=args.host, port=args.port)\n",
        "Uvicorn launch",
    )


def candidate_files(target, source_module):
    config_path = target / "config.yaml"
    runner_path = target / "run_litellm_background.py"
    if not source_module.is_file():
        raise FileNotFoundError(f"Routing module missing: {source_module}")
    config, config_bom, config_newline = read_text(config_path)
    runner, runner_bom, runner_newline = read_text(runner_path)
    new_config = transform_config(config)
    new_runner = transform_runner(runner)
    module_data = source_module.read_bytes()
    ast.parse(new_runner, filename=str(runner_path))
    ast.parse(module_data.decode("utf-8-sig"), filename=str(source_module))
    return {
        "config.yaml": encode_text(new_config, config_bom, config_newline),
        "run_litellm_background.py": encode_text(new_runner, runner_bom, runner_newline),
        MODULE_NAME: module_data,
    }


def atomic_write(path, data):
    temporary = path.with_name(path.name + ".gemma-routing-tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def restore(target, backup):
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    if Path(manifest["target"]).resolve() != target.resolve():
        raise ValueError("Backup target does not match the requested target")
    for name in ("config.yaml", "run_litellm_background.py"):
        atomic_write(target / name, (backup / name).read_bytes())
    if manifest["module_existed"]:
        atomic_write(target / MODULE_NAME, (backup / MODULE_NAME).read_bytes())
    else:
        (target / MODULE_NAME).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("plan", "apply", "rollback"), default="plan")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    target = args.target.resolve()

    if args.mode == "rollback":
        if args.backup_dir is None:
            parser.error("--backup-dir is required for rollback")
        restore(target, args.backup_dir.resolve())
        print("Files restored. LiteLLM was not restarted.")
        return

    source_module = Path(__file__).resolve().with_name(MODULE_NAME)
    candidates = candidate_files(target, source_module)
    print(f"Target: {target}")
    for name, data in candidates.items():
        path = target / name
        old_hash = sha256(path.read_bytes()) if path.exists() else "absent"
        print(f"{name}: {old_hash} -> {sha256(data)}")
    print("Public names remain codestral and devstral; both use ollama_chat/gemma4:31b.")
    print("Unlisted text-model names route through codestral; non-text paths are unchanged.")
    if args.mode == "plan":
        print("PLAN ONLY: no files or services were changed.")
        return

    backup = target / "GemmaRoutingBackups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup.mkdir(parents=True, exist_ok=False)
    for name in ("config.yaml", "run_litellm_background.py"):
        shutil.copy2(target / name, backup / name)
    module_existed = (target / MODULE_NAME).exists()
    if module_existed:
        shutil.copy2(target / MODULE_NAME, backup / MODULE_NAME)
    (backup / "manifest.json").write_text(
        json.dumps({"target": str(target), "module_existed": module_existed}, indent=2),
        encoding="utf-8",
    )
    try:
        for name, data in candidates.items():
            atomic_write(target / name, data)
    except Exception:
        restore(target, backup)
        raise
    print(f"Backup: {backup}")
    print("Files updated. LiteLLM and Ollama were not restarted.")
    print("Restart LiteLLM only in an approved maintenance window; then test all three text APIs.")


if __name__ == "__main__":
    main()
