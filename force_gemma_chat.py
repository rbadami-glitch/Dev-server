"""Route text-generation API calls through the existing LiteLLM aliases.

This wrapper runs before LiteLLM authentication and model lookup. The public
model catalog is left alone; an unlisted request name becomes the `codestral`
alias, which is configured to use Gemma 31B. Existing `devstral` requests keep
their alias so their current virtual-key permissions still apply.
"""

import json


TEXT_PATHS = frozenset(
    {
        "/chat/completions",
        "/v1/chat/completions",
        "/completions",
        "/v1/completions",
        "/responses",
        "/v1/responses",
        "/messages",
        "/v1/messages",
    }
)


class ForceGemmaChat:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path", "").rstrip("/") not in TEXT_PATHS
        ):
            await self.app(scope, receive, send)
            return

        chunks = []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break

        body = b"".join(chunks)
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None

        if (
            isinstance(payload, dict)
            and isinstance(payload.get("model"), str)
            and payload["model"].strip()
        ):
            requested = payload["model"]
            alias = "devstral" if requested == "devstral" else "codestral"
            if requested != alias:
                payload["model"] = alias
                body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                scope = dict(scope)
                scope["headers"] = [
                    (name, value)
                    for name, value in scope.get("headers", ())
                    if name.lower() != b"content-length"
                ] + [(b"content-length", str(len(body)).encode("ascii"))]

        first = True

        async def replay_receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)
