import asyncio
import json
import os
import re
from pathlib import Path
from typing import Dict

import mlx.core as mx
import numpy as np
from mlx_lm import generate as mx_generate
from mlx_lm import stream_generate as mx_stream_generate
from mlx_lm.utils import load as load_llm
from mlx_audio.stt import load as load_stt

from tts import ChatterboxTTS, Qwen3TTS
from utils import STT, LLM, TTS, QWEN3_TTS

try:
    from mlx_vlm import generate as mx_vlm_generate
    from mlx_vlm import load as load_vlm
    from mlx_vlm import stream_generate as mx_vlm_stream_generate
except Exception:
    mx_vlm_generate = None
    load_vlm = None
    mx_vlm_stream_generate = None

LLM_PROFILE_CACHE: Dict[str, Dict[str, object]] = {}


def _load_llm_profiles() -> Dict[str, Dict[str, object]]:
    if LLM_PROFILE_CACHE:
        return LLM_PROFILE_CACHE
    repo_root = Path(__file__).resolve().parents[3]
    llms_path = repo_root / "app" / "src" / "assets" / "llms.json"
    if not llms_path.exists():
        return {}
    try:
        data = json.loads(llms_path.read_text(encoding="utf-8"))
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict) and isinstance(item.get("repo_id"), str):
                LLM_PROFILE_CACHE[item["repo_id"]] = item
    except Exception:
        return {}
    return LLM_PROFILE_CACHE


def _is_vision_model(repo_id: str) -> bool:
    profile = _load_llm_profiles().get(repo_id)
    return bool(profile and profile.get("vision"))


def _env_flag(name: str) -> bool:
    value = str(os.environ.get(name, "")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _env_flag_with_default_true(name: str) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return True
    return _env_flag(name)


def _strip_thinking(text: str) -> str:
    if not text:
        return text
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"</?think>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()

def _strip_thinking_keep_ws(text: str) -> str:
    if not text:
        return text
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"</?think>", "", cleaned, flags=re.IGNORECASE)
    return cleaned


def _is_unsupported_thinking_kw_error(exc: Exception) -> bool:
    msg = str(exc)
    return (
        "unexpected keyword argument 'enable_thinking'" in msg
        or 'unexpected keyword argument "enable_thinking"' in msg
        or "unexpected keyword argument 'thinking_budget'" in msg
        or 'unexpected keyword argument "thinking_budget"' in msg
    )


