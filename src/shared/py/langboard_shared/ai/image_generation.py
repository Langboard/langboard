"""Explicit, bounded image generation through independently configured native profiles."""

from asyncio import create_task, shield
from base64 import b64decode, b64encode
from hashlib import sha256
from io import BytesIO
from json import loads
from threading import BoundedSemaphore
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4
from anyio import to_thread
from httpx import AsyncClient
from pydantic import BaseModel, ConfigDict, Field
from ..core.security import KeyVault
from ..Env import Env
from ..tasks.webhooks.utils import ResolvedWebhookTarget, ensure_public_webhook_url
from ..tasks.webhooks.utils.WebhookUrlPolicy import validate_webhook_url


SUPPORTED_PROVIDERS = ("OpenAI", "OpenAI Compatible", "LiteLLM")
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 12 * 1024 * 1024
# Nonwaiting admission across generation and decode; per process, not distributed quota.
MAX_IN_FLIGHT = 2
_IMAGE_CAPACITY = BoundedSemaphore(MAX_IN_FLIGHT)


class ImageGenerationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    agent_llm: Literal["OpenAI", "OpenAI Compatible", "LiteLLM"]
    base_url: str = Field(min_length=1, max_length=2048)
    model_name: str = Field(min_length=1, max_length=256)
    enabled: bool = False
    size: Literal["1024x1024"] = "1024x1024"
    quality: Literal["auto", "low", "medium", "high", "standard", "hd"] | None = None
    response_format: Literal["b64_json", "url"] | None = None


def allowed_bases() -> set[str]:
    return {
        url.strip().rstrip("/")
        for url in Env.get_from_env("MODEL_PROVIDER_ALLOWED_BASE_URLS", "").split(",")
        if url.strip()
    }


