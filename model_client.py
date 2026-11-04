"""Model layer (Milestones 2 and 9).

`ModelClient` is the interface every agent talks to. Agents never call an AI
provider directly, so swapping models means writing one small adapter instead
of rewriting the system.

Two providers: `StubProvider` (deterministic offline stand-in, replies marked
[stub]) and `MuseProvider` (live Meta Model API, Responses protocol, model
`muse-spark-1.3` Standard tier). `get_model_client()` picks live when
META_API_KEY is set, else the stub. Contributor-tier IDs can never reach
either provider: config refuses to boot with them (M8).

Providers also expose `generate_image` (Muse Image stills) and
`describe_image` (Muse Spark vision over Responses input blocks). The
stub raises ModelError for both: pixels are never faked offline.
"""

from dataclasses import dataclass

import httpx

from config import settings

API_BASE_URL = "https://api.meta.ai/v1"
RESPONSES_PATH = "/responses"
IMAGES_PATH = "/images/generations"
IMAGE_MODEL_ID = "muse-image-1.0"

# M8 hardening requirement, honored here: explicit network timeouts on
# every model call (reasoning models answer slowly; the read budget
# reflects that, while connect/write stay tight).
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 60.0
WRITE_TIMEOUT = 10.0
POOL_TIMEOUT = 10.0
# Image generation is slower than text; its read budget reflects that.
IMAGE_READ_TIMEOUT = 120.0


class ModelError(Exception):
    """The model provider failed (network, auth, bad response). Never
    carries the API key: only statuses and short body excerpts."""


@dataclass
class ModelResponse:
    """What one model call returns: text plus measurable usage figures
    (character counts always; token counts as reported, else 0)."""

    text: str
    model: str
    input_chars: int
    output_chars: int
    input_tokens: int = 0
    output_tokens: int = 0


class ModelClient:
    """Interface: turn a system prompt + messages into a reply."""

    name = "base"
    supports_planning = False
    supports_vision = False

    def generate(self, *, system: str, messages: list, response_format=None, max_output_tokens=None) -> ModelResponse:
        """Produce a reply. Every provider implements this method."""
        raise NotImplementedError

    def generate_image(self, *, prompt: str) -> dict:
        """Create one illustration. Offline providers cannot fake pixels."""
        raise ModelError("image generation needs the live Muse model (no API key set)")

    def describe_image(self, *, image_data_url: str, question: str, system: str = "") -> ModelResponse:
        """Describe a photo. Offline providers cannot see."""
        raise ModelError("image understanding needs the live Muse model (no API key set)")


class StubProvider(ModelClient):
    """Deterministic offline stand-in. Replies are always marked [stub]."""

    name = "stub-1"

    def generate(self, *, system: str, messages: list, response_format=None, max_output_tokens=None) -> ModelResponse:
        user_turns = [entry for entry in messages if entry.get("role") == "user"]
        latest = user_turns[-1].get("content", "") if user_turns else ""
        text = (
            f"[stub] Received {len(latest)} characters across {len(user_turns)} "
            "user turn(s). Connect the Muse model to get real answers."
        )
        input_chars = len(system or "") + sum(len(entry.get("content", "")) for entry in messages)
        return ModelResponse(text=text, model=self.name, input_chars=input_chars, output_chars=len(text))


def _input_item(role: str, text: str) -> dict:
    """One Responses input message (typed blocks per the API docs).

    User turns carry `input_text`; replayed assistant turns carry
    `output_text`. Anything that is not clearly the assistant's is
    treated as the user's — the model must never mistake our history
    replay for its own new output.
    """
    if role == "assistant":
        return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}
    return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}


def _build_responses_body(
    *, model: str, system: str, messages: list, response_format=None, max_output_tokens=None
) -> dict:
    """Map one generate() call onto a Responses API request body.

    Per the quickstart: top-level `instructions` carries the system
    prompt, `input` carries the user text. A lone user turn stays a
    plain string (every planner-style caller sends exactly one). Real
    multi-turn history becomes a typed message list so the model sees
    who said what instead of one flattened blob. A format becomes
    `text.format` (the Responses structured-output parameter, not the
    Chat Completions `response_format` name).
    """
    turns = []
    for entry in messages:
        if isinstance(entry, dict):
            turns.append((entry.get("role", "user"), entry.get("content", "")))
        else:
            turns.append(("user", str(entry)))
    if len(turns) == 1 and turns[0][0] == "user":
        body = {"model": model, "input": turns[0][1]}
    else:
        body = {"model": model, "input": [_input_item(role, text) for role, text in turns]}
    if (system or "").strip():
        body["instructions"] = system
    if response_format is not None:
        body["text"] = {"format": response_format}
    if max_output_tokens is not None:
        body["max_output_tokens"] = max_output_tokens
    return body


def _parse_responses_body(payload) -> tuple:
    """Pull (text, input_tokens, output_tokens) from a Responses object.

    The answer is the concatenated `output_text` of the `message` item.
    Token counts come from `usage`, defaulting to 0 when unreported.
    """
    if not isinstance(payload, dict):
        raise ModelError("model returned an invalid response")
    status = payload.get("status")
    if status is not None and status != "completed":
        details = payload.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, dict) else None
        raise ModelError(f"model status {status!r}" + (f" ({reason})" if reason else ""))
    texts = []
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "output_text":
                    texts.append(block.get("text", ""))
    text = "".join(texts).strip()
    if not text:
        raise ModelError("model returned no text")
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    try:
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
    except (TypeError, ValueError):
        input_tokens = output_tokens = 0
    return text, input_tokens, output_tokens


