# main.py
import argparse
import asyncio
import base64
import json
import logging
import os
import socket
import time
import sys
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Dict, List, Optional
import re
import urllib.request
import urllib.error
import urllib.parse

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")
os.environ.setdefault("HF_XET_DISABLE", "1")
os.environ.setdefault("HF_HUB_DISABLE_HF_XET", "1")

from engine.characters import build_llm_messages, build_runtime_context, build_system_prompt

import mlx.core as mx
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import StreamingResponse
from starlette.websockets import WebSocketState
import db_service  # DB ops exposed via HTTP endpoints
from fastapi.middleware.cors import CORSMiddleware
import utils
from utils import STT, LLM, TTS, create_opus_packetizer
from services import (
    ConnectionManager,
    MdnsService,
    VoicePipeline,
    firmware_bin_path,
    get_local_ip,
    list_serial_ports,
    resolve_voice_ref_audio_path,
    resolve_voice_ref_text,
    run_firmware_flash,
    sanitize_spoken_text,
)

# Client type constants
CLIENT_TYPE_DESKTOP = "desktop"
CLIENT_TYPE_ESP32 = "esp32"

# Bump this string when changing prompt/sanitization so logs prove which code is running.
SERVER_BUILD_MARKER = "sanitize_v1_paraling_v2"


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
logger.info(f"Server build marker: {SERVER_BUILD_MARKER}")

GAIN_DB = 7.0
CEILING = 0.89

pipeline: VoicePipeline = None
manager = ConnectionManager()
mdns_service = MdnsService()

LLM_PROFILE_CACHE: Dict[str, Dict[str, object]] = {}
DEVICE_WATCHERS: set[asyncio.Queue] = set()
ESP32_WS: Optional[WebSocket] = None
ESP32_SESSION_ID: Optional[str] = None


def _start_mdns_service(server_port: int) -> None:
    try:
        mdns_service.start(server_port)
    except Exception as exc:
        mdns_service.enabled = False
        try:
            mdns_service.current_ip = get_local_ip()
        except Exception:
            mdns_service.current_ip = None
        logger.warning("mDNS start failed: %s", exc)


def _load_llm_profiles() -> Dict[str, Dict[str, object]]:
    if LLM_PROFILE_CACHE:
        return LLM_PROFILE_CACHE
    repo_root = Path(__file__).resolve().parents[2]
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


def _is_thinking_model(repo_id: str) -> bool:
    profile = _load_llm_profiles().get(repo_id)
    return bool(profile and profile.get("thinking"))


def _normalize_tts_backend(backend: Optional[str]) -> str:
    value = (backend or "").strip().lower()
    if value in {"", "qwen3-tts", "qwen3_tts", "qwen3"}:
        return "qwen3-tts"
    if value in {"chatterbox", "chatterbox-turbo", "chatterbox_turbo"}:
        return "chatterbox-turbo"
    return "qwen3-tts"


def _strip_thinking(text: str) -> str:
    if not text:
        return text
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"</?think>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _push_device_event(payload: Dict[str, object]) -> None:
    if not DEVICE_WATCHERS:
        return
    for q in list(DEVICE_WATCHERS):
        try:
            q.put_nowait(payload)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipeline
    app.state.pipeline_ready = False
    udp_task = None
    
    # Start mDNS service advertisement (fire-and-forget)
    server_port = getattr(app.state, "server_port", 8000)
    asyncio.create_task(asyncio.to_thread(_start_mdns_service, server_port))

    async def broadcast_server():
        ip = get_local_ip()
        if ip.startswith("127."):
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        msg = f"ELATO_SERVER {ip} {server_port}".encode("utf-8")
        while True:
            try:
                sock.sendto(msg, ("255.255.255.255", 1900))
            except Exception:
                pass
            await asyncio.sleep(2)

    udp_task = asyncio.create_task(broadcast_server())
    
    # Initialize database (already handled by module import, but logging for clarity)
    logger.info("Database service active")

    # Sync latest global voices/personalities on startup (best-effort).
    try:
        db_service.db_service.sync_global_voices_and_personalities()
    except Exception as e:
        logger.warning(f"Global assets sync failed: {e}")

    # Set defaults if not already set (e.g. running via uvicorn directly)
    if not hasattr(app.state, "stt_model"):
        app.state.stt_model = STT
    if not hasattr(app.state, "llm_model"):
        app.state.llm_model = db_service.db_service.get_setting("llm_model") or LLM
    if not hasattr(app.state, "tts_backend"):
        stored_tts_backend = db_service.db_service.get_setting("tts_backend")
        if not stored_tts_backend:
            db_service.db_service.set_setting("tts_backend", "qwen3-tts")
        app.state.tts_backend = _normalize_tts_backend(
            stored_tts_backend or "qwen3-tts"
        )
    # if not hasattr(app.state, "tts_ref_audio"):
    #     app.state.tts_ref_audio = os.path.join(os.path.dirname(__file__), "tts", "santa.wav")
    if not hasattr(app.state, "silence_threshold"):
        app.state.silence_threshold = 0.03
    if not hasattr(app.state, "silence_duration"):
        app.state.silence_duration = 1.5
    if not hasattr(app.state, "streaming_interval"):
        app.state.streaming_interval = 3
    if not hasattr(app.state, "output_sample_rate"):
        app.state.output_sample_rate = 24_000
    
    safe_streaming_interval = float(app.state.streaming_interval)
    if safe_streaming_interval < 1.2:
        safe_streaming_interval = 1.2

    pipeline = VoicePipeline(
        stt_model=app.state.stt_model,
        llm_model=app.state.llm_model,
        # tts_ref_audio=app.state.tts_ref_audio,
        tts_ref_audio=None,
        tts_backend=app.state.tts_backend,
        silence_threshold=app.state.silence_threshold,
        silence_duration=app.state.silence_duration,
        streaming_interval=safe_streaming_interval,
        output_sample_rate=app.state.output_sample_rate,
    )
    await pipeline.init_models()
    logger.info("Voice pipeline initialized")
    app.state.pipeline_ready = True
    yield
    logger.info("Shutting down...")
    mdns_service.stop()
    if udp_task:
        udp_task.cancel()


app = FastAPI(title="Voice Pipeline WebSocket Server", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- HTTP Endpoints for Settings ---

from pydantic import BaseModel
from typing import Optional, Dict, Any

class SettingUpdate(BaseModel):
    value: Optional[str] = None

@app.get("/network-info")
async def network_info():
    real_ip = get_local_ip()
    return {
        "ip": real_ip,
        "advertising_ip": mdns_service.current_ip,
        "mdns_enabled": mdns_service.enabled
    }

@app.post("/restart-mdns")
async def restart_mdns():
    """Force restart mDNS service (useful after network change)."""
    server_port = getattr(app.state, "server_port", 8000)
    logger.info("Manual mDNS restart requested")
    mdns_service.stop()
    asyncio.create_task(asyncio.to_thread(_start_mdns_service, server_port))
    return {"status": "starting", "ip": mdns_service.current_ip}


@app.get("/events/device")
async def device_events():
    async def stream():
        q: asyncio.Queue = asyncio.Queue(maxsize=5)
        DEVICE_WATCHERS.add(q)
        try:
            status = db_service.db_service.get_device_status()
            yield f"data: {json.dumps(status)}\n\n"
            while True:
                data = await q.get()
                yield f"data: {json.dumps(data)}\n\n"
        finally:
            DEVICE_WATCHERS.discard(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/startup-status")
async def startup_status():
    voices_n = db_service.db_service.get_table_count("voices")
    personalities_n = db_service.db_service.get_table_count("personalities")
    seeded = bool(getattr(db_service.db_service, "seeded_ok", False)) and voices_n > 0 and personalities_n > 0
    pipeline_ready = bool(getattr(app.state, "pipeline_ready", False))
    return {
        "ready": bool(seeded and pipeline_ready),
        "seeded": bool(seeded),
        "pipeline_ready": bool(pipeline_ready),
        "counts": {"voices": voices_n, "personalities": personalities_n},
    }

@app.get("/settings")
async def get_all_settings():
    """Get all settings from app_state."""
    return db_service.db_service.get_all_settings()

@app.get("/settings/{key}")
async def get_setting(key: str):
    """Get a specific setting by key."""
    value = db_service.db_service.get_setting(key)
    return {"key": key, "value": value}

@app.put("/settings/{key}")
async def set_setting(key: str, body: SettingUpdate):
    """Set a setting value."""
    if key == "tts_backend":
        try:
            normalized = _normalize_tts_backend(body.value)
            db_service.db_service.set_setting(key, normalized)
            if pipeline:
                await pipeline.set_tts_backend(normalized)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        return {"key": key, "value": normalized}
    db_service.db_service.set_setting(key, body.value)
    return {"key": key, "value": body.value}

@app.delete("/settings/{key}")
async def delete_setting(key: str):
    """Delete a setting."""
    success = db_service.db_service.delete_setting(key)
    return {"deleted": success}

# --- Convenience endpoints for common settings ---

@app.get("/active-user")
async def get_active_user():
    """Get the active user ID."""
    user_id = db_service.db_service.get_active_user_id()
    user = db_service.db_service.get_user(user_id) if user_id else None
    return {
        "user_id": user_id,
        "user": {
            "id": user.id,
            "name": user.name,
            "current_personality_id": user.current_personality_id,
        } if user else None
    }

class ActiveUserUpdate(BaseModel):
    user_id: Optional[str] = None

@app.put("/active-user")
async def set_active_user(body: ActiveUserUpdate):
    """Set the active user ID."""
    db_service.db_service.set_active_user_id(body.user_id)
    return await get_active_user()

@app.get("/app-mode")
async def get_app_mode():
    """Get the current app mode."""
    return {"mode": db_service.db_service.get_app_mode()}

class AppModeUpdate(BaseModel):
    mode: str

@app.put("/app-mode")
async def set_app_mode(body: AppModeUpdate):
    """Set the app mode."""
    mode = db_service.db_service.set_app_mode(body.mode)
    return {"mode": mode}

# --- ESP32 device state ---

class DeviceUpdate(BaseModel):
    mac_address: Optional[str] = None
    volume: Optional[int] = None
    flashed: Optional[bool] = None
    ws_status: Optional[str] = None
    ws_last_seen: Optional[float] = None
    firmware_version: Optional[str] = None

@app.get("/device")
async def get_device():
    """Get current ESP32 device state."""
    return db_service.db_service.get_device_status()

@app.put("/device")
async def update_device(body: DeviceUpdate):
    """Patch ESP32 device state."""
    patch = body.model_dump(exclude_unset=True)
    return db_service.db_service.update_esp32_device(patch)


@app.post("/device/disconnect")
async def disconnect_device():
    """Force close the ESP32 WebSocket session."""
    global ESP32_WS, ESP32_SESSION_ID
    if ESP32_WS:
        try:
            await ESP32_WS.send_json({"type": "server", "msg": "SESSION.END"})
        except Exception:
            pass
        try:
            await ESP32_WS.close(code=1000)
        except Exception:
            pass
    ESP32_WS = None
    ESP32_SESSION_ID = None
    status = db_service.db_service.update_esp32_device(
        {"ws_status": "disconnected", "ws_last_seen": time.time(), "session_id": None}
    )
    _push_device_event(status)
    return status


class FirmwareFlashRequest(BaseModel):
    port: str
    baud: int = 460800
    chip: str = "esp32s3"
    offset: str = "0x10000"



@app.get("/firmware/ports")
async def firmware_ports():
    return {"ports": list_serial_ports()}


@app.post("/firmware/flash")
async def firmware_flash(body: FirmwareFlashRequest):
    fw_path = firmware_bin_path()
    if not fw_path.exists():
        raise HTTPException(status_code=404, detail=f"firmware.bin not found at {fw_path}")

    def run() -> Dict[str, object]:
        return run_firmware_flash(
            port=body.port,
            baud=body.baud,
            chip=body.chip,
            offset=body.offset,
            firmware_path=fw_path,
        )

    return await asyncio.to_thread(run)

import webrtcvad

# --- Models endpoint (for frontend Models.tsx) ---

@app.get("/models")
async def get_models():
    """Get current model configuration."""
    tts_backend = _normalize_tts_backend(
        getattr(pipeline, "tts_backend", None)
        or db_service.db_service.get_setting("tts_backend")
        or "qwen3-tts"
    )
    tts_repo = None
    if pipeline is not None and getattr(pipeline, "tts", None) is not None:
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
        }
    }