def validate_image_config(value: str) -> ImageGenerationConfig:
    config = ImageGenerationConfig.model_validate(loads(value))
    config.base_url = config.base_url.strip().rstrip("/")
    config.model_name = config.model_name.strip()
    parsed = urlsplit(config.base_url)
    if (
        not config.model_name
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid image generation configuration")
    if config.base_url not in allowed_bases():
        if parsed.scheme != "https":
            raise ValueError("Public image providers require HTTPS")
        validate_webhook_url(config.base_url)
    return config


def prepare_image_binding(value: str, api_key: str | None, current=None) -> tuple[str, str]:
    """Accept material only; client-controlled secret references are never supported."""
    config = validate_image_config(value)
    key = (api_key or "").strip()
    if len(key) > 4096 or "\n" in key or "\r" in key:
        raise ValueError("Invalid image credential")
    locator = current.api_key if current else ""
    if current and locator and not key:
        previous = validate_image_config(current.value)
        if (previous.base_url, previous.agent_llm) != (config.base_url, config.agent_llm):
            raise ValueError("Supply credentials explicitly when changing the provider endpoint")
    if key:
        locator = KeyVault.store_secret(f"image-generation-{uuid4().hex}", key)
    return config.model_dump_json(exclude_none=True), locator


def binding_signature(binding) -> tuple:
    credential_digest = sha256(KeyVault.get_key(binding.api_key).encode()).digest() if binding.api_key else None
    return (
        binding.bot_type,
        binding.platform,
        binding.platform_running_type,
        binding.api_url,
        binding.value,
        binding.api_key,
        credential_digest,
    )


async def provider_target(base_url: str) -> ResolvedWebhookTarget:
    parsed = urlsplit(base_url)
    if base_url in allowed_bases():
        return ResolvedWebhookTarget(f"{base_url}/images/generations", parsed.netloc, parsed.hostname)
    return await ensure_public_webhook_url(f"{base_url}/images/generations")


class ImageGenerationError(Exception):
    def __init__(self, code: str, status: int):
        self.code = code
        self.status = status


async def bounded_response(
    client, method: str, target: ResolvedWebhookTarget, *, headers=None, payload=None, limit: int
):
    async with client.stream(
        method,
        target.url,
        headers={"Host": target.host_header, **(headers or {})},
        extensions={"sni_hostname": target.sni_hostname},
        **({"json": payload} if payload is not None else {}),
    ) as response:
        if response.status_code == 429:
            raise ImageGenerationError("image_generation_rate_limited", 429)
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > limit:
                raise ValueError("Image provider response is too large")
        return bytes(body)


def safe_preview(raw: bytes) -> str:
    # Pillow is optional for deployments without image generation support.
    from PIL import Image, ImageOps

    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Image is too large")
    with Image.open(BytesIO(raw)) as image:
        if image.format not in {"PNG", "JPEG", "WEBP"} or getattr(image, "is_animated", False):
            raise ValueError("Unsupported raster image")
        width, height = image.size
        if min(width, height) < 32 or max(width, height) > 4096 or width * height > 16_777_216:
            raise ValueError("Invalid image dimensions")
        image.load()
        preview = ImageOps.fit(image.convert("RGBA"), (512, 512), method=Image.Resampling.LANCZOS)
        # A fresh raster carries no EXIF, text, ICC or provider metadata.
        clean = Image.new("RGBA", preview.size)
        clean.paste(preview)
        output = BytesIO()
        clean.save(output, format="PNG")
    return "data:image/png;base64," + b64encode(output.getvalue()).decode("ascii")


async def _generate_preview_bytes(binding, prompt: str) -> bytes:
    config = validate_image_config(binding.value)
    if not config.enabled:
        raise ImageGenerationError("image_generation_disabled", 409)
    target = await provider_target(config.base_url)
    headers = {"Accept": "application/json"}
    if binding.api_key:
        headers["Authorization"] = "Bearer " + KeyVault.get_key(binding.api_key)
    payload = {"model": config.model_name, "prompt": prompt, "n": 1, "size": config.size}
    for key in ("quality", "response_format"):
        option = getattr(config, key)
        if option is not None:
            payload[key] = option
    async with AsyncClient(timeout=60, follow_redirects=False, trust_env=False) as client:
        response = loads(
            await bounded_response(client, "POST", target, headers=headers, payload=payload, limit=MAX_RESPONSE_BYTES)
        )
        records = response.get("data")
        if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
            raise ValueError("Invalid image response")
        record = records[0]
        if isinstance(record.get("b64_json"), str):
            encoded = record["b64_json"]
            if len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
                raise ValueError("Image is too large")
            raw = b64decode(encoded, validate=True)
        elif isinstance(record.get("url"), str):
            if urlsplit(record["url"]).scheme != "https":
                raise ValueError("Image download requires HTTPS")
            download = await ensure_public_webhook_url(record["url"])
            # Never forward the generation credential to provider-returned URLs.
            raw = await bounded_response(client, "GET", download, limit=MAX_IMAGE_BYTES)
        else:
            raise ValueError("Invalid image response")
    return raw


async def generate_preview(binding, prompt: str) -> str:
    if not _IMAGE_CAPACITY.acquire(blocking=False):
        raise ImageGenerationError("image_generation_busy", 429)
    decoding = False
    try:
        raw = await _generate_preview_bytes(binding, prompt)

        worker_released = False

        def render():
            nonlocal worker_released
            try:
                return safe_preview(raw)
            finally:
                # The worker retains admission until actual CPU work finishes,
                # including when the waiting HTTP request has been cancelled.
                worker_released = True
                _IMAGE_CAPACITY.release()

        async def decode():
            try:
                return await to_thread.run_sync(render, abandon_on_cancel=False)
            except Exception:
                if not worker_released:
                    _IMAGE_CAPACITY.release()
                raise

        task = create_task(decode())
        decoding = True
        # Retrieve a detached worker's failure if its HTTP request was cancelled.
        task.add_done_callback(lambda completed: completed.exception() if not completed.cancelled() else None)
        return await shield(task)
    finally:
        if not decoding:
            _IMAGE_CAPACITY.release()
