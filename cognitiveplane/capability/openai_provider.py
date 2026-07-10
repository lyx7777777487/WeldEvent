"""OpenAI-compatible LLM Provider implementation.

Source: L1-Interaction-Layer-Business-Requirements.md §3.6.2, §3.6.9.

Requires: `openai` package (pip install openai)
Fallback: raises RuntimeError if openai is not installed.
"""

import json
import logging
import re
from collections.abc import AsyncGenerator

import httpx

from cognitiveplane.adapters.observability.tracing import observe
from cognitiveplane.capability.config import LLMConfig
from cognitiveplane.capability.provider import LLMProvider, LLMRequest, LLMResponse
from cognitiveplane.capability.response_cache import LLMResponseCache

logger = logging.getLogger("llm_provider")


class OpenAIProvider(LLMProvider):
    """Real OpenAI API provider with fallback support.

    Features:
    - Per-purpose model routing via LLMConfig.resolve_model()
    - Automatic fallback to secondary model on failure
    - Timeout enforcement per purpose
    - Token and cost tracking
    """

    def __init__(
        self,
        config: LLMConfig,
        cache: LLMResponseCache | None = None,
    ) -> None:
        self._config = config
        self._client = None
        # LLM 响应缓存（可选）— 默认 None 时所有请求直通 LLM API。
        # 启用后只缓存 temperature==0 + 无 tool_calls 的响应。
        self._cache = cache

    def _ensure_client(self):
        if self._client is not None:
            return
        try:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self._config.primary.api_key,
                base_url=self._config.primary.base_url,
                organization=self._config.primary.organization,
                timeout=30.0,
                max_retries=1,
                http_client=httpx.AsyncClient(trust_env=False),
            )
        except ImportError:
            raise RuntimeError(
                "openai package not installed. "
                "Install with: pip install openai"
            )

    @observe(name="llm.complete", as_type="generation", capture_input=False)
    async def complete(self, request: LLMRequest) -> LLMResponse:
        # 缓存命中检查（仅 temperature==0 的请求）
        cache_key = None
        if self._cache is not None:
            cache_key = self._cache.get_or_compute_key(request)
            if cache_key is not None:
                cached = self._cache.get(cache_key)
                if cached is not None:
                    return cached

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
            # thinking 模式（GLM-4.6 / DeepSeek-R1 / Qwen3 等）返回的推理内容。
            # 多轮回传时必须带 reasoning_content，否则 API 校验报错。
            reasoning_content = getattr(choice.message, "reasoning_content", None)

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
            # Fallback: DeepSeek 有时将工具调用以 XML 格式嵌入 content，
            # 而非通过原生 function calling 返回 tool_calls。
            # 格式: <｜｜DSML｜｜tool_calls> ... </｜｜DSML｜｜tool_calls>
            # 内部: <｜｜DSML｜｜invoke name="tool_name"> ... </｜｜DSML｜｜invoke>
            # 参数: <｜｜DSML｜｜parameter name="param_name">value</｜｜DSML｜｜parameter>
            elif (not tool_calls) and content and '<｜｜DSML｜｜' in content:
                tool_calls = []
                # Extract entire tool_calls block
                m = re.search(r'<｜｜DSML｜｜tool_calls>\s*(.*?)\s*</｜｜DSML｜｜tool_calls>', content, re.DOTALL)
                if m:
                    block = m.group(1)
                    # Extract each invoke tag
                    invoke_matches = re.finditer(
                        r'<｜｜DSML｜｜invoke\s+name=\"([^\"]+)\"[^>]*>(.*?)</｜｜DSML｜｜invoke>',
                        block,
                        re.DOTALL
                    )
                    call_id = 1
                    for inv in invoke_matches:
                        tool_name = inv.group(1)
                        params_str = inv.group(2).strip()
                        # Extract parameters
                        params = {}
                        param_matches = re.finditer(
                            r'<｜｜DSML｜｜parameter\s+name=\"([^\"]+)\"[^>]*>(.*?)</｜｜DSML｜｜parameter>',
                            params_str,
                            re.DOTALL
                        )
                        for pm in param_matches:
                            pname = pm.group(1)
                            pval = pm.group(2).strip()
                            # Try JSON parse if looks like JSON, else keep as string
                            try:
                                if pval.startswith('{') or pval.startswith('['):
                                    pval = json.loads(pval)
                            except json.JSONDecodeError:
                                logger.warning("stream chunk parse failed, skipped", exc_info=True)
                            params[pname] = pval
                        tool_calls.append({
                            "id": f"xml-fb-{call_id}",
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(params),
                            },
                        })
                        call_id += 1
                    # If we parsed at least one tool_call,
                    # keep tool_calls and clear content from XML markup
                    # so it doesn't show up in final reply.
                    if tool_calls:
                        # Remove XML tags from content but keep preceding text
                        content = re.sub(
                            r'<｜｜DSML｜｜tool_calls>.*</｜｜DSML｜｜tool_calls>',
                            '',
                            content,
                            flags=re.DOTALL
                        ).strip()
            parsed = self._try_parse(content, request.response_format)
            result = LLMResponse(
                content=content,
                model_used=response.model,
                tokens_prompt=response.usage.prompt_tokens if response.usage else 0,
                tokens_completion=response.usage.completion_tokens if response.usage else 0,
                parsed_object=parsed,
                tool_calls=tool_calls,
                reasoning_content=reasoning_content,
            )
            # 写入缓存（仅当请求可缓存 + 响应无 tool_calls + 有 cache 实例）
            if (
                cache_key is not None
                and self._cache is not None
                and self._cache.is_cacheable_response(result)
            ):
                self._cache.set(cache_key, result)
            return result
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
                logger.warning("stream chunk parse failed, skipped", exc_info=True)
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
                reasoning_content=getattr(choice.message, "reasoning_content", None),
            )
        except Exception:
            return LLMResponse(
                content=f"LLM error (primary + fallback failed): {error}",
                model_used="error",
            )

    @observe(name="llm.stream", as_type="generation", capture_input=False)
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

        logger.info(
            "[vision] using model=%s base_url=%s same_client=%s",
            vision_model,
            vision_cfg.base_url,
            vision_cfg.api_key == self._config.primary.api_key and vision_cfg.base_url == self._config.primary.base_url,
        )

        # 如果 vision 配置与 primary 不同，需要创建独立 client
        client = self._client
        if (
            vision_cfg.api_key != self._config.primary.api_key
            or vision_cfg.base_url != self._config.primary.base_url
        ):
            from openai import AsyncOpenAI
            logger.info("[vision] creating separate client for Volc/Doubao")
            client = AsyncOpenAI(
                api_key=vision_cfg.api_key,
                base_url=vision_cfg.base_url,
                timeout=60.0,
                max_retries=0,
                http_client=httpx.AsyncClient(trust_env=False),
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

        import time
        t0 = time.monotonic()
        logger.info("[vision] sending multimodal request (images=%d, text_len=%d)...", len(images), len(text))
        try:
            # 火山 doubao-seed-1-6-vision 默认开启 thinking 模式（reasoning_tokens 占 80%+），
            # 单图响应 40s+ 易触发超时。焊缝质检是确定性任务，不需要长链推理，
            # 通过 extra_body 关闭 thinking，响应时间降至 10s 内。
            # 兼容非火山模型：extra_body 仅 ARK 平台识别，OpenAI/DeepSeek 会忽略。
            response = await client.chat.completions.create(
                model=vision_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body={"thinking": {"type": "disabled"}},
            )
            elapsed = time.monotonic() - t0
            choice = response.choices[0]
            logger.info("[vision] response in %.1fs model=%s tokens=%s", elapsed, response.model, response.usage)
            return LLMResponse(
                content=choice.message.content or "",
                model_used=response.model,
                tokens_prompt=response.usage.prompt_tokens if response.usage else 0,
                tokens_completion=response.usage.completion_tokens if response.usage else 0,
            )
        except Exception as e:
            elapsed = time.monotonic() - t0
            logger.error("[vision] FAILED in %.1fs: %s: %s", elapsed, type(e).__name__, e)
            raise

    def health_check(self) -> bool:
        return self._client is not None