class ModelsUpdate(BaseModel):
    model_repo: Optional[str] = None

@app.put("/models")
async def set_models(body: ModelsUpdate):
    """Set model configuration (requires restart to take effect)."""
    if body.model_repo:
        db_service.db_service.set_setting("llm_model", body.model_repo)
    return await get_models()


class ModelSwitchRequest(BaseModel):
    model_repo: str


@app.post("/models/switch")
async def switch_model(body: ModelSwitchRequest):
    """
    Download a new LLM model and hot-swap it into the running pipeline.
    Returns a streaming response with progress updates as JSON lines.
    
    Progress format (newline-delimited JSON):
    {"stage": "downloading", "progress": 0.5, "message": "Downloading..."}
    {"stage": "loading", "progress": 0.9, "message": "Loading model weights..."}
    {"stage": "complete", "progress": 1.0, "message": "Model switched successfully"}
    {"stage": "error", "error": "Error message"}
    """
    global pipeline
    
    model_repo = body.model_repo.strip()
    if not model_repo:
        raise HTTPException(status_code=400, detail="model_repo is required")
    
    async def generate_progress():
        try:
            # Stage 1: Download the model
            yield json.dumps({"stage": "downloading", "progress": 0.0, "message": f"Starting download of {model_repo}..."}) + "\n"
            
            from huggingface_hub import HfApi, snapshot_download
            from huggingface_hub.constants import HF_HUB_CACHE
            import threading
            import time
            
            download_complete = threading.Event()
            download_error = [None]
            download_path = [None]
            start_time = [asyncio.get_event_loop().time()]
            expected_total_bytes = [None]
            baseline_bytes = [0]
            last_bytes = [0]
            last_change_monotonic = [time.monotonic()]

            def _repo_cache_dir() -> str:
                # HF cache layout: $HF_HUB_CACHE/models--org--repo
                repo_dir_name = f"models--{model_repo.replace('/', '--')}"
                return os.path.join(str(HF_HUB_CACHE), repo_dir_name)

            def _repo_cache_bytes() -> int:
                # Count both completed blobs and any .incomplete files.
                try:
                    base = _repo_cache_dir()
                    total = 0
                    for sub in ("blobs", "snapshots"):
                        d = os.path.join(base, sub)
                        if not os.path.isdir(d):
                            continue
                        for root, _dirs, files in os.walk(d):
                            for fn in files:
                                fp = os.path.join(root, fn)
                                try:
                                    st = os.stat(fp)
                                    total += int(st.st_size)
                                except Exception:
                                    continue
                    return total
                except Exception:
                    return 0

            def _xet_cache_bytes() -> int:
                # When HF uses Xet/CAS (cas-bridge.xethub.hf.co), the bulk data is stored under
                # the xet cache (typically alongside the hub cache).
                try:
                    # HF_HUB_CACHE is usually .../huggingface/hub; xet cache is often .../huggingface/xet
                    hub_cache = str(HF_HUB_CACHE)
                    root = os.path.dirname(hub_cache)
                    candidates = [
                        os.path.join(root, "xet"),
                        os.path.join(root, "xet-cache"),
                    ]
                    # Allow overrides if present
                    for env_key in ("HF_XET_CACHE", "XET_CACHE_DIR", "XET_HOME"):
                        v = os.environ.get(env_key)
                        if v and v.strip():
                            candidates.insert(0, v.strip())

                    total = 0
                    for d in candidates:
                        if not d or not os.path.isdir(d):
                            continue
                        for root_dir, _dirs, files in os.walk(d):
                            for fn in files:
                                fp = os.path.join(root_dir, fn)
                                try:
                                    st = os.stat(fp)
                                    total += int(st.st_size)
                                except Exception:
                                    continue
                    return total
                except Exception:
                    return 0

            def _total_cache_bytes() -> int:
                # Track overall cache growth (hub + xet) relative to a baseline.
                return _repo_cache_bytes() + _xet_cache_bytes()

            def _compute_expected_total_bytes() -> int | None:
                try:
                    # Some repos only expose per-file sizes when files_metadata=True.
                    info = HfApi().model_info(model_repo, files_metadata=True)
                    total = 0
                    siblings = getattr(info, "siblings", None) or []
                    for s in siblings:
                        size = getattr(s, "size", None)
                        if isinstance(size, int) and size > 0:
                            total += size
                    return total or None
                except Exception:
                    return None
            
            def download_model():
                try:
                    # Reliability knobs:
                    # - Disable Xet backend (it can stall on some networks)
                    # - Try to enable hf_transfer if installed (faster/more resilient)
                    os.environ["HF_HUB_DISABLE_XET"] = "1"
                    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
                    os.environ["HF_XET_DISABLE"] = "1"
                    os.environ["HF_HUB_DISABLE_HF_XET"] = "1"

                    expected_total_bytes[0] = _compute_expected_total_bytes()

                    # Download the full model snapshot
                    path = snapshot_download(
                        repo_id=model_repo,
                        local_files_only=False,
                        resume_download=True,
                        max_workers=4,
                    )
                    download_path[0] = path
                except Exception as e:
                    download_error[0] = str(e)
                finally:
                    download_complete.set()
            
            # Start download in background thread
            download_thread = threading.Thread(target=download_model)
            download_thread.start()
            
            # Poll for completion and send progress updates.
            # We compute progress from bytes in the HF cache (works reliably when Xet is disabled).
            # Also detect stalls (no byte growth for a while) and surface a clear error.
            stall_seconds = 300  # 5 minutes

            # Baseline the cache size at start so we can report bytes downloaded for this request.
            baseline_bytes[0] = _total_cache_bytes()
            last_bytes[0] = 0
            while not download_complete.is_set():
                await asyncio.sleep(1.0)  # Check every second
                elapsed = asyncio.get_event_loop().time() - start_time[0]

                current_bytes = max(0, _total_cache_bytes() - baseline_bytes[0])
                if current_bytes != last_bytes[0]:
                    last_bytes[0] = current_bytes
                    last_change_monotonic[0] = time.monotonic()
                else:
                    if time.monotonic() - last_change_monotonic[0] > stall_seconds:
                        yield json.dumps({
                            "stage": "error",
                            "error": (
                                "Model download appears stalled (no disk progress for 5 minutes). "
                                "This is often caused by the HuggingFace Xet backend or an unstable network. "
                                "Please retry; the server now forces HF_HUB_DISABLE_XET=1."
                            ),
                        }) + "\n"
                        return

                if isinstance(expected_total_bytes[0], int) and expected_total_bytes[0] > 0:
                    progress = min(0.99, current_bytes / expected_total_bytes[0])
                else:
                    # Fallback: keep UI moving even if we couldn't estimate the total size.
                    progress = min(0.95, 1.0 - (1.0 / (1.0 + elapsed / 10.0)))
                
                # Show elapsed time in message for long downloads
                if elapsed > 30:
                    mins = int(elapsed // 60)
                    secs = int(elapsed % 60)
                    time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
                    if isinstance(expected_total_bytes[0], int) and expected_total_bytes[0] > 0:
                        gb = current_bytes / (1024 ** 3)
                        total_gb = expected_total_bytes[0] / (1024 ** 3)
                        msg = f"Downloading {model_repo}... ({gb:.2f}/{total_gb:.2f} GB, {time_str} elapsed)"
                    else:
                        gb = current_bytes / (1024 ** 3)
                        msg = f"Downloading {model_repo}... ({gb:.2f} GB downloaded, {time_str} elapsed)"
                else:
                    gb = current_bytes / (1024 ** 3)
                    msg = f"Downloading {model_repo}... ({gb:.2f} GB)"
                
                yield json.dumps({"stage": "downloading", "progress": progress, "message": msg}) + "\n"
            
            download_thread.join()
            
            if download_error[0]:
                yield json.dumps({"stage": "error", "error": f"Download failed: {download_error[0]}"}) + "\n"
                return
            
            yield json.dumps({"stage": "downloading", "progress": 1.0, "message": "Download complete!"}) + "\n"
            
            # Stage 2: Load the model into memory
            yield json.dumps({"stage": "loading", "progress": 0.0, "message": "Loading model weights..."}) + "\n"
            
            try:
                if not pipeline:
                    raise RuntimeError("Pipeline is not initialized")

                # Load the new model
                new_llm, new_tokenizer, new_backend = await pipeline.load_llm_backend(model_repo)
                
                yield json.dumps({"stage": "loading", "progress": 0.5, "message": "Model loaded, swapping..."}) + "\n"
                
                # Hot-swap the model in the pipeline
                if pipeline:
                    async with pipeline.llm_lock:
                        # Replace the old model with the new one
                        old_llm = pipeline.llm
                        old_tokenizer = pipeline.tokenizer
                        
                        pipeline.llm = new_llm
                        pipeline.tokenizer = new_tokenizer
                        pipeline.llm_model = model_repo
                        pipeline.llm_backend = new_backend
                        
                        # Clear old model from memory
                        del old_llm
                        del old_tokenizer
                        mx.metal.clear_cache()
                
                # Save the setting
                db_service.db_service.set_setting("llm_model", model_repo)
                
                yield json.dumps({"stage": "loading", "progress": 1.0, "message": "Model weights loaded!"}) + "\n"
                yield json.dumps({"stage": "complete", "progress": 1.0, "message": f"Successfully switched to {model_repo}"}) + "\n"
                
            except Exception as e:
                logger.error(f"Failed to load model: {e}")
                yield json.dumps({"stage": "error", "error": f"Failed to load model: {str(e)}"}) + "\n"
                
        except Exception as e:
            logger.error(f"Model switch failed: {e}")
            yield json.dumps({"stage": "error", "error": str(e)}) + "\n"
    
    return StreamingResponse(
        generate_progress(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# --- Voices ---

@app.get("/voices")
async def get_voices(include_non_global: bool = True):
    voices = db_service.db_service.get_voices(include_non_global=include_non_global)
    return [
        {
            "voice_id": v.voice_id,
            "gender": v.gender,
            "voice_name": v.voice_name,
            "voice_description": v.voice_description,
            "voice_src": v.voice_src,
            "is_global": v.is_global,
            "created_at": getattr(v, "created_at", None),
        }
        for v in voices
    ]


class VoiceCreate(BaseModel):
    voice_id: str
    voice_name: str
    voice_description: Optional[str] = None


@app.post("/voices")
async def create_voice(body: VoiceCreate):
    v = db_service.db_service.upsert_voice(
        voice_id=body.voice_id,
        voice_name=body.voice_name,
        voice_description=body.voice_description,
        gender=None,
        voice_src=None,
        is_global=False,
    )
    if not v:
        raise HTTPException(status_code=500, detail="Failed to create voice")
    return {
        "voice_id": v.voice_id,
        "gender": v.gender,
        "voice_name": v.voice_name,
        "voice_description": v.voice_description,
        "voice_src": v.voice_src,
        "is_global": v.is_global,
        "created_at": getattr(v, "created_at", None),
    }


def _app_data_dir() -> Path:
    db_path = os.environ.get("ELATO_DB_PATH")
    if db_path:
        return Path(db_path).expanduser().resolve().parent
    try:
        from db.paths import default_db_path

        return Path(default_db_path()).expanduser().resolve().parent
    except Exception:
        return Path.cwd()


def _voices_dir() -> Path:
    return Path(os.environ.get("ELATO_VOICES_DIR") or _app_data_dir().joinpath("voices"))


def _images_dir() -> Path:
    return Path(os.environ.get("ELATO_IMAGES_DIR") or _app_data_dir().joinpath("images"))


class VoiceDownloadRequest(BaseModel):
    voice_id: str


@app.post("/assets/voices/download")
async def download_voice_asset(body: VoiceDownloadRequest):
    voice_id = (body.voice_id or "").strip()
    if not voice_id:
        raise HTTPException(status_code=400, detail="voice_id is required")

    print('downloading voice', voice_id)

    base_url = os.environ.get(
        "ELATO_VOICE_BASE_URL",
        "https://pub-6b92949063b142d59fc3478c56ec196c.r2.dev",
    ).rstrip("/")
    url = f"{base_url}/{urllib.parse.quote(voice_id)}.wav"
    timeout_s = float(os.environ.get("ELATO_VOICE_TIMEOUT_S", "10"))
    try:
        out_dir = _voices_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = out_dir.joinpath(f"{voice_id}.wav.part")
        final_path = out_dir.joinpath(f"{voice_id}.wav")

        def _fetch_to_path() -> None:
            try:
                start = time.monotonic()
                bytes_written = 0
                use_proxy = os.environ.get("ELATO_VOICE_USE_PROXY", "0") == "1"
                opener = (
                    urllib.request.build_opener()
                    if use_proxy
                    else urllib.request.build_opener(urllib.request.ProxyHandler({}))
                )
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Elato/1.0",
                        "Accept": "audio/wav,application/octet-stream;q=0.9,*/*;q=0.8",
                        "Accept-Encoding": "identity",
                    },
                )
                with opener.open(req, timeout=timeout_s) as resp:
                    if resp.status != 200:
                        raise HTTPException(status_code=404, detail=f"Voice not found: {voice_id}")
                    content_length = resp.getheader("Content-Length")
                    try:
                        resolved = socket.getaddrinfo("pub-6b92949063b142d59fc3478c56ec196c.r2.dev", 443)
                        resolved_ips = ",".join(sorted({r[4][0] for r in resolved}))
                    except Exception:
                        resolved_ips = "unknown"
                    logger.info(
                        "Downloading voice %s from %s (timeout=%.0fs, content_length=%s, resolved=%s, proxy=%s)",
                        voice_id,
                        url,
                        timeout_s,
                        content_length,
                        resolved_ips,
                        "on" if use_proxy else "off",
                    )
                    with open(tmp_path, "wb") as f:
                        while True:
                            chunk = resp.read(256 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)
                            bytes_written += len(chunk)
                elapsed = time.monotonic() - start
                logger.info(
                    "Downloaded voice %s (%d bytes) in %.2fs",
                    voice_id,
                    bytes_written,
                    elapsed,
                )
                if tmp_path.exists():
                    tmp_path.replace(final_path)
            except Exception:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
                raise

        await asyncio.to_thread(_fetch_to_path)
    except HTTPException:
        raise
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise HTTPException(status_code=404, detail=f"Voice not found: {voice_id}")
        detail = f"Failed to download (HTTP {e.code})"
        logger.warning("Voice download failed for %s: %s", voice_id, detail)
        raise HTTPException(status_code=502, detail=detail)
    except (socket.timeout, TimeoutError) as e:
        detail = f"Failed to download (timeout after {timeout_s:.0f}s)"
        logger.warning("Voice download failed for %s: %s", voice_id, detail)
        raise HTTPException(status_code=504, detail=detail)
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), socket.timeout):
            detail = f"Failed to download (timeout after {timeout_s:.0f}s)"
            logger.warning("Voice download failed for %s: %s", voice_id, detail)
            raise HTTPException(status_code=504, detail=detail)
        detail = f"Failed to download (network error: {getattr(e, 'reason', e)})"
        logger.warning("Voice download failed for %s: %s", voice_id, detail)
        raise HTTPException(status_code=502, detail=detail)
    except Exception as e:
        logger.warning("Voice download failed for %s: %s", voice_id, e)
        raise HTTPException(status_code=502, detail=f"Failed to download: {e}")

    return {"path": str(final_path)}


