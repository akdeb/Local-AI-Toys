"""Model configuration and hot-swap endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import db_service
from utils import LLM, STT, TTS, QWEN3_TTS, normalize_tts_backend

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/models")
async def get_models(request: Request):
    pipeline = getattr(request.app.state, "pipeline", None)
    tts_backend = normalize_tts_backend(
        getattr(pipeline, "tts_backend", None)
        or db_service.db_service.get_setting("tts_backend")
        or "qwen3-tts"
    )
    tts_repo = None
    if pipeline and getattr(pipeline, "tts", None):
        tts_repo = getattr(pipeline.tts, "model_id", None)
    return {
        "llm": {
            "backend": "mlx",
            "repo": db_service.db_service.get_setting("llm_model") or LLM,
            "file": None,
            "context_window": 4096,
            "loaded": pipeline is not None and pipeline.llm is not None,
        },
        "tts": {
            "backend": tts_backend,
            "backbone_repo": tts_repo,
            "codec_repo": None,
            "loaded": pipeline is not None and pipeline.tts is not None,
        },
        "stt": {
            "backend": "whisper",
            "repo": STT,
            "loaded": pipeline is not None and pipeline.stt is not None,
        },
    }


class ModelsUpdate(BaseModel):
    model_repo: Optional[str] = None


@router.put("/models")
async def set_models(request: Request, body: ModelsUpdate):
    if body.model_repo:
        db_service.db_service.set_setting("llm_model", body.model_repo)
    return await get_models(request)


class ModelSwitchRequest(BaseModel):
    model_repo: str


@router.post("/models/switch")
async def switch_model(request: Request, body: ModelSwitchRequest):
    """Download a new LLM and hot-swap it. Returns newline-delimited JSON progress."""
    import mlx.core as mx

    pipeline = getattr(request.app.state, "pipeline", None)
    model_repo = body.model_repo.strip()
    if not model_repo:
        raise HTTPException(status_code=400, detail="model_repo is required")

    async def generate_progress():
        try:
            yield json.dumps({"stage": "downloading", "progress": 0.0, "message": f"Starting download of {model_repo}..."}) + "\n"

            from huggingface_hub import HfApi, snapshot_download
            from huggingface_hub.constants import HF_HUB_CACHE
            import threading

            download_complete = threading.Event()
            download_error = [None]
            download_path = [None]
            start_time = [asyncio.get_event_loop().time()]
            expected_total_bytes = [None]
            baseline_bytes = [0]
            last_bytes = [0]
            last_change_monotonic = [time.monotonic()]

            def _repo_cache_dir() -> str:
                return os.path.join(str(HF_HUB_CACHE), f"models--{model_repo.replace('/', '--')}")

            def _cache_bytes(base_dir: str) -> int:
                total = 0
                for sub in ("blobs", "snapshots"):
                    d = os.path.join(base_dir, sub)
                    if not os.path.isdir(d):
                        continue
                    for root, _, files in os.walk(d):
                        for fn in files:
                            try:
                                total += os.stat(os.path.join(root, fn)).st_size
                            except Exception:
                                pass
                return total

            def _xet_cache_bytes() -> int:
                try:
                    hub_cache = str(HF_HUB_CACHE)
                    root = os.path.dirname(hub_cache)
                    candidates = [os.path.join(root, "xet"), os.path.join(root, "xet-cache")]
                    for env_key in ("HF_XET_CACHE", "XET_CACHE_DIR", "XET_HOME"):
                        v = os.environ.get(env_key)
                        if v and v.strip():
                            candidates.insert(0, v.strip())
                    total = 0
                    for d in candidates:
                        if not d or not os.path.isdir(d):
                            continue
                        for rd, _, files in os.walk(d):
                            for fn in files:
                                try:
                                    total += os.stat(os.path.join(rd, fn)).st_size
                                except Exception:
                                    pass
                    return total
                except Exception:
                    return 0

            def _total_cache_bytes() -> int:
                return _cache_bytes(_repo_cache_dir()) + _xet_cache_bytes()

            def _compute_expected_total() -> int | None:
                try:
                    info = HfApi().model_info(model_repo, files_metadata=True)
                    total = sum(
                        getattr(s, "size", 0) or 0
                        for s in (getattr(info, "siblings", None) or [])
                        if isinstance(getattr(s, "size", None), int)
                    )
                    return total or None
                except Exception:
                    return None

            def download_model():
                try:
                    os.environ["HF_HUB_DISABLE_XET"] = "1"
                    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
                    os.environ["HF_XET_DISABLE"] = "1"
                    os.environ["HF_HUB_DISABLE_HF_XET"] = "1"
                    expected_total_bytes[0] = _compute_expected_total()
                    download_path[0] = snapshot_download(
                        repo_id=model_repo, local_files_only=False,
                        resume_download=True, max_workers=4,
                    )
                except Exception as e:
                    download_error[0] = str(e)
                finally:
                    download_complete.set()

            download_thread = threading.Thread(target=download_model)
            download_thread.start()

            stall_seconds = 300
            baseline_bytes[0] = _total_cache_bytes()
            while not download_complete.is_set():
                await asyncio.sleep(1.0)
                elapsed = asyncio.get_event_loop().time() - start_time[0]
                current_bytes = max(0, _total_cache_bytes() - baseline_bytes[0])
                if current_bytes != last_bytes[0]:
                    last_bytes[0] = current_bytes
                    last_change_monotonic[0] = time.monotonic()
                elif time.monotonic() - last_change_monotonic[0] > stall_seconds:
                    yield json.dumps({"stage": "error", "error": "Download stalled for 5 minutes. Retry."}) + "\n"
                    return

                if isinstance(expected_total_bytes[0], int) and expected_total_bytes[0] > 0:
                    progress = min(0.99, current_bytes / expected_total_bytes[0])
                else:
                    progress = min(0.95, 1.0 - (1.0 / (1.0 + elapsed / 10.0)))

                gb = current_bytes / (1024 ** 3)
                if elapsed > 30:
                    mins, secs = int(elapsed // 60), int(elapsed % 60)
                    time_str = f"{mins}m {secs}s" if mins else f"{secs}s"
                    if isinstance(expected_total_bytes[0], int) and expected_total_bytes[0] > 0:
                        msg = f"Downloading {model_repo}... ({gb:.2f}/{expected_total_bytes[0]/(1024**3):.2f} GB, {time_str})"
                    else:
                        msg = f"Downloading {model_repo}... ({gb:.2f} GB, {time_str})"
                else:
                    msg = f"Downloading {model_repo}... ({gb:.2f} GB)"
                yield json.dumps({"stage": "downloading", "progress": progress, "message": msg}) + "\n"

            download_thread.join()
            if download_error[0]:
                yield json.dumps({"stage": "error", "error": f"Download failed: {download_error[0]}"}) + "\n"
                return

            yield json.dumps({"stage": "downloading", "progress": 1.0, "message": "Download complete!"}) + "\n"
            yield json.dumps({"stage": "loading", "progress": 0.0, "message": "Loading model weights..."}) + "\n"

            try:
                if not pipeline:
                    raise RuntimeError("Pipeline not initialized")
                new_llm, new_tokenizer, new_backend = await pipeline.load_llm_backend(model_repo)
                yield json.dumps({"stage": "loading", "progress": 0.5, "message": "Model loaded, swapping..."}) + "\n"

                async with pipeline.llm_lock:
                    old_llm, old_tok = pipeline.llm, pipeline.tokenizer
                    pipeline.llm = new_llm
                    pipeline.tokenizer = new_tokenizer
                    pipeline.llm_model = model_repo
                    pipeline.llm_backend = new_backend
                    del old_llm, old_tok
                    mx.metal.clear_cache()

                db_service.db_service.set_setting("llm_model", model_repo)
                yield json.dumps({"stage": "loading", "progress": 1.0, "message": "Model weights loaded!"}) + "\n"
                yield json.dumps({"stage": "complete", "progress": 1.0, "message": f"Switched to {model_repo}"}) + "\n"
            except Exception as e:
                logger.error(f"Failed to load model: {e}")
                yield json.dumps({"stage": "error", "error": f"Failed to load model: {e}"}) + "\n"
        except Exception as e:
            logger.error(f"Model switch failed: {e}")
            yield json.dumps({"stage": "error", "error": str(e)}) + "\n"

    return StreamingResponse(
        generate_progress(), media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class TtsSwitchRequest(BaseModel):
    tts_backend: str


@router.post("/models/switch-tts")
async def switch_tts_model(request: Request, body: TtsSwitchRequest):
    """Download TTS weights and hot-swap the TTS backend."""
    pipeline = getattr(request.app.state, "pipeline", None)
    normalized_backend = normalize_tts_backend(body.tts_backend)
    target_repo = QWEN3_TTS if normalized_backend == "qwen3-tts" else TTS

    async def generate_progress():
        try:
            yield json.dumps({"stage": "downloading", "progress": 0.0, "message": f"Preparing {normalized_backend}..."}) + "\n"
            from huggingface_hub import snapshot_download

            try:
                snapshot_download(repo_id=target_repo, local_files_only=False, resume_download=True, max_workers=4)
            except Exception as e:
                yield json.dumps({"stage": "error", "error": f"Download failed: {e}"}) + "\n"
                return

            yield json.dumps({"stage": "downloading", "progress": 1.0, "message": "Download complete!"}) + "\n"
            yield json.dumps({"stage": "loading", "progress": 0.0, "message": "Loading TTS weights..."}) + "\n"

            if not pipeline:
                yield json.dumps({"stage": "error", "error": "Pipeline not initialized"}) + "\n"
                return
            try:
                await pipeline.set_tts_backend(normalized_backend)
                db_service.db_service.set_setting("tts_backend", normalized_backend)
                request.app.state.tts_backend = normalized_backend
            except Exception as e:
                logger.error(f"Failed to switch TTS backend: {e}")
                yield json.dumps({"stage": "error", "error": f"Failed to switch TTS backend: {e}"}) + "\n"
                return

            yield json.dumps({"stage": "loading", "progress": 1.0, "message": "TTS weights loaded!"}) + "\n"
            yield json.dumps({"stage": "complete", "progress": 1.0, "message": f"Switched to {normalized_backend}"}) + "\n"
        except Exception as e:
            logger.error(f"TTS switch failed: {e}")
            yield json.dumps({"stage": "error", "error": str(e)}) + "\n"

    return StreamingResponse(
        generate_progress(), media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