class MuseProvider(ModelClient):
    """Live Meta Model API provider (Responses protocol)."""

    supports_planning = True
    supports_vision = True

    def __init__(self, *, api_key: str = "", model_id: str = "") -> None:
        self.api_key = api_key or settings.meta_api_key
        self.model_id = model_id or settings.model_id
        self.name = self.model_id

    def generate(self, *, system: str, messages: list, response_format=None, max_output_tokens=None) -> ModelResponse:
        if not messages:
            raise ModelError("no messages to send")
        body = _build_responses_body(
            model=self.model_id,
            system=system,
            messages=messages,
            response_format=response_format,
            max_output_tokens=max_output_tokens,
        )
        try:
            response = httpx.post(
                API_BASE_URL + RESPONSES_PATH,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
                timeout=httpx.Timeout(
                    connect=CONNECT_TIMEOUT, read=READ_TIMEOUT, write=WRITE_TIMEOUT, pool=POOL_TIMEOUT
                ),
            )
        except httpx.HTTPError as exc:
            raise ModelError(f"model request failed: {exc}")
        if response.status_code != 200:
            raise ModelError(f"model error {response.status_code}: {response.text[:300]}")
        try:
            payload = response.json()
        except ValueError:
            raise ModelError("model returned invalid JSON")
        text, input_tokens, output_tokens = _parse_responses_body(payload)
        input_chars = len(system or "") + sum(
            len(entry.get("content", "")) if isinstance(entry, dict) else len(str(entry)) for entry in messages
        )
        return ModelResponse(
            text=text,
            model=self.name,
            input_chars=input_chars,
            output_chars=len(text),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def describe_image(self, *, image_data_url: str, question: str, system: str = "") -> ModelResponse:
        """Ask Muse Spark about an image (base64 data URL, Responses input blocks).

        Per the image-understanding docs: `input` is a message list whose
        user message carries `input_text` plus an `input_image` block with
        the data URL as a plain `image_url` string.
        """
        if not (image_data_url or "").strip():
            raise ModelError("no image to describe")
        body = {
            "model": self.model_id,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": question or "What is in this image?"},
                        {"type": "input_image", "image_url": image_data_url},
                    ],
                }
            ],
        }
        if (system or "").strip():
            body["instructions"] = system
        try:
            response = httpx.post(
                API_BASE_URL + RESPONSES_PATH,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
                timeout=httpx.Timeout(
                    connect=CONNECT_TIMEOUT, read=READ_TIMEOUT, write=WRITE_TIMEOUT, pool=POOL_TIMEOUT
                ),
            )
        except httpx.HTTPError as exc:
            raise ModelError(f"model request failed: {exc}")
        if response.status_code != 200:
            raise ModelError(f"model error {response.status_code}: {response.text[:300]}")
        try:
            payload = response.json()
        except ValueError:
            raise ModelError("model returned invalid JSON")
        text, input_tokens, output_tokens = _parse_responses_body(payload)
        return ModelResponse(
            text=text,
            model=self.name,
            input_chars=len(question or "") + len(image_data_url),
            output_chars=len(text),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def generate_image(self, *, prompt: str) -> dict:
        """Create one still with Muse Image. Returns b64 bytes plus format.

        Per the image-generation docs: OpenAI-compatible
        POST /v1/images/generations, model `muse-image-1.0`, one image,
        base64 bytes by default in `data[0].b64_json`.
        """
        cleaned = (prompt or "").strip()
        if not cleaned:
            raise ModelError("no prompt to illustrate")
        try:
            response = httpx.post(
                API_BASE_URL + IMAGES_PATH,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": IMAGE_MODEL_ID, "prompt": cleaned, "n": 1},
                timeout=httpx.Timeout(
                    connect=CONNECT_TIMEOUT, read=IMAGE_READ_TIMEOUT, write=WRITE_TIMEOUT, pool=POOL_TIMEOUT
                ),
            )
        except httpx.HTTPError as exc:
            raise ModelError(f"image request failed: {exc}")
        if response.status_code != 200:
            raise ModelError(f"image error {response.status_code}: {response.text[:300]}")
        try:
            payload = response.json()
        except ValueError:
            raise ModelError("image service returned invalid JSON")
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or not payload["data"]:
            raise ModelError("image service returned no image")
        first = payload["data"][0]
        b64 = first.get("b64_json", "") if isinstance(first, dict) else ""
        if not b64:
            raise ModelError("image service returned no image bytes")
        image_format = payload.get("output_format") or "webp"
        if image_format not in ("webp", "png", "jpeg"):
            image_format = "webp"
        return {"b64": b64, "format": image_format, "model": IMAGE_MODEL_ID}


def get_model_client() -> ModelClient:
    """Live Muse provider when META_API_KEY is set, else the offline stub."""
    if settings.meta_api_key.strip():
        return MuseProvider()
    return StubProvider()