@app.get("/assets/voices/list")
async def list_downloaded_voices():
    out_dir = _voices_dir()
    if not out_dir.exists():
        return {"voices": []}
    voices: List[str] = []
    for path in out_dir.iterdir():
        if not path.is_file() or path.suffix.lower() != ".wav":
            continue
        voices.append(path.stem)
    voices.sort()
    return {"voices": voices}


@app.get("/assets/voices/{voice_id}/base64")
async def read_voice_base64(voice_id: str):
    voice_id = (voice_id or "").strip()
    if not voice_id:
        return {"base64": None}
    path = _voices_dir().joinpath(f"{voice_id}.wav")
    if not path.exists() or not path.is_file():
        return {"base64": None}
    data = path.read_bytes()
    encoded = base64.b64encode(data).decode("utf-8")
    return {"base64": encoded}


class ImageSaveRequest(BaseModel):
    experience_id: str
    base64_image: str
    ext: Optional[str] = None


@app.post("/assets/images/save")
async def save_experience_image(body: ImageSaveRequest):
    exp_id = (body.experience_id or "").strip()
    if not exp_id:
        raise HTTPException(status_code=400, detail="experience_id is required")
    raw = body.base64_image or ""
    try:
        data = base64.b64decode(raw)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to decode base64: {e}")

    safe_ext = (body.ext or "png").lower()
    safe_ext = "".join(c for c in safe_ext if c.isalnum())
    if not safe_ext:
        safe_ext = "png"

    out_dir = _images_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir.joinpath(f"personality_{exp_id}.{safe_ext}")
    path.write_bytes(data)
    return {"path": str(path)}