class VoicePipeline:
    def __init__(
        self,
        silence_threshold=0.03,
        silence_duration=1.5,
        input_sample_rate=16_000,
        output_sample_rate=24_000,
        streaming_interval=3,
        frame_duration_ms=30,
        stt_model=STT,
        llm_model=LLM,
        tts_ref_audio: str | None = None,
        tts_backend: str = "qwen3-tts",
    ):
        self.silence_threshold = silence_threshold
        self.silence_duration = silence_duration
        self.input_sample_rate = input_sample_rate
        self.output_sample_rate = output_sample_rate
        self.streaming_interval = streaming_interval
        self.frame_duration_ms = frame_duration_ms

        self.stt_model_id = stt_model
        self.llm_model = llm_model
        self.tts_ref_audio = tts_ref_audio
        self.tts_backend = tts_backend
        self.llm_backend = "lm"
        self.llm = None
        self.tokenizer = None
        self.disable_thinking = _env_flag_with_default_true("MLX_DISABLE_THINKING")

        self.llm_lock = asyncio.Lock()
        self.stt_lock = asyncio.Lock()
        self.tts_lock = asyncio.Lock()

    async def init_models(self):
        self.llm, self.tokenizer, self.llm_backend = await self.load_llm_backend(
            self.llm_model
        )
        self.stt = await asyncio.to_thread(lambda: load_stt(self.stt_model_id, strict=False))
        await self._init_tts()

    def _load_llm_backend_sync(self, model_repo: str):
        if _is_vision_model(model_repo):
            if load_vlm is None:
                raise RuntimeError(
                    "Model is marked vision=true in llms.json but mlx-vlm is unavailable. "
                    "Install/update mlx-vlm in the backend environment."
                )

            # Some VLM repos need trust_remote_code; try both paths for compatibility.
            trust_remote_code = _env_flag("MLX_TRUST_REMOTE_CODE")
            vlm_errors: list[str] = []
            for trc in [trust_remote_code, True]:
                try:
                    llm, tokenizer = load_vlm(model_repo, trust_remote_code=trc)
                    return llm, tokenizer, "vlm"
                except TypeError:
                    # Older mlx-vlm versions may not accept trust_remote_code.
                    try:
                        llm, tokenizer = load_vlm(model_repo)
                        return llm, tokenizer, "vlm"
                    except Exception as e:
                        vlm_errors.append(str(e))
                except Exception as e:
                    vlm_errors.append(str(e))

            # If model is marked vision but behaves like text-only, allow fallback.
            try:
                llm, tokenizer = load_llm(model_repo)
                return llm, tokenizer, "lm"
            except Exception as lm_error:
                combined = "; ".join([err for err in vlm_errors if err]) or "unknown error"
                raise RuntimeError(
                    f"Failed to load vision model with mlx-vlm ({combined}) "
                    f"and fallback mlx-lm also failed ({lm_error})."
                ) from lm_error

        llm, tokenizer = load_llm(model_repo)
        return llm, tokenizer, "lm"

    async def load_llm_backend(self, model_repo: str):
        return await asyncio.to_thread(lambda: self._load_llm_backend_sync(model_repo))

    async def _init_tts(self):
        backend = self._normalize_tts_backend(self.tts_backend)
        self.tts_backend = backend

        if backend == "qwen3-tts":
            self.tts = Qwen3TTS(
                model_id=QWEN3_TTS,
                output_sample_rate=self.output_sample_rate,
                stream=True,
                streaming_interval=self.streaming_interval,
            )
        else:
            self.tts = ChatterboxTTS(
                model_id=TTS,
                output_sample_rate=self.output_sample_rate,
                stream=True,
                streaming_interval=self.streaming_interval,
            )

        await asyncio.to_thread(self.tts.load)

    @staticmethod
    def _normalize_tts_backend(backend: str | None) -> str:
        value = (backend or "").strip().lower()
        if value in {"", "qwen3-tts", "qwen3_tts", "qwen3"}:
            return "qwen3-tts"
        if value in {"chatterbox", "chatterbox-turbo", "chatterbox_turbo"}:
            return "chatterbox-turbo"
        raise ValueError("tts_backend must be 'chatterbox-turbo' or 'qwen3-tts'")

    async def set_tts_backend(self, backend: str) -> str:
        backend = self._normalize_tts_backend(backend)

        async with self.tts_lock:
            self.tts_backend = backend
            await self._init_tts()
        return backend

    def _messages_to_plain_prompt(
        self, messages, add_generation_prompt: bool = True
    ) -> str:
        lines = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "user")).strip() or "user"
            content = message.get("content", "")
            if isinstance(content, list):
                text_parts = [
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ]
                content = " ".join(p for p in text_parts if p)
            lines.append(f"{role}: {str(content).strip()}")
        if add_generation_prompt:
            lines.append("assistant:")
        return "\n".join(lines).strip()

    def _apply_chat_template(self, messages, add_generation_prompt: bool, clear_thinking: bool | None):
        if not hasattr(self.tokenizer, "apply_chat_template"):
            return self._messages_to_plain_prompt(
                messages, add_generation_prompt=add_generation_prompt
            )
        base_kwargs = dict(tokenize=False, add_generation_prompt=add_generation_prompt)

        # Hard-disable thinking by default (can be overridden with MLX_DISABLE_THINKING=0).
        if self.disable_thinking:
            for thinking_kwargs in (
                {"enable_thinking": False, "thinking_budget": 0},
                {"enable_thinking": False},
                {"clear_thinking": True},
            ):
                try:
                    return self.tokenizer.apply_chat_template(
                        messages, **base_kwargs, **thinking_kwargs
                    )
                except TypeError:
                    continue

        if clear_thinking is not None:
            try:
                return self.tokenizer.apply_chat_template(
                    messages,
                    **base_kwargs,
                    clear_thinking=clear_thinking,
                )
            except TypeError:
                pass

        return self.tokenizer.apply_chat_template(messages, **base_kwargs)

    def _generate(self, prompt: str, max_tokens: int):
        if self.llm_backend == "vlm":
            if mx_vlm_generate is None:
                raise RuntimeError("mlx-vlm generate is unavailable")
            try:
                response = mx_vlm_generate(
                    self.llm,
                    self.tokenizer,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    verbose=False,
                    enable_thinking=False,
                )
            except TypeError:
                response = mx_vlm_generate(
                    self.llm,
                    self.tokenizer,
                    prompt,
                    max_tokens=max_tokens,
                    verbose=False,
                    enable_thinking=False,
                )
            if isinstance(response, tuple):
                response = response[0]
            return _strip_thinking(str(response).strip())

        lm_kwargs = dict(
            prompt=prompt,
            max_tokens=max_tokens,
            verbose=False,
        )
        if self.disable_thinking:
            lm_kwargs["enable_thinking"] = False

        try:
            response = mx_generate(
                self.llm,
                self.tokenizer,
                **lm_kwargs,
            )
        except TypeError:
            # Older mlx-lm versions may not support enable_thinking.
            lm_kwargs.pop("enable_thinking", None)
            response = mx_generate(
                self.llm,
                self.tokenizer,
                **lm_kwargs,
            )
        return _strip_thinking(response.strip())

    def _stream_generate_sync(self, prompt: str, max_tokens: int):
        if self.llm_backend == "vlm":
            if mx_vlm_stream_generate is None:
                raise RuntimeError("mlx-vlm stream_generate is unavailable")
            primary_kwargs = dict(
                prompt=prompt,
                max_tokens=max_tokens,
                enable_thinking=False,
                thinking_budget=0 if self.disable_thinking else None,
            )
            fallback_kwargs = dict(prompt=prompt, max_tokens=max_tokens)
            stream_creator = lambda kwargs: mx_vlm_stream_generate(
                self.llm,
                self.tokenizer,
                **kwargs,
            )
        else:
            primary_kwargs = dict(
                prompt=prompt,
                max_tokens=max_tokens,
            )
            if self.disable_thinking:
                primary_kwargs["enable_thinking"] = False
            fallback_kwargs = dict(prompt=prompt, max_tokens=max_tokens)
            stream_creator = lambda kwargs: mx_stream_generate(
                self.llm,
                self.tokenizer,
                **kwargs,
            )

        raw_accum = ""
        clean_accum = ""
        tried_fallback = False
        while True:
            try:
                stream = stream_creator(primary_kwargs if not tried_fallback else fallback_kwargs)
                for item in stream:
                    segment = getattr(item, "text", "")
                    if not segment:
                        continue
                    raw_accum += str(segment)
                    cleaned = _strip_thinking_keep_ws(raw_accum)
                    if cleaned.startswith(clean_accum):
                        delta = cleaned[len(clean_accum) :]
                    else:
                        delta = cleaned
                    clean_accum = cleaned
                    if delta:
                        yield delta
                break
            except TypeError as e:
                if tried_fallback or not _is_unsupported_thinking_kw_error(e):
                    raise
                tried_fallback = True

    async def generate_text_simple(
        self,
        prompt: str,
        max_tokens=100,
        clear_thinking: bool | None = None,
    ) -> str:
        if not self.llm or not self.tokenizer:
            raise RuntimeError("LLM not initialized")

        messages = [{"role": "user", "content": prompt}]
        formatted_prompt = self._apply_chat_template(
            messages, add_generation_prompt=True, clear_thinking=clear_thinking
        )

        async with self.llm_lock:
            response = await asyncio.to_thread(
                lambda: self._generate(formatted_prompt, max_tokens)
            )
        return response.strip()

    async def transcribe(self, audio_bytes: bytes) -> str:
        audio = (
            np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        )
        async with self.stt_lock:
            result = await asyncio.to_thread(self.stt.generate, mx.array(audio))
        return result.text.strip()

    async def generate_response(
        self,
        text: str,
        system_prompt: str = None,
        messages=None,
        max_tokens: int = 512,
        clear_thinking: bool | None = None,
    ) -> str:
        if messages is None:
            sys_content = system_prompt or (
                "{%- set enable_thinking = false %}"
                "You are a helpful voice assistant. You always respond with short "
                "sentences and never use punctuation like parentheses or colons "
                "that wouldn't appear in conversational speech."
            )
            messages = [
                {"role": "system", "content": sys_content},
                {"role": "user", "content": text},
            ]

        prompt = self._apply_chat_template(
            messages, add_generation_prompt=True, clear_thinking=clear_thinking
        )

        async with self.llm_lock:
            response = await asyncio.to_thread(
                lambda: self._generate(prompt, max_tokens)
            )
        return response.strip()

    async def stream_response(
        self,
        text: str,
        system_prompt: str = None,
        messages=None,
        max_tokens: int = 512,
        clear_thinking: bool | None = None,
        cancel_event: asyncio.Event | None = None,
    ):
        if messages is None:
            sys_content = system_prompt or (
                "{%- set enable_thinking = false %}"
                "You are a helpful voice assistant. You always respond with short "
                "sentences and never use punctuation like parentheses or colons "
                "that wouldn't appear in conversational speech."
            )
            messages = [
                {"role": "system", "content": sys_content},
                {"role": "user", "content": text},
            ]

        prompt = self._apply_chat_template(
            messages, add_generation_prompt=True, clear_thinking=clear_thinking
        )

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        stream_error: list[Exception] = []

        def _run_stream():
            try:
                for delta in self._stream_generate_sync(prompt, max_tokens):
                    if cancel_event and cancel_event.is_set():
                        break
                    loop.call_soon_threadsafe(queue.put_nowait, delta)
            except Exception as e:
                stream_error.append(e)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        async with self.llm_lock:
            task = asyncio.create_task(asyncio.to_thread(_run_stream))
            try:
                while True:
                    chunk = await queue.get()
                    if chunk is None:
                        break
                    if cancel_event and cancel_event.is_set():
                        break
                    yield chunk
            finally:
                await task
                if stream_error:
                    raise RuntimeError(f"LLM streaming failed: {stream_error[0]}")

    async def synthesize_speech(
        self,
        text: str,
        cancel_event: asyncio.Event = None,
        ref_audio_path: str | None = None,
        ref_text: str | None = None,
    ):
        audio_queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        stream_error: list[Exception] = []

        def _tts_stream():
            try:
                for audio_bytes in self.tts.generate(
                    text, ref_audio_path=ref_audio_path, ref_text=ref_text
                ):
                    if cancel_event and cancel_event.is_set():
                        break
                    loop.call_soon_threadsafe(audio_queue.put_nowait, audio_bytes)
            except Exception as e:
                stream_error.append(e)
            finally:
                loop.call_soon_threadsafe(audio_queue.put_nowait, None)

        async with self.tts_lock:
            tts_task = asyncio.create_task(asyncio.to_thread(_tts_stream))
            try:
                while True:
                    chunk = await audio_queue.get()
                    if chunk is None:
                        break
                    if cancel_event and cancel_event.is_set():
                        break
                    yield chunk
            finally:
                await tts_task
                if stream_error:
                    raise RuntimeError(f"TTS generation failed: {stream_error[0]}")
