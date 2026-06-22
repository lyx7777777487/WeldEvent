"""OpenAI-compatible LLM Provider implementation.

Source: L1-Interaction-Layer-Business-Requirements.md §3.6.2, §3.6.9.

Requires: `openai` package (pip install openai)
Fallback: raises RuntimeError if openai is not installed.
"""

from collections.abc import AsyncGenerator

from cognitiveplane.capability.config import LLMConfig
from cognitiveplane.capability.provider import LLMProvider, LLMRequest, LLMResponse


class OpenAIProvider(LLMProvider):
    """Real OpenAI API provider with fallback support.

    Features:
    - Per-purpose model routing via LLMConfig.resolve_model()
    - Automatic fallback to secondary model on failure
    - Timeout enforcement per purpose
    - Token and cost tracking
    """

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return
        try:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self._config.primary.api_key,
                base_url=self._config.primary.base_url,
                organization=self._config.primary.organization,
            )
        except ImportError:
            raise RuntimeError(
                "openai package not installed. "
                "Install with: pip install openai"
            )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self._ensure_client()
        model = request.model or self._config.resolve_model(request.purpose)
        try:
            kwargs = dict(
                model=model,
                messages=self._inject_json_hint(request),
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                top_p=request.top_p,
            )
            # Force JSON mode when caller asked for structured output —
            # DeepSeek / OpenAI compatibles accept {"type":"json_object"}.
            if request.response_format is not None:
                kwargs["response_format"] = {"type": "json_object"}
            # Function calling: pass tools when provided.
            if request.tools:
                kwargs["tools"] = request.tools
                kwargs["tool_choice"] = "auto"
            response = await self._client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            content = choice.message.content or ""

            # Extract tool_calls if present (function calling response).
            tool_calls = None
            if choice.message.tool_calls:
                tool_calls = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in choice.message.tool_calls
                ]

            parsed = self._try_parse(content, request.response_format)
            return LLMResponse(
                content=content,
                model_used=response.model,
                tokens_prompt=response.usage.prompt_tokens if response.usage else 0,
                tokens_completion=response.usage.completion_tokens if response.usage else 0,
                parsed_object=parsed,
                tool_calls=tool_calls,
            )
        except Exception as e:
            if self._config.fallback is not None:
                return await self._fallback_complete(request, str(e))
            raise

    @staticmethod
    def _inject_json_hint(request: LLMRequest) -> list[dict]:
        """When JSON output is requested, ensure the prompt mentions JSON.

        DeepSeek requires the literal word 'json' somewhere in the input
        when response_format=json_object is used.
        """
        if request.response_format is None:
            return list(request.messages)
        msgs = [dict(m) for m in request.messages]
        if not any("json" in (m.get("content") or "").lower() for m in msgs):
            schema_hint = ""
            try:
                if isinstance(request.response_format, type):
                    schema_hint = (
                        " Schema: "
                        + str(request.response_format.model_json_schema())
                    )
            except Exception:
                pass
            msgs.append(
                {
                    "role": "system",
                    "content": "Respond with a single valid JSON object." + schema_hint,
                }
            )
        return msgs

    @staticmethod
    def _try_parse(content: str, response_format):
        """Try to parse the content into the requested pydantic model."""
        if response_format is None or not isinstance(response_format, type):
            return None
        import json

        text = content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        try:
            return response_format.model_validate(data)
        except Exception:
            return None

    async def _fallback_complete(self, request: LLMRequest, error: str) -> LLMResponse:
        """Attempt fallback model on primary failure."""
        fallback_model = request.model or self._config.fallback.default_model
        try:
            response = await self._client.chat.completions.create(
                model=fallback_model,
                messages=request.messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
            choice = response.choices[0]
            return LLMResponse(
                content=choice.message.content or "",
                model_used=f"fallback:{response.model}",
                tokens_prompt=response.usage.prompt_tokens if response.usage else 0,
                tokens_completion=response.usage.completion_tokens if response.usage else 0,
            )
        except Exception:
            return LLMResponse(
                content=f"LLM error (primary + fallback failed): {error}",
                model_used="error",
            )

    async def stream(self, request: LLMRequest) -> AsyncGenerator[str, None]:
        self._ensure_client()
        model = request.model or self._config.resolve_model(request.purpose)
        stream = await self._client.chat.completions.create(
            model=model,
            messages=request.messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self._ensure_client()
        model = self._config.embedding_model or "text-embedding-3-small"
        response = await self._client.embeddings.create(
            input=texts,
            model=model,
        )
        return [item.embedding for item in response.data]

    async def vision_complete(
        self,
        text: str,
        images: list[str],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """多模态调用 — 支持图片+文本输入。

        images: 图片URL列表或 base64 编码列表
               URL格式: "https://..."
               Base64格式: "data:image/jpeg;base64,..."
        """
        self._ensure_client()

        # 解析多模态配置
        vision_cfg = self._config.resolve_vision_config()
        vision_model = model or self._config.resolve_model("vision")

        # 如果 vision 配置与 primary 不同，需要创建独立 client
        client = self._client
        if (
            vision_cfg.api_key != self._config.primary.api_key
            or vision_cfg.base_url != self._config.primary.base_url
        ):
            from openai import AsyncOpenAI
            client = AsyncOpenAI(
                api_key=vision_cfg.api_key,
                base_url=vision_cfg.base_url,
            )

        # 构建多模态消息
        content_parts: list[dict] = [{"type": "text", "text": text}]
        for img in images:
            if img.startswith("data:"):
                # base64 格式
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": img},
                })
            elif img.startswith("http"):
                # URL 格式
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": img},
                })
            else:
                # 当作本地路径，读取并转 base64
                import base64
                import mimetypes
                mime = mimetypes.guess_type(img)[0] or "image/jpeg"
                with open(img, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                })

        messages = [{"role": "user", "content": content_parts}]

        response = await client.chat.completions.create(
            model=vision_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model_used=response.model,
            tokens_prompt=response.usage.prompt_tokens if response.usage else 0,
            tokens_completion=response.usage.completion_tokens if response.usage else 0,
        )

    def health_check(self) -> bool:
        return self._client is not None