# --- Users CRUD ---

@app.get("/users")
async def get_users():
    """Get all users."""
    users = db_service.db_service.get_users()
    return [
        {
            "id": u.id,
            "name": u.name,
            "age": u.age,
            "current_personality_id": u.current_personality_id,
            "user_type": u.user_type,
            "about_you": getattr(u, "about_you", "") or "",
            "avatar_emoji": getattr(u, "avatar_emoji", None),
        }
        for u in users
    ]

class UserCreate(BaseModel):
    name: str
    age: Optional[int] = None
    about_you: Optional[str] = ""
    avatar_emoji: Optional[str] = None

@app.post("/users")
async def create_user(body: UserCreate):
    """Create a new user."""
    user = db_service.db_service.create_user(
        name=body.name,
        age=body.age,
        about_you=body.about_you or "",
        avatar_emoji=body.avatar_emoji,
    )
    return {"id": user.id, "name": user.name}

@app.put("/users/{user_id}")
async def update_user(user_id: str, body: Dict[str, Any]):
    """Update a user."""
    user = db_service.db_service.update_user(user_id, **body)
    if not user:
        return {"error": "User not found"}, 404
    return {"id": user.id, "name": user.name}

# --- Experiences CRUD (personalities, games, stories) ---

def _experience_to_dict(p):
    return {
        "id": p.id,
        "name": p.name,
        "prompt": p.prompt,
        "short_description": p.short_description,
        "tags": p.tags,
        "is_visible": p.is_visible,
        "is_global": p.is_global,
        "voice_id": p.voice_id,
        "type": getattr(p, "type", "personality"),
        "img_src": getattr(p, "img_src", None),
        "created_at": getattr(p, "created_at", None),
    }


@app.get("/experiences")
async def get_experiences(include_hidden: bool = False, type: Optional[str] = None):
    """Get all experiences (personalities, games, stories)."""
    experiences = db_service.db_service.get_experiences(
        include_hidden=include_hidden,
        experience_type=type if type in ("personality", "game", "story") else None,
    )
    return [_experience_to_dict(p) for p in experiences]


@app.get("/personalities")
async def get_personalities(include_hidden: bool = False):
    """Get all personalities (backward compatible)."""
    personalities = db_service.db_service.get_experiences(
        include_hidden=include_hidden,
        experience_type=None,  # Return all types for backward compatibility
    )
    return [_experience_to_dict(p) for p in personalities]


class ExperienceCreate(BaseModel):
    name: str
    prompt: str
    short_description: Optional[str] = ""
    tags: list = []
    voice_id: str = "radio"
    type: str = "personality"
    is_global: bool = False
    img_src: Optional[str] = None


# Alias for backward compatibility
PersonalityCreate = ExperienceCreate


@app.post("/experiences")
async def create_experience(body: ExperienceCreate):
    """Create a new experience (personality, game, or story)."""
    exp_type = body.type if body.type in ("personality", "game", "story") else "personality"
    p = db_service.db_service.create_experience(
        name=body.name,
        prompt=body.prompt,
        short_description=body.short_description or "",
        tags=body.tags,
        voice_id=body.voice_id,
        experience_type=exp_type,
        is_global=False,
        img_src=body.img_src,
    )
    return _experience_to_dict(p)


@app.post("/personalities")
async def create_personality(body: ExperienceCreate):
    """Create a new personality (backward compatible)."""
    p = db_service.db_service.create_experience(
        name=body.name,
        prompt=body.prompt,
        short_description=body.short_description or "",
        tags=body.tags,
        voice_id=body.voice_id,
        experience_type="personality",
        is_global=False,
        img_src=body.img_src,
    )
    return {"id": p.id, "name": p.name}


class GenerateExperienceRequest(BaseModel):
    description: str
    voice_id: Optional[str] = None
    type: str = "personality"


# Alias for backward compatibility
GeneratePersonalityRequest = GenerateExperienceRequest


@app.post("/experiences/generate")
async def generate_experience(body: GenerateExperienceRequest):
    """Generate an experience from a description using the LLM."""
    if not pipeline:
        raise HTTPException(status_code=503, detail="AI engine not ready")

    description = body.description
    voice_id = body.voice_id or "radio"
    exp_type = body.type if body.type in ("personality", "game", "story") else "personality"
    logger.info(f"Generating {exp_type} from description: {description}")

    type_context = {
        "personality": "a character to chat with",
        "game": "an interactive game host",
        "story": "an interactive storyteller",
    }
    context = type_context.get(exp_type, "a character")

    # 1. Generate Name
    name_prompt = f"Based on this description: '{description}', suggest a short, creative name for {context}. Output ONLY the name, nothing else."
    name = await pipeline.generate_text_simple(name_prompt, max_tokens=30)
    name = name.strip().strip('"').strip("'").split("\n")[0]

    # 2. Generate Short Description
    desc_prompt = f"Based on this description: '{description}', provide a very short (1 sentence) description of {context}. Output ONLY the description."
    short_desc = await pipeline.generate_text_simple(desc_prompt, max_tokens=100)
    short_desc = short_desc.strip().strip('"').strip("'")

    # 3. Generate System Prompt
    sys_prompt = f"Based on this description: '{description}', write a system prompt for an AI to act as {context}. The prompt should start with 'You are [Name]...'. Output ONLY the prompt."
    system_prompt = await pipeline.generate_text_simple(sys_prompt, max_tokens=300)
    system_prompt = system_prompt.strip()

    tags: list = []

    p = db_service.db_service.create_experience(
        name=name,
        prompt=system_prompt,
        short_description=short_desc,
        tags=tags,
        voice_id=voice_id,
        experience_type=exp_type,
        is_global=False,
    )

    return _experience_to_dict(p)


@app.post("/personalities/generate")
async def generate_personality(body: GenerateExperienceRequest):
    """Generate a personality from description (backward compatible)."""
    body.type = "personality"
    return await generate_experience(body)


@app.put("/experiences/{experience_id}")
async def update_experience(experience_id: str, body: Dict[str, Any]):
    """Update an experience."""
    p = db_service.db_service.update_experience(experience_id, **body)
    if not p:
        raise HTTPException(status_code=404, detail="Experience not found")
    return _experience_to_dict(p)


@app.put("/personalities/{personality_id}")
async def update_personality(personality_id: str, body: Dict[str, Any]):
    """Update a personality (backward compatible)."""
    p = db_service.db_service.update_experience(personality_id, **body)
    if not p:
        return {"error": "Personality not found"}, 404
    return {"id": p.id, "name": p.name}


@app.delete("/experiences/{experience_id}")
async def delete_experience(experience_id: str):
    """Delete an experience."""
    ok = db_service.db_service.delete_experience(experience_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Experience not found or cannot delete global experience")
    return {"ok": True}


@app.delete("/personalities/{personality_id}")
async def delete_personality(personality_id: str):
    """Delete a personality (backward compatible)."""
    ok = db_service.db_service.delete_experience(personality_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Personality not found or cannot delete global personality")
    return {"ok": True}

# --- Conversations ---

@app.get("/conversations")
async def get_conversations(limit: int = 50, offset: int = 0, session_id: Optional[str] = None):
    """Get conversations."""
    convos = db_service.db_service.get_conversations(limit=limit, offset=offset, session_id=session_id)
    return [
        {
            "id": c.id,
            "role": c.role,
            "transcript": c.transcript,
            "timestamp": c.timestamp,
            "session_id": c.session_id,
        }
        for c in convos
    ]

# --- Sessions ---

@app.get("/sessions")
async def get_sessions(limit: int = 50, offset: int = 0, user_id: Optional[str] = None):
    """Get sessions."""
    sessions = db_service.db_service.get_sessions(limit=limit, offset=offset, user_id=user_id)
    return [
        {
            "id": s.id,
            "started_at": s.started_at,
            "ended_at": s.ended_at,
            "duration_sec": s.duration_sec,
            "client_type": s.client_type,
            "user_id": s.user_id,
            "personality_id": s.personality_id,
        }
        for s in sessions
    ]

# --- Shutdown ---

@app.post("/shutdown")
async def shutdown():
    """Shutdown the server."""
    import signal
    os.kill(os.getpid(), signal.SIGTERM)
    return {"status": "shutting down"}


@app.websocket("/ws")
async def websocket_unified(websocket: WebSocket, client_type: str = Query(default=CLIENT_TYPE_DESKTOP)):
    """
    Unified WebSocket endpoint for voice communication.
    
    Supports two client types differentiated by query param or header:
    - desktop (default): React UI client - uses base64 JSON audio
    - esp32: ESP32 device - uses raw PCM binary + Opus output
    
    Query param: ?client_type=esp32 or ?client_type=desktop
    Header: X-Client-Type: esp32 or X-Client-Type: desktop
    
    Desktop Protocol:
    - Client sends: {"type": "audio", "data": "<base64 int16 PCM>"}
    - Client sends: {"type": "end_of_speech"}
    - Server sends: {"type": "transcription", "text": "..."}
    - Server sends: {"type": "response", "text": "..."}
    - Server sends: {"type": "audio", "data": "<base64 int16 PCM>"}
    - Server sends: {"type": "audio_end"}
    
    ESP32 Protocol:
    - Client sends: raw PCM16 bytes at 16kHz
    - Client sends: {"type": "instruction", "msg": "end_of_speech"}
    - Server sends: {"type": "server", "msg": "RESPONSE.CREATED"/"RESPONSE.COMPLETE"/"AUDIO.COMMITTED"}
    - Server sends: Opus-encoded audio bytes at 24kHz
    """
    # Check header for client type override
    header_client = websocket.headers.get("x-client-type", "").lower()
    if header_client in (CLIENT_TYPE_ESP32, CLIENT_TYPE_DESKTOP):
        client_type = header_client
    
    global ESP32_WS, ESP32_SESSION_ID
    is_esp32 = client_type == CLIENT_TYPE_ESP32
    client_label = "[ESP32]" if is_esp32 else "[Desktop]"
    
    if is_esp32:
        await websocket.accept()
        ESP32_WS = websocket
    else:
        await manager.connect(websocket)

    if not pipeline:
        await websocket.close()
        return

    llm_repo = getattr(pipeline, "llm_model", "") or ""
    thinking_model = _is_thinking_model(llm_repo)

    session_id = str(uuid.uuid4())
    if is_esp32:
        ESP32_SESSION_ID = session_id
    
    # Get active user/personality
    user_id = db_service.db_service.get_active_user_id()
    personality_id = None
    if user_id:
        u = db_service.db_service.get_user(user_id)
        personality_id = u.current_personality_id if u else None
    
    personality = None
    if personality_id:
        try:
            personality = db_service.db_service.get_personality(personality_id)
        except Exception:
            personality = None

    # Start session
    try:
        db_service.db_service.start_session(
            session_id=session_id,
            client_type="device" if is_esp32 else "desktop",
            user_id=user_id,
            personality_id=personality_id,
        )
    except Exception as e:
        logger.error(f"Failed to start session: {e}")

    if is_esp32:
        try:
            status = db_service.db_service.update_esp32_device(
                {
                    "ws_status": "connected",
                    "ws_last_seen": time.time(),
                    "session_id": session_id,
                }
            )
            _push_device_event(status)
        except Exception:
            pass

    # Helper to build LLM context with conversation history
    def _build_llm_context(user_text: str) -> List[Dict[str, str]]:
        try:
            convos = db_service.db_service.get_conversations(session_id=session_id)
        except Exception:
            convos = []

        runtime = build_runtime_context()
        user_ctx = None
        try:
            u = db_service.db_service.get_user(user_id) if user_id else None
            if u:
                user_ctx = {
                    "name": u.name,
                    "age": u.age,
                    "about_you": getattr(u, "about_you", "") or "",
                    "user_type": u.user_type,
                }
        except Exception:
            user_ctx = None

        tts_backend = (
            getattr(pipeline, "tts_backend", None)
            or db_service.db_service.get_setting("tts_backend")
            or "qwen3-tts"
        )
        tts_backend = _normalize_tts_backend(tts_backend)

        experience_type = getattr(personality, "type", "personality") if personality else "personality"

        behavior_constraints = (
            "You always respond with short sentences. "
            "Avoid punctuation like parentheses or colons or markdown that would not appear in conversational speech. Do not use Markdown formatting (no *, **, _, __, backticks). "
        )

        if tts_backend == "chatterbox-turbo":
            behavior_constraints += (
                "To add expressivity, you should occasionally use ONLY these paralinguistic cues in brackets: "
                "[laugh], [chuckle], [sigh], [gasp], [cough], [clear throat], [sniff], [groan], [shush]. "
                "Use only these cues naturally in context to enhance the conversational flow. "
                "Examples: [chuckle] That is funny. [sigh] That was a long day."
            )

        if experience_type == "game":
            behavior_constraints += (
                " You are the game host and you do everything needed to run the game. "
                "Do NOT put any setup tasks on the user. Do NOT ask the user to choose a mode or category unless they ask for it. "
                "Start the game immediately after greeting; greet in one short line and then begin the first move. "
                "Never ask the user to think of something; you choose any secret item or answer internally. "
                "If the user says begin, start, ready, or hi/hello/hey, immediately start the game with the correct opening. "
                "Keep the game moving with one clear prompt at a time. "
                "After each user turn, respond and then prompt for the next step."
            )

            game_name = (getattr(personality, "name", "") or "").lower()
            if "20 questions" in game_name or "twenty questions" in game_name:
                behavior_constraints += (
                    " This is 20 Questions. You secretly choose an item and the user asks yes/no questions. "
                    "Answer with Yes/No/Unsure plus a short friendly sentence. "
                    "Always include a running count like 'Question 3/20' in every reply after a question. "
                    "If the user makes a direct guess, confirm if correct and end the round. "
                    "If incorrect, say it's not correct and continue with the next question count. "
                    "Offer a gentle hint after Question 10 or if the user asks for a hint."
                )
        elif experience_type == "story":
            if _is_bedtime_story_mode():
                behavior_constraints += (
                    " You are in bedtime mode. You are the story director agent responsible for "
                    "plot planning, pacing, and chapter transitions in a single continuous bedtime story. "
                    "Do not ask questions, do not pause for choices, and do not wait for user input. "
                    "Keep the narrative flowing gently on your own with soothing sleepy pacing. "
                    "Each continuation should feel like the next chapter of the same story world. "
                    "Make it fun for kids: add one playful discovery or tiny wonder in each chapter. "
                    "Never repeat previous lines verbatim. Keep variety in imagery and actions. "
                    "Keep sentences short, warm, and simple. Avoid scary or complex themes."
                )
            else:
                behavior_constraints += (
                    " You are an interactive choose-your-adventure storyteller for kids. "
                    "After a short scene, offer exactly two clear choices and then wait for the user's decision. "
                    "Keep the story coherent, playful, and safe. "
                    "Keep sentences short, warm, and simple. Avoid scary or complex themes."
                )

        if thinking_model:
            behavior_constraints += " Do not output <think> or reasoning text. Respond with the final answer only."

        sys_prompt = build_system_prompt(
            personality_name=getattr(personality, "name", None),
            personality_prompt=getattr(personality, "prompt", None),
            user_context=user_ctx,
            runtime=runtime,
            extra_system_prompt=behavior_constraints,
        )

        history_msgs: List[Dict[str, str]] = []
        for c in convos:
            if c.role == "user":
                history_msgs.append({"role": "user", "content": c.transcript})
            elif c.role == "ai":
                history_msgs.append({"role": "assistant", "content": c.transcript})

        return build_llm_messages(
            system_prompt=sys_prompt,
            history=history_msgs,
            user_text=user_text,
            max_history_messages=30,
        )

    # Get volume setting
    volume = 100
    try:
        raw = db_service.db_service.get_setting("laptop_volume")
        if raw is not None:
            volume = int(raw)
    except Exception:
        pass
    
    # Send auth/session message
    if is_esp32:
        try:
            await websocket.send_json({
                "type": "auth",
                "volume_control": volume,
                "pitch_factor": 1.0,
                "is_ota": False,
                "is_reset": False
            })
        except Exception:
            return
    else:
        try:
            await websocket.send_text(json.dumps({"type": "session_started", "session_id": session_id}))
        except Exception:
            pass
    
    logger.info(f"{client_label} Client connected, session={session_id}")

    def _is_bedtime_story_mode() -> bool:
        try:
            mode = (db_service.db_service.get_app_mode() or "").strip().lower()
        except Exception:
            mode = "story"
        experience_type = getattr(personality, "type", "personality") if personality else "personality"
        return mode == "bedtime" and experience_type == "story"

    def _get_bedtime_auto_chapters() -> int:
        try:
            raw = db_service.db_service.get_setting("bedtime_auto_chapters")
            if raw is not None:
                n = int(str(raw).strip())
                return max(1, min(8, n))
        except Exception:
            pass
        return 3
    
    # Generate and send initial greeting (speak first, then listen)
    cancel_event = asyncio.Event()
    try:
        experience_type = getattr(personality, "type", "personality") if personality else "personality"
        if experience_type == "game":
            greeting_user_text = (
                "[System] The user just connected. Give a short greeting (under 8 words) "
                "and immediately start the game with the first move. Do NOT ask if they are ready."
            )
        elif experience_type == "story":
            if _is_bedtime_story_mode():
                greeting_user_text = (
                    "[System] The user just connected in bedtime mode. Start a calm bedtime story immediately. "
                    "Use 2-3 short soothing sentences. Do not ask questions or wait for input."
                )
            else:
                greeting_user_text = (
                    "[System] The user just connected. Start a choose-your-adventure story immediately. "
                    "Use 1-2 short opening sentences and end with exactly two choices."
                )
        else:
            greeting_user_text = "[System] The user just connected. Greet them with a short friendly sentence (under 8 words)."
        greeting_messages = _build_llm_context(greeting_user_text)
        greeting_text = await pipeline.generate_response(
            greeting_user_text,
            messages=greeting_messages,
            max_tokens=50,
            clear_thinking=True if thinking_model else None,
        )
        greeting_text = greeting_text.strip() or "Hello!"

        if thinking_model:
            greeting_text = _strip_thinking(greeting_text)

        allow_paralinguistic = _normalize_tts_backend(getattr(pipeline, "tts_backend", None)) == "chatterbox-turbo"
        greeting_text = sanitize_spoken_text(greeting_text, allow_paralinguistic=allow_paralinguistic)
        
        logger.info(f"{client_label} Greeting: {greeting_text}")
        
        active_voice_id = getattr(personality, "voice_id", None)
        ref_audio_path = resolve_voice_ref_audio_path(active_voice_id)
        ref_text = resolve_voice_ref_text(active_voice_id)
        
        if is_esp32:
            # ESP32: Send RESPONSE.CREATED, then Opus audio, then RESPONSE.COMPLETE
            try:
                await websocket.send_json({"type": "server", "msg": "RESPONSE.CREATED", "volume_control": volume})
            except Exception:
                pass

            opus_packets = []
            opus = create_opus_packetizer(lambda pkt: opus_packets.append(pkt))
            
            async for audio_chunk in pipeline.synthesize_speech(
                greeting_text,
                ref_audio_path=ref_audio_path,
                ref_text=ref_text,
            ):
                chunk_mutable = bytearray(audio_chunk)
                utils.boost_limit_pcm16le_in_place(chunk_mutable, gain_db=GAIN_DB, ceiling=CEILING)
                opus.push(chunk_mutable)
                while opus_packets:
                    try:
                        await websocket.send_bytes(opus_packets.pop(0))
                    except Exception:
                        break
            
            opus.flush(pad_final_frame=True)
            while opus_packets:
                try:
                    await websocket.send_bytes(opus_packets.pop(0))
                except Exception:
                    break
            opus.close()

            try:
                await websocket.send_json({"type": "server", "msg": "RESPONSE.COMPLETE"})
            except Exception:
                pass
        else:
            # Desktop: Send response text, then base64 audio, then audio_end
            try:
                await websocket.send_text(json.dumps({"type": "response", "text": greeting_text}))
            except Exception:
                pass
            
            async for audio_chunk in pipeline.synthesize_speech(
                greeting_text,
                ref_audio_path=ref_audio_path,
                ref_text=ref_text,
            ):
                try:
                    await websocket.send_text(
                        json.dumps({
                            "type": "audio",
                            "data": base64.b64encode(audio_chunk).decode("utf-8"),
                        })
                    )
                except Exception:
                    break
            
            try:
                await websocket.send_text(json.dumps({"type": "audio_end"}))
            except Exception:
                pass

        # Log a synthetic user message so history alternates properly (user -> assistant)
        try:
            db_service.db_service.log_conversation(role="user", transcript="[connected]", session_id=session_id)
        except Exception:
            pass
        try:
            db_service.db_service.log_conversation(role="ai", transcript=greeting_text, session_id=session_id)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"{client_label} Greeting generation failed: {e}")

    # Common state
    audio_buffer = bytearray()
    cancel_event = asyncio.Event()
    current_tts_task = None
    ws_open = True
    session_system_prompt = None
    session_voice = "dave"
    bedtime_sequence_task = None
    bedtime_disconnect_task = None

    # VAD setup for ESP32
    if is_esp32:
        vad = webrtcvad.Vad(3)
        input_sample_rate = 16000
        vad_frame_ms = 30
        vad_frame_bytes = int(input_sample_rate * vad_frame_ms / 1000) * 2
        speech_frames = []
        is_speaking = False
        silence_count = 0
        SILENCE_FRAMES = int(1.5 / (vad_frame_ms / 1000))  # 1.5s of silence
    
    # Desktop prebuffer settings
    PREBUFFER_MS = 300
    PREBUFFER_BYTES = int(pipeline.output_sample_rate * (PREBUFFER_MS / 1000.0) * 2)

    async def _emit_ai_turn(ai_text: str, for_esp32: bool):
        if not ai_text or cancel_event.is_set() or not ws_open:
            return
        if for_esp32:
            try:
                await websocket.send_json(
                    {"type": "server", "msg": "RESPONSE.CREATED", "volume_control": volume}
                )
            except Exception:
                cancel_event.set()
                return

            opus_packets = []
            opus = create_opus_packetizer(lambda pkt: opus_packets.append(pkt))
            active_voice_id = getattr(personality, "voice_id", None)
            ref_audio_path = resolve_voice_ref_audio_path(active_voice_id)
            ref_text = resolve_voice_ref_text(active_voice_id)
            async for audio_chunk in pipeline.synthesize_speech(
                ai_text,
                cancel_event,
                ref_audio_path=ref_audio_path,
                ref_text=ref_text,
            ):
                if cancel_event.is_set() or not ws_open:
                    break
                chunk_mutable = bytearray(audio_chunk)
                utils.boost_limit_pcm16le_in_place(
                    chunk_mutable, gain_db=GAIN_DB, ceiling=CEILING
                )
                opus.push(chunk_mutable)
                while opus_packets:
                    try:
                        await websocket.send_bytes(opus_packets.pop(0))
                    except Exception:
                        cancel_event.set()
                        break
            opus.flush(pad_final_frame=True)
            while opus_packets:
                try:
                    await websocket.send_bytes(opus_packets.pop(0))
                except Exception:
                    break
            opus.close()
            try:
                await websocket.send_json({"type": "server", "msg": "RESPONSE.COMPLETE"})
            except Exception:
                pass
            return

        try:
            await websocket.send_text(json.dumps({"type": "response", "text": ai_text}))
        except Exception:
            cancel_event.set()
            return

        active_voice_id = getattr(personality, "voice_id", None)
        ref_audio_path = resolve_voice_ref_audio_path(active_voice_id)
        ref_text = resolve_voice_ref_text(active_voice_id)
        async for audio_chunk in pipeline.synthesize_speech(
            ai_text,
            cancel_event,
            ref_audio_path=ref_audio_path,
            ref_text=ref_text,
        ):
            if cancel_event.is_set() or not ws_open:
                break
            try:
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "audio",
                            "data": base64.b64encode(audio_chunk).decode("utf-8"),
                        }
                    )
                )
            except Exception:
                cancel_event.set()
                break
        try:
            await websocket.send_text(json.dumps({"type": "audio_end"}))
        except Exception:
            pass

    async def _run_bedtime_autoplay(for_esp32: bool):
        """Hard non-interactive bedtime flow: no mic input, fixed chapter progression."""
        allow_paralinguistic = (
            _normalize_tts_backend(getattr(pipeline, "tts_backend", None))
            == "chatterbox-turbo"
        )
        bedtime_prompts = ["CONTINUE THE STORY"] * 9 + ["END THE STORY"]

        if not for_esp32:
            try:
                await websocket.send_text(
                    json.dumps({"type": "bedtime_mode", "mic_enabled": False})
                )
            except Exception:
                pass

        async def _sequence():
            for idx, auto_step in enumerate(bedtime_prompts, start=1):
                if cancel_event.is_set() or not ws_open:
                    break
                is_final = auto_step == "END THE STORY"
                if not is_final:
                    auto_prompt = (
                        f"[System] CONTINUE THE STORY. Chapter {idx}/10. "
                        "Keep the same world and characters and move the plot forward. "
                        "Make this chapter interesting for kids with exactly one playful event, "
                        "one magical sensory detail, and one comforting moment. "
                        "No questions and no choices. "
                        "Do not repeat prior lines verbatim or restate the opening scene. "
                        "Keep to 5-7 short sentences."
                    )
                else:
                    auto_prompt = (
                        "[System] END THE STORY. Chapter 10/10. "
                        "Give a gentle satisfying ending with calm closure and sleep cues. "
                        "No questions and no choices. Keep to 4-6 short sentences."
                    )
                try:
                    db_service.db_service.log_conversation(
                        role="user",
                        transcript=f"[auto-bedtime] step {idx}: {auto_prompt}",
                        session_id=session_id,
                    )
                except Exception:
                    pass

                llm_messages = _build_llm_context(auto_prompt)
                ai_text = await pipeline.generate_response(
                    auto_prompt,
                    messages=llm_messages,
                    max_tokens=140,
                    clear_thinking=True if thinking_model else None,
                )
                if thinking_model:
                    ai_text = _strip_thinking(ai_text)
                ai_text = sanitize_spoken_text(
                    ai_text, allow_paralinguistic=allow_paralinguistic
                ).strip()
                if not ai_text:
                    continue
                logger.info(
                    f"{client_label} Bedtime auto step {idx}/{len(bedtime_prompts)}: {ai_text}"
                )
                await _emit_ai_turn(ai_text, for_esp32=for_esp32)
                try:
                    db_service.db_service.log_conversation(
                        role="ai", transcript=ai_text, session_id=session_id
                    )
                except Exception:
                    pass

        try:
            await asyncio.wait_for(_sequence(), timeout=600.0)
        except asyncio.TimeoutError:
            timeout_text = (
                "The stars are dim now, and the story is ready to sleep. Goodnight."
            )
            await _emit_ai_turn(timeout_text, for_esp32=for_esp32)
            try:
                db_service.db_service.log_conversation(
                    role="ai", transcript=timeout_text, session_id=session_id
                )
            except Exception:
                pass
        finally:
            if ws_open:
                with suppress(Exception):
                    await websocket.close(code=1000)

    async def _wait_for_bedtime_disconnect():
        while not cancel_event.is_set():
            try:
                msg = await websocket.receive()
            except Exception:
                break
            if msg.get("type") == "websocket.disconnect":
                break
        cancel_event.set()

    async def process_transcription_and_respond(transcription: str, for_esp32: bool):
        """Common logic for processing transcription and generating response."""
        nonlocal cancel_event, personality, ws_open, volume
        
        if not transcription or not transcription.strip():
            return
        
        logger.info(f"{client_label} Transcript: {transcription}")
        
        # Send transcription acknowledgment
        if for_esp32:
            try:
                await websocket.send_json({"type": "server", "msg": "AUDIO.COMMITTED"})
            except Exception as e:
                logger.error(f"{client_label} Failed to send AUDIO.COMMITTED: {e}")
                return
        else:
            try:
                await websocket.send_text(json.dumps({"type": "transcription", "text": transcription}))
            except Exception as e:
                logger.error(f"{client_label} Failed to send transcription: {e}")
                return

        # Build LLM context BEFORE logging the user message to avoid duplicate
        cancel_event.clear()
        llm_messages = _build_llm_context(transcription)
        
        # Now log the user conversation
        try:
            db_service.db_service.log_conversation(
                role="user", transcript=transcription, session_id=session_id
            )
        except Exception as e:
            logger.error(f"Failed to log user conversation: {e}")
        
        # Stream LLM -> TTS concurrently (chunked text)
        allow_paralinguistic = (
            _normalize_tts_backend(getattr(pipeline, "tts_backend", None))
            == "chatterbox-turbo"
        )
        active_voice_id = getattr(personality, "voice_id", None)
        ref_audio_path = resolve_voice_ref_audio_path(active_voice_id)
        ref_text = resolve_voice_ref_text(active_voice_id)
        logger.info(
            f"{client_label} Streaming response backend={getattr(pipeline, 'tts_backend', 'unknown')} "
            f"ref_audio={'yes' if ref_audio_path else 'no'}"
        )

        if for_esp32:
            try:
                await websocket.send_json(
                    {"type": "server", "msg": "RESPONSE.CREATED", "volume_control": volume}
                )
            except Exception:
                return

        def _extract_speakable_chunks(buffer: str, flush: bool = False):
            chunks = []
            while True:
                m = re.search(r"(.+?[.!?。！？])(?:\s+|$)", buffer, flags=re.DOTALL)
                if not m:
                    break
                chunk = buffer[: m.end(1)].strip()
                buffer = buffer[m.end(1) :].lstrip()
                if chunk:
                    chunks.append(chunk)

            if not flush and len(buffer) >= 140:
                split_at = buffer.rfind(" ", 0, 120)
                if split_at <= 0:
                    split_at = 120
                chunk = buffer[:split_at].strip()
                buffer = buffer[split_at:].lstrip()
                if chunk:
                    chunks.append(chunk)

            if flush:
                final = buffer.strip()
                if final:
                    chunks.append(final)
                buffer = ""

            return chunks, buffer

        text_queue: asyncio.Queue = asyncio.Queue(maxsize=16)
        llm_text_parts: list[str] = []
        llm_error: list[Exception] = []
        carry = ""

        async def _llm_producer():
            nonlocal carry
            try:
                async for delta in pipeline.stream_response(
                    transcription,
                    messages=llm_messages,
                    clear_thinking=True if thinking_model else None,
                    cancel_event=cancel_event,
                ):
                    if cancel_event.is_set() or not ws_open:
                        break
                    llm_text_parts.append(delta)
                    carry += delta
                    ready, carry = _extract_speakable_chunks(carry, flush=False)
                    for chunk in ready:
                        chunk = sanitize_spoken_text(
                            chunk, allow_paralinguistic=allow_paralinguistic
                        ).strip()
                        if chunk:
                            await text_queue.put(chunk)
            except asyncio.CancelledError:
                return
            except Exception as e:
                llm_error.append(e)
            finally:
                if carry and not cancel_event.is_set():
                    ready, _ = _extract_speakable_chunks(carry, flush=True)
                    for chunk in ready:
                        chunk = sanitize_spoken_text(
                            chunk, allow_paralinguistic=allow_paralinguistic
                        ).strip()
                        if chunk:
                            with suppress(asyncio.QueueFull):
                                text_queue.put_nowait(chunk)
                with suppress(asyncio.QueueFull):
                    text_queue.put_nowait(None)

        producer_task = asyncio.create_task(_llm_producer())

        if for_esp32:
            opus_packets = []
            opus = create_opus_packetizer(lambda pkt: opus_packets.append(pkt))
            try:
                while True:
                    phrase = await text_queue.get()
                    if phrase is None:
                        break
                    async for audio_chunk in pipeline.synthesize_speech(
                        phrase,
                        cancel_event,
                        ref_audio_path=ref_audio_path,
                        ref_text=ref_text,
                    ):
                        if cancel_event.is_set() or not ws_open:
                            break
                        chunk_mutable = bytearray(audio_chunk)
                        utils.boost_limit_pcm16le_in_place(
                            chunk_mutable, gain_db=GAIN_DB, ceiling=CEILING
                        )
                        opus.push(chunk_mutable)
                        while opus_packets:
                            try:
                                await websocket.send_bytes(opus_packets.pop(0))
                            except Exception:
                                cancel_event.set()
                                break
            except Exception as e:
                logger.error(f"{client_label} TTS stream error (esp32): {e}")
                cancel_event.set()
            finally:
                cancel_event.set()
                if producer_task and not producer_task.done():
                    producer_task.cancel()
                with suppress(Exception):
                    await producer_task
                opus.flush(pad_final_frame=True)
                while opus_packets:
                    try:
                        await websocket.send_bytes(opus_packets.pop(0))
                    except Exception:
                        break
                opus.close()
                try:
                    await websocket.send_json({"type": "server", "msg": "RESPONSE.COMPLETE"})
                except Exception:
                    pass
        else:
            buffered = bytearray()
            started = False
            try:
                while True:
                    phrase = await text_queue.get()
                    if phrase is None:
                        break
                    async for audio_chunk in pipeline.synthesize_speech(
                        phrase,
                        cancel_event,
                        ref_audio_path=ref_audio_path,
                        ref_text=ref_text,
                    ):
                        if cancel_event.is_set() or not ws_open:
                            break
                        if not started:
                            buffered.extend(audio_chunk)
                            if len(buffered) < PREBUFFER_BYTES:
                                continue
                            try:
                                await websocket.send_text(
                                    json.dumps(
                                        {
                                            "type": "audio",
                                            "data": base64.b64encode(bytes(buffered)).decode(
                                                "utf-8"
                                            ),
                                        }
                                    )
                                )
                            except Exception:
                                cancel_event.set()
                                break
                            buffered.clear()
                            started = True
                        else:
                            try:
                                await websocket.send_text(
                                    json.dumps(
                                        {
                                            "type": "audio",
                                            "data": base64.b64encode(audio_chunk).decode("utf-8"),
                                        }
                                    )
                                )
                            except Exception:
                                cancel_event.set()
                                break
            except Exception as e:
                logger.error(f"{client_label} TTS stream error (desktop): {e}")
                cancel_event.set()
            finally:
                cancel_event.set()
                if producer_task and not producer_task.done():
                    producer_task.cancel()
                with suppress(Exception):
                    await producer_task
                if buffered:
                    try:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "audio",
                                    "data": base64.b64encode(bytes(buffered)).decode("utf-8"),
                                }
                            )
                        )
                    except Exception:
                        pass
                try:
                    await websocket.send_text(json.dumps({"type": "audio_end"}))
                except Exception:
                    pass

        full_response = "".join(llm_text_parts)
        full_response = sanitize_spoken_text(
            full_response, allow_paralinguistic=allow_paralinguistic
        ).strip()
        if llm_error:
            logger.error(f"{client_label} LLM generation error: {llm_error[0]}")
        if not full_response:
            return

        logger.info(f"{client_label} LLM response: {full_response}")
        if not for_esp32:
            try:
                await websocket.send_text(json.dumps({"type": "response", "text": full_response}))
            except Exception:
                pass

        try:
            db_service.db_service.log_conversation(
                role="ai", transcript=full_response, session_id=session_id
            )
        except Exception as e:
            logger.error(f"Failed to log AI conversation: {e}")

        if _is_bedtime_story_mode():
            chapter_count = _get_bedtime_auto_chapters()
            if chapter_count > 1:
                for chapter_idx in range(2, chapter_count + 1):
                    if cancel_event.is_set() or not ws_open:
                        break

                    chapter_prompt = (
                        f"[System] Continue with next chapter ({chapter_idx}/{chapter_count}) "
                        "of the same bedtime story. Keep continuity with earlier events. "
                        "No questions, no choices, no interruptions. End softly."
                    )
                    chapter_messages = _build_llm_context(chapter_prompt)
                    chapter_text = await pipeline.generate_response(
                        chapter_prompt,
                        messages=chapter_messages,
                        max_tokens=260,
                        clear_thinking=True if thinking_model else None,
                    )
                    chapter_text = sanitize_spoken_text(
                        chapter_text, allow_paralinguistic=allow_paralinguistic
                    ).strip()
                    if not chapter_text:
                        break

                    logger.info(
                        f"{client_label} Bedtime chapter {chapter_idx}/{chapter_count}: {chapter_text}"
                    )

                    if for_esp32:
                        try:
                            await websocket.send_json(
                                {
                                    "type": "server",
                                    "msg": "RESPONSE.CREATED",
                                    "volume_control": volume,
                                }
                            )
                        except Exception:
                            break

                        opus_packets = []
                        opus = create_opus_packetizer(lambda pkt: opus_packets.append(pkt))
                        async for audio_chunk in pipeline.synthesize_speech(
                            chapter_text,
                            cancel_event,
                            ref_audio_path=ref_audio_path,
                            ref_text=ref_text,
                        ):
                            if cancel_event.is_set() or not ws_open:
                                break
                            chunk_mutable = bytearray(audio_chunk)
                            utils.boost_limit_pcm16le_in_place(
                                chunk_mutable, gain_db=GAIN_DB, ceiling=CEILING
                            )
                            opus.push(chunk_mutable)
                            while opus_packets:
                                try:
                                    await websocket.send_bytes(opus_packets.pop(0))
                                except Exception:
                                    cancel_event.set()
                                    break

                        opus.flush(pad_final_frame=True)
                        while opus_packets:
                            try:
                                await websocket.send_bytes(opus_packets.pop(0))
                            except Exception:
                                break
                        opus.close()
                        try:
                            await websocket.send_json(
                                {"type": "server", "msg": "RESPONSE.COMPLETE"}
                            )
                        except Exception:
                            pass
                    else:
                        try:
                            await websocket.send_text(
                                json.dumps({"type": "response", "text": chapter_text})
                            )
                        except Exception:
                            break
                        async for audio_chunk in pipeline.synthesize_speech(
                            chapter_text,
                            cancel_event,
                            ref_audio_path=ref_audio_path,
                            ref_text=ref_text,
                        ):
                            if cancel_event.is_set() or not ws_open:
                                break
                            try:
                                await websocket.send_text(
                                    json.dumps(
                                        {
                                            "type": "audio",
                                            "data": base64.b64encode(audio_chunk).decode(
                                                "utf-8"
                                            ),
                                        }
                                    )
                                )
                            except Exception:
                                cancel_event.set()
                                break
                        try:
                            await websocket.send_text(json.dumps({"type": "audio_end"}))
                        except Exception:
                            pass

                    try:
                        db_service.db_service.log_conversation(
                            role="ai", transcript=chapter_text, session_id=session_id
                        )
                    except Exception as e:
                        logger.error(f"Failed to log bedtime chapter AI conversation: {e}")

    if _is_bedtime_story_mode():
        bedtime_sequence_task = asyncio.create_task(
            _run_bedtime_autoplay(for_esp32=is_esp32)
        )
        bedtime_disconnect_task = asyncio.create_task(_wait_for_bedtime_disconnect())
        done, pending = await asyncio.wait(
            {bedtime_sequence_task, bedtime_disconnect_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        cancel_event.set()
        for task in pending:
            task.cancel()
            with suppress(Exception):
                await task
        for task in done:
            with suppress(Exception):
                await task
        return

    try:
        while True:
            try:
                message = await websocket.receive()
            except Exception:
                break
            
            if message.get("type") == "websocket.disconnect":
                break
            
            if is_esp32:
                # ESP32: Handle binary audio with VAD
                if "bytes" in message:
                    audio_buffer.extend(message["bytes"])
                    
                    # VAD processing
                    while len(audio_buffer) >= vad_frame_bytes:
                        frame = bytes(audio_buffer[:vad_frame_bytes])
                        audio_buffer = audio_buffer[vad_frame_bytes:]
                        
                        is_speech = vad.is_speech(frame, input_sample_rate)
                        
                        if is_speech:
                            if not is_speaking:
                                is_speaking = True
                                logger.info(f"{client_label} Speech started")
                            speech_frames.append(frame)
                            silence_count = 0
                        elif is_speaking:
                            speech_frames.append(frame)
                            silence_count += 1
                            
                            if silence_count > SILENCE_FRAMES:
                                is_speaking = False
                                logger.info(f"{client_label} Speech ended, processing...")
                                
                                # Combine and transcribe
                                full_audio = b"".join(speech_frames)
                                speech_frames = []
                                silence_count = 0
                                
                                transcription = await pipeline.transcribe(full_audio)
                                await process_transcription_and_respond(transcription, for_esp32=True)
                
                # ESP32: Handle JSON messages (manual end_of_speech, interrupts)
                elif "text" in message:
                    try:
                        data = json.loads(message["text"])
                        msg_type = data.get("type")
                        
                        if msg_type == "instruction":
                            msg = data.get("msg")
                            if msg == "end_of_speech" and speech_frames:
                                # Manual end of speech trigger
                                is_speaking = False
                                full_audio = b"".join(speech_frames)
                                speech_frames = []
                                silence_count = 0
                                transcription = await pipeline.transcribe(full_audio)
                                await process_transcription_and_respond(transcription, for_esp32=True)
                            elif msg == "INTERRUPT":
                                # Cancel current TTS
                                if not _is_bedtime_story_mode():
                                    cancel_event.set()
                                    speech_frames = []
                                    audio_buffer.clear()
                        
                        if "system_prompt" in data:
                            session_system_prompt = data["system_prompt"]
                    except Exception:
                        pass
            else:
                # Desktop: Handle JSON messages
                if "text" in message:
                    try:
                        data = json.loads(message["text"])
                        msg_type = data.get("type")
                        
                        if msg_type == "config":
                            session_voice = data.get("voice", "dave")
                            session_system_prompt = data.get("system_prompt")
                            logger.info(f"Config updated: voice={session_voice}, prompt_len={len(session_system_prompt) if session_system_prompt else 0}")
                        
                        elif msg_type == "audio":
                            audio_data = base64.b64decode(data["data"])
                            audio_buffer.extend(audio_data)

                            # If user is speaking while we're TTS-ing, cancel current TTS
                            if current_tts_task and not current_tts_task.done() and not _is_bedtime_story_mode():
                                cancel_event.set()
                                try:
                                    await current_tts_task
                                except asyncio.CancelledError:
                                    pass
                                cancel_event.clear()
                                current_tts_task = None
                        
                        elif msg_type == "end_of_speech":
                            if audio_buffer:
                                logger.info("Processing audio...")
                                transcription = await pipeline.transcribe(bytes(audio_buffer))
                                audio_buffer.clear()
                                
                                if transcription and transcription.strip():
                                    async def _run_response(text: str):
                                        try:
                                            await process_transcription_and_respond(text, for_esp32=False)
                                        except asyncio.CancelledError:
                                            return
                                        except Exception as e:
                                            logger.error(f"{client_label} Response task error: {e}")
                                            import traceback
                                            traceback.print_exc()
                                    
                                    current_tts_task = asyncio.create_task(_run_response(transcription))
                        
                        elif msg_type == "cancel":
                            if current_tts_task and not current_tts_task.done() and not _is_bedtime_story_mode():
                                cancel_event.set()
                            audio_buffer.clear()
                    except Exception as e:
                        logger.error(f"Error parsing message: {e}")
                        
    except WebSocketDisconnect:
        logger.info(f"{client_label} Disconnected")
    except Exception as e:
        logger.error(f"{client_label} WebSocket error: {e}")
    finally:
        ws_open = False
        if bedtime_sequence_task and not bedtime_sequence_task.done():
            cancel_event.set()
            bedtime_sequence_task.cancel()
            with suppress(Exception):
                await bedtime_sequence_task
        if bedtime_disconnect_task and not bedtime_disconnect_task.done():
            bedtime_disconnect_task.cancel()
            with suppress(Exception):
                await bedtime_disconnect_task
        if current_tts_task and not current_tts_task.done():
            cancel_event.set()
            current_tts_task.cancel()
            with suppress(Exception):
                await current_tts_task
        if is_esp32:
            try:
                status = db_service.db_service.update_esp32_device(
                    {
                        "ws_status": "disconnected",
                        "ws_last_seen": time.time(),
                        "session_id": None,
                    }
                )
                _push_device_event(status)
            except Exception:
                pass
            if ESP32_WS is websocket:
                ESP32_WS = None
                ESP32_SESSION_ID = None
        else:
            manager.disconnect(websocket)
        try:
            db_service.db_service.end_session(session_id)
        except Exception:
            pass
        logger.info(f"{client_label} Session ended: {session_id}")


# Backward compatibility: Keep /ws/esp32 as alias
@app.websocket("/ws/esp32")
async def websocket_esp32_compat(websocket: WebSocket):
    """Backward compatibility endpoint for ESP32. Redirects to unified /ws with esp32 client type."""
    # Call the unified endpoint with ESP32 client type
    await websocket_unified(websocket, client_type=CLIENT_TYPE_ESP32)


def main():
    if len(sys.argv) >= 6 and sys.argv[1:5] == ["-B", "-S", "-I", "-c"]:
        code = sys.argv[5]
        if isinstance(code, str) and code.startswith("from multiprocessing."):
            exec(code, {"__name__": "__main__"})
            return

    parser = argparse.ArgumentParser(description="Voice Pipeline WebSocket Server")
    parser.add_argument(
        "--stt_model",
        type=str,
        default=STT,
        help="STT model",
    )
    parser.add_argument(
        "--llm_model",
        type=str,
        default=LLM,
        help="LLM model",
    )
    # default_ref_audio = os.path.join(os.path.dirname(__file__), "tts", "santa.wav")
    # parser.add_argument(
    #     "--tts_ref_audio",
    #     type=str,
    #     default=default_ref_audio,
    #     help="Reference audio WAV path for voice cloning",
    # )
    parser.add_argument(
        "--silence_duration", type=float, default=1.5, help="Silence duration"
    )
    parser.add_argument(
        "--silence_threshold", type=float, default=0.03, help="Silence threshold"
    )
    parser.add_argument(
        "--streaming_interval", type=float, default=3, help="Streaming interval"
    )
    parser.add_argument(
        "--output_sample_rate",
        type=int,
        default=24_000,
        help="Output sample rate for TTS audio",
    )
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    args = parser.parse_args()

    app.state.stt_model = args.stt_model
    app.state.llm_model = args.llm_model
    # app.state.tts_ref_audio = args.tts_ref_audio
    app.state.silence_threshold = args.silence_threshold
    app.state.silence_duration = args.silence_duration
    app.state.streaming_interval = args.streaming_interval
    app.state.output_sample_rate = args.output_sample_rate
    app.state.server_port = args.port

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
