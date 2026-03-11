import argparse
import asyncio
import base64
import json
import logging
import os
import re
import socket
import sys
import time
import uuid
from contextlib import asynccontextmanager, suppress
from typing import Dict, List, Optional

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")
os.environ.setdefault("HF_XET_DISABLE", "1")
os.environ.setdefault("HF_HUB_DISABLE_HF_XET", "1")

import mlx.core as mx
import uvicorn
import webrtcvad
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

import db_service
import utils
from utils import STT, LLM, create_opus_packetizer, normalize_tts_backend, is_thinking_model, strip_thinking
from engine.characters import build_llm_messages, build_runtime_context, build_system_prompt
from engine.conversation import build_context_history
from engine.prompts import (
    build_behavior_constraints,
    greeting_prompt,
    bedtime_chapter_prompt,
    sanitize_bedtime_chapter,
)
from services import (
    ConnectionManager,
    MdnsService,
    VoicePipeline,
    get_local_ip,
    resolve_voice_ref_audio_path,
    resolve_voice_ref_text,
    sanitize_spoken_text,
)
from routes import router as api_router
from routes.device import push_device_event

CLIENT_TYPE_DESKTOP = "desktop"
CLIENT_TYPE_ESP32 = "esp32"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

GAIN_DB = 7.0
CEILING = 0.89

manager = ConnectionManager()
mdns_service = MdnsService()


def _start_mdns_service(server_port: int) -> None:
    try:
        mdns_service.start(server_port)
    except Exception as exc:
        mdns_service.enabled = False
        try:
            mdns_service.current_ip = get_local_ip()
        except Exception:
            pass
        logger.warning("mDNS start failed: %s", exc)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pipeline_ready = False
    app.state.esp32_ws = None
    app.state.esp32_session_id = None
    app.state.device_watchers = set()

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
    logger.info("Database service active")

    try:
        db_service.db_service.sync_global_voices_and_personalities()
    except Exception as e:
        logger.warning(f"Global assets sync failed: {e}")

    if not hasattr(app.state, "stt_model"):
        app.state.stt_model = STT
    if not hasattr(app.state, "llm_model"):
        app.state.llm_model = db_service.db_service.get_setting("llm_model") or LLM
    if not hasattr(app.state, "tts_backend"):
        stored = db_service.db_service.get_setting("tts_backend")
        if not stored:
            db_service.db_service.set_setting("tts_backend", "qwen3-tts")
        app.state.tts_backend = normalize_tts_backend(stored or "qwen3-tts")
    if not hasattr(app.state, "silence_threshold"):
        app.state.silence_threshold = 0.03
    if not hasattr(app.state, "silence_duration"):
        app.state.silence_duration = 1.5
    if not hasattr(app.state, "streaming_interval"):
        app.state.streaming_interval = 2.0
    if not hasattr(app.state, "output_sample_rate"):
        app.state.output_sample_rate = 24_000

    safe_interval = max(1.5, float(app.state.streaming_interval))

    pipeline = VoicePipeline(
        stt_model=app.state.stt_model,
        llm_model=app.state.llm_model,
        tts_ref_audio=None,
        tts_backend=app.state.tts_backend,
        silence_threshold=app.state.silence_threshold,
        silence_duration=app.state.silence_duration,
        streaming_interval=safe_interval,
        output_sample_rate=app.state.output_sample_rate,
    )
    await pipeline.init_models()
    app.state.pipeline = pipeline
    logger.info("Voice pipeline initialized")
    app.state.pipeline_ready = True
    yield
    logger.info("Shutting down...")
    mdns_service.stop()
    if udp_task:
        udp_task.cancel()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Voice Pipeline WebSocket Server", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)


# --- Network / Events (lightweight, kept here because they use module-level singletons) ---

@app.get("/network-info")
async def network_info():
    return {
        "ip": get_local_ip(),
        "advertising_ip": mdns_service.current_ip,
        "mdns_enabled": mdns_service.enabled,
    }


@app.post("/restart-mdns")
async def restart_mdns():
    server_port = getattr(app.state, "server_port", 8000)
    mdns_service.stop()
    asyncio.create_task(asyncio.to_thread(_start_mdns_service, server_port))
    return {"status": "starting", "ip": mdns_service.current_ip}


@app.get("/startup-status")
async def startup_status():
    voices_n = db_service.db_service.get_table_count("voices")
    personalities_n = db_service.db_service.get_table_count("personalities")
    seeded = bool(getattr(db_service.db_service, "seeded_ok", False))
    pipeline_ready = bool(getattr(app.state, "pipeline_ready", False))
    return {
        "ready": bool(seeded and pipeline_ready),
        "seeded": bool(seeded),
        "pipeline_ready": bool(pipeline_ready),
        "counts": {"voices": voices_n, "personalities": personalities_n},
    }


@app.get("/events/device")
async def device_events():
    async def stream():
        q: asyncio.Queue = asyncio.Queue(maxsize=5)
        app.state.device_watchers.add(q)
        try:
            yield f"data: {json.dumps(db_service.db_service.get_device_status())}\n\n"
            while True:
                data = await q.get()
                yield f"data: {json.dumps(data)}\n\n"
        finally:
            app.state.device_watchers.discard(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no", "Access-Control-Allow-Origin": "*"},
    )


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_unified(websocket: WebSocket, client_type: str = Query(default=CLIENT_TYPE_DESKTOP)):
    header_client = websocket.headers.get("x-client-type", "").lower()
    if header_client in (CLIENT_TYPE_ESP32, CLIENT_TYPE_DESKTOP):
        client_type = header_client

    is_esp32 = client_type == CLIENT_TYPE_ESP32
    label = "[ESP32]" if is_esp32 else "[Desktop]"
    pipeline: VoicePipeline = getattr(app.state, "pipeline", None)

    if is_esp32:
        await websocket.accept()
        app.state.esp32_ws = websocket
    else:
        await manager.connect(websocket)

    if not pipeline:
        await websocket.close()
        return

    llm_repo = getattr(pipeline, "llm_model", "") or ""
    thinking = is_thinking_model(llm_repo)
    session_id = str(uuid.uuid4())
    if is_esp32:
        app.state.esp32_session_id = session_id

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
            pass

    try:
        db_service.db_service.start_session(
            session_id=session_id,
            client_type="device" if is_esp32 else "desktop",
            user_id=user_id, personality_id=personality_id,
        )
    except Exception as e:
        logger.error(f"Failed to start session: {e}")

    if is_esp32:
        try:
            status = db_service.db_service.update_esp32_device(
                {"ws_status": "connected", "ws_last_seen": time.time(), "session_id": session_id}
            )
            push_device_event(app, status)
        except Exception:
            pass

    # --- Helpers ---

    exp_type = getattr(personality, "type", "personality") if personality else "personality"

    def _is_bedtime() -> bool:
        """True only when app mode is 'bedtime' AND the current experience is a story."""
        if exp_type != "story":
            return False
        try:
            return (db_service.db_service.get_app_mode() or "").strip().lower() == "bedtime"
        except Exception:
            return False

    def _build_llm_context(user_text: str) -> List[Dict[str, str]]:
        runtime = build_runtime_context()
        user_ctx = None
        try:
            u = db_service.db_service.get_user(user_id) if user_id else None
            if u:
                user_ctx = {"name": u.name, "age": u.age, "about_you": getattr(u, "about_you", "") or "", "user_type": u.user_type}
        except Exception:
            pass

        tts_be = normalize_tts_backend(getattr(pipeline, "tts_backend", None) or db_service.db_service.get_setting("tts_backend") or "qwen3-tts")

        constraints = build_behavior_constraints(
            tts_backend=tts_be, experience_type=exp_type,
            personality_name=getattr(personality, "name", None),
            is_bedtime=_is_bedtime(), thinking_model=thinking,
        )
        sys_prompt = build_system_prompt(
            personality_name=getattr(personality, "name", None),
            personality_prompt=getattr(personality, "prompt", None),
            user_context=user_ctx, runtime=runtime, extra_system_prompt=constraints,
        )
        history = build_context_history(
            db_service=db_service.db_service, current_session_id=session_id,
            user_id=user_id, personality_id=personality_id,
            max_history_messages=80, max_prior_sessions=6,
        )
        return build_llm_messages(system_prompt=sys_prompt, history=history, user_text=user_text, max_history_messages=80)

    volume = 100
    try:
        raw = db_service.db_service.get_setting("laptop_volume")
        if raw is not None:
            volume = int(raw)
    except Exception:
        pass

    if is_esp32:
        try:
            await websocket.send_json({"type": "auth", "volume_control": volume, "pitch_factor": 1.0, "is_ota": False, "is_reset": False})
        except Exception:
            return
    else:
        try:
            await websocket.send_text(json.dumps({"type": "session_started", "session_id": session_id}))
        except Exception:
            pass

    logger.info(f"{label} Client connected, session={session_id}")

    # --- Audio send helpers ---
    cancel_event = asyncio.Event()
    ws_open = True

    def _voice_refs():
        vid = getattr(personality, "voice_id", None)
        return resolve_voice_ref_audio_path(vid), resolve_voice_ref_text(vid)

    async def _send_audio_esp32(text: str, cancel: asyncio.Event | None = None):
        """Stream TTS audio as Opus packets to ESP32."""
        ref_audio, ref_text = _voice_refs()
        opus_packets: list[bytes] = []
        opus = create_opus_packetizer(lambda pkt: opus_packets.append(pkt))
        try:
            async for chunk in pipeline.synthesize_speech(text, cancel, ref_audio_path=ref_audio, ref_text=ref_text):
                if (cancel and cancel.is_set()) or not ws_open:
                    break
                buf = bytearray(chunk)
                utils.boost_limit_pcm16le_in_place(buf, gain_db=GAIN_DB, ceiling=CEILING)
                opus.push(buf)
                while opus_packets:
                    await websocket.send_bytes(opus_packets.pop(0))
        finally:
            opus.flush(pad_final_frame=True)
            while opus_packets:
                try:
                    await websocket.send_bytes(opus_packets.pop(0))
                except Exception:
                    break
            opus.close()

    async def _send_audio_desktop(text: str, cancel: asyncio.Event | None = None, prebuffer_bytes: int = 0):
        """Stream TTS audio as base64 JSON to desktop."""
        ref_audio, ref_text = _voice_refs()
        buffered = bytearray()
        started = prebuffer_bytes == 0
        async for chunk in pipeline.synthesize_speech(text, cancel, ref_audio_path=ref_audio, ref_text=ref_text):
            if (cancel and cancel.is_set()) or not ws_open:
                break
            if not started:
                buffered.extend(chunk)
                if len(buffered) < prebuffer_bytes:
                    continue
                await websocket.send_text(json.dumps({"type": "audio", "data": base64.b64encode(bytes(buffered)).decode()}))
                buffered.clear()
                started = True
            else:
                await websocket.send_text(json.dumps({"type": "audio", "data": base64.b64encode(chunk).decode()}))
        if buffered:
            await websocket.send_text(json.dumps({"type": "audio", "data": base64.b64encode(bytes(buffered)).decode()}))

    async def _emit_ai_turn(ai_text: str, for_esp32: bool):
        if not ai_text or cancel_event.is_set() or not ws_open:
            return
        if for_esp32:
            try:
                await websocket.send_json({"type": "server", "msg": "RESPONSE.CREATED", "volume_control": volume})
            except Exception:
                cancel_event.set()
                return
            try:
                await _send_audio_esp32(ai_text, cancel_event)
            except Exception:
                cancel_event.set()
            try:
                await websocket.send_json({"type": "server", "msg": "RESPONSE.COMPLETE"})
            except Exception:
                pass
        else:
            try:
                await websocket.send_text(json.dumps({"type": "response", "text": ai_text}))
            except Exception:
                cancel_event.set()
                return
            try:
                await _send_audio_desktop(ai_text, cancel_event)
            except Exception:
                cancel_event.set()
            try:
                await websocket.send_text(json.dumps({"type": "audio_end"}))
            except Exception:
                pass

    async def _emit_pause(for_esp32: bool, seconds: float = 2.0):
        if cancel_event.is_set() or not ws_open or seconds <= 0:
            return
        frame_sec = 0.20
        frame_samples = int(pipeline.output_sample_rate * frame_sec)
        frame_pcm = b"\x00\x00" * frame_samples
        frame_count = max(1, int(round(seconds / frame_sec)))
        if for_esp32:
            opus_packets: list[bytes] = []
            opus = create_opus_packetizer(lambda pkt: opus_packets.append(pkt))
            for _ in range(frame_count):
                if cancel_event.is_set() or not ws_open:
                    break
                opus.push(frame_pcm)
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
        else:
            for _ in range(frame_count):
                if cancel_event.is_set() or not ws_open:
                    break
                try:
                    await websocket.send_text(json.dumps({"type": "audio", "data": base64.b64encode(frame_pcm).decode()}))
                except Exception:
                    cancel_event.set()
                    break

    # --- Greeting ---

    if not _is_bedtime():
        try:
            g_text, g_max = greeting_prompt(exp_type)
            msgs = _build_llm_context(g_text)
            greeting = await pipeline.generate_response(g_text, messages=msgs, max_tokens=g_max, clear_thinking=True if thinking else None)
            greeting = (greeting or "").strip() or "Hello!"
            if thinking:
                greeting = strip_thinking(greeting)
            allow_para = normalize_tts_backend(getattr(pipeline, "tts_backend", None)) == "chatterbox-turbo"
            greeting = sanitize_spoken_text(greeting, allow_paralinguistic=allow_para)
            logger.info(f"{label} Greeting: {greeting}")
            await _emit_ai_turn(greeting, for_esp32=is_esp32)
            try:
                db_service.db_service.log_conversation(role="user", transcript="[connected]", session_id=session_id)
            except Exception:
                pass
            try:
                db_service.db_service.log_conversation(role="ai", transcript=greeting, session_id=session_id)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"{label} Greeting failed: {e}")

    # --- Common state ---

    audio_buffer = bytearray()
    cancel_event = asyncio.Event()
    current_tts_task = None
    session_system_prompt = None
    session_voice = "dave"
    bedtime_sequence_task = None
    bedtime_disconnect_task = None
    PREBUFFER_BYTES = int(pipeline.output_sample_rate * 0.8 * 2)

    if is_esp32:
        vad = webrtcvad.Vad(3)
        vad_frame_ms = 30
        vad_frame_bytes = int(16000 * vad_frame_ms / 1000) * 2
        speech_frames: list[bytes] = []
        is_speaking = False
        silence_count = 0
        SILENCE_FRAMES = int(1.5 / (vad_frame_ms / 1000))

    # --- Streaming response pipeline ---

    def _extract_speakable_chunks(buffer: str, flush: bool = False, soft_limit: int = 140):
        chunks = []
        while True:
            m = re.search(r"(.+?[.!?。！？])(?:\s+|$)", buffer, flags=re.DOTALL)
            if not m:
                break
            chunk = buffer[:m.end(1)].strip()
            buffer = buffer[m.end(1):].lstrip()
            if chunk:
                chunks.append(chunk)
        if not flush and len(buffer) >= soft_limit:
            split_at = buffer.rfind(" ", 0, max(40, soft_limit - 20))
            if split_at <= 0:
                split_at = max(40, soft_limit - 20)
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

    async def process_transcription_and_respond(
        transcription: str,
        for_esp32: bool,
        bedtime_autoplay: bool = False,
        bedtime_chapter_index: int | None = None,
        bedtime_chapter_total: int | None = None,
    ):
        nonlocal cancel_event, ws_open, volume

        if not transcription or not transcription.strip():
            return
        logger.info(f"{label} Transcript: {transcription}")

        if for_esp32 and not bedtime_autoplay:
            try:
                await websocket.send_json({"type": "server", "msg": "AUDIO.COMMITTED"})
            except Exception:
                return
        elif not for_esp32 and not bedtime_autoplay:
            try:
                await websocket.send_text(json.dumps({"type": "transcription", "text": transcription}))
            except Exception:
                return

        cancel_event.clear()
        llm_messages = _build_llm_context(transcription)

        if not bedtime_autoplay:
            try:
                db_service.db_service.log_conversation(role="user", transcript=transcription, session_id=session_id)
            except Exception:
                pass

        allow_para = normalize_tts_backend(getattr(pipeline, "tts_backend", None)) == "chatterbox-turbo"
        tts_be = normalize_tts_backend(getattr(pipeline, "tts_backend", None))
        tts_soft_limit = 260 if tts_be == "qwen3-tts" else 140
        ref_audio, ref_text = _voice_refs()

        if for_esp32 and not bedtime_autoplay:
            try:
                await websocket.send_json({"type": "server", "msg": "RESPONSE.CREATED", "volume_control": volume})
            except Exception:
                return

        text_queue: asyncio.Queue = asyncio.Queue(maxsize=16)
        llm_parts: list[str] = []
        llm_error: list[Exception] = []
        carry = ""

        async def _llm_producer():
            nonlocal carry
            try:
                async for delta in pipeline.stream_response(
                    transcription, messages=llm_messages,
                    clear_thinking=True if thinking else None, cancel_event=cancel_event,
                ):
                    if cancel_event.is_set() or not ws_open:
                        break
                    llm_parts.append(delta)
                    carry += delta
                    ready, carry = _extract_speakable_chunks(carry, flush=False, soft_limit=tts_soft_limit)
                    for chunk in ready:
                        chunk = sanitize_spoken_text(chunk, allow_paralinguistic=allow_para).strip()
                        if chunk:
                            await text_queue.put(chunk)
            except asyncio.CancelledError:
                return
            except Exception as e:
                llm_error.append(e)
            finally:
                if carry and not cancel_event.is_set():
                    ready, _ = _extract_speakable_chunks(carry, flush=True, soft_limit=tts_soft_limit)
                    for chunk in ready:
                        chunk = sanitize_spoken_text(chunk, allow_paralinguistic=allow_para).strip()
                        if chunk:
                            with suppress(asyncio.QueueFull):
                                text_queue.put_nowait(chunk)
                with suppress(asyncio.QueueFull):
                    text_queue.put_nowait(None)

        producer = asyncio.create_task(_llm_producer())

        try:
            if for_esp32:
                opus_packets: list[bytes] = []
                opus = create_opus_packetizer(lambda pkt: opus_packets.append(pkt))
                try:
                    while True:
                        phrase = await text_queue.get()
                        if phrase is None:
                            break
                        async for chunk in pipeline.synthesize_speech(phrase, cancel_event, ref_audio_path=ref_audio, ref_text=ref_text):
                            if cancel_event.is_set() or not ws_open:
                                break
                            buf = bytearray(chunk)
                            utils.boost_limit_pcm16le_in_place(buf, gain_db=GAIN_DB, ceiling=CEILING)
                            opus.push(buf)
                            while opus_packets:
                                try:
                                    await websocket.send_bytes(opus_packets.pop(0))
                                except Exception:
                                    cancel_event.set()
                                    break
                except Exception as e:
                    logger.error(f"{label} TTS stream error (esp32): {e}")
                    cancel_event.set()
                finally:
                    opus.flush(pad_final_frame=True)
                    while opus_packets:
                        try:
                            await websocket.send_bytes(opus_packets.pop(0))
                        except Exception:
                            break
                    opus.close()
                    if not bedtime_autoplay:
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
                        async for chunk in pipeline.synthesize_speech(phrase, cancel_event, ref_audio_path=ref_audio, ref_text=ref_text):
                            if cancel_event.is_set() or not ws_open:
                                break
                            if not started:
                                buffered.extend(chunk)
                                if len(buffered) < PREBUFFER_BYTES:
                                    continue
                                await websocket.send_text(json.dumps({"type": "audio", "data": base64.b64encode(bytes(buffered)).decode()}))
                                buffered.clear()
                                started = True
                            else:
                                await websocket.send_text(json.dumps({"type": "audio", "data": base64.b64encode(chunk).decode()}))
                except Exception as e:
                    logger.error(f"{label} TTS stream error (desktop): {e}")
                    cancel_event.set()
                finally:
                    if buffered:
                        try:
                            await websocket.send_text(json.dumps({"type": "audio", "data": base64.b64encode(bytes(buffered)).decode()}))
                        except Exception:
                            pass
                    try:
                        await websocket.send_text(json.dumps({"type": "audio_end"}))
                    except Exception:
                        pass
        finally:
            if not bedtime_autoplay:
                cancel_event.set()
            if producer and not producer.done():
                producer.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await producer

        full_response = sanitize_spoken_text("".join(llm_parts), allow_paralinguistic=allow_para).strip()
        if bedtime_autoplay and isinstance(bedtime_chapter_index, int) and isinstance(bedtime_chapter_total, int):
            full_response = sanitize_bedtime_chapter(full_response, bedtime_chapter_index, bedtime_chapter_total)
        if llm_error:
            logger.error(f"{label} LLM error: {llm_error[0]}")
        if not full_response:
            return

        logger.info(f"{label} LLM response: {full_response}")
        if not for_esp32:
            try:
                await websocket.send_text(json.dumps({"type": "response", "text": full_response}))
            except Exception:
                pass
        try:
            db_service.db_service.log_conversation(role="ai", transcript=full_response, session_id=session_id)
        except Exception:
            pass

    # --- Bedtime autoplay ---

    async def _run_bedtime_autoplay(for_esp32: bool):
        chapter_count = 5
        if not for_esp32:
            try:
                await websocket.send_text(json.dumps({"type": "bedtime_mode", "mic_enabled": False}))
            except Exception:
                pass
        else:
            try:
                await websocket.send_json({"type": "server", "msg": "RESPONSE.CREATED", "volume_control": volume})
            except Exception:
                return

        async def _sequence():
            for idx in range(1, chapter_count + 1):
                if cancel_event.is_set() or not ws_open:
                    break
                auto_prompt = bedtime_chapter_prompt(idx, chapter_count)
                try:
                    db_service.db_service.log_conversation(role="user", transcript=f"[auto-bedtime] chapter {idx}", session_id=session_id)
                except Exception:
                    pass
                await process_transcription_and_respond(
                    auto_prompt, for_esp32=for_esp32, bedtime_autoplay=True,
                    bedtime_chapter_index=idx, bedtime_chapter_total=chapter_count,
                )
                if idx < chapter_count and not cancel_event.is_set() and ws_open:
                    await _emit_pause(for_esp32=for_esp32, seconds=2.0)

        try:
            await asyncio.wait_for(_sequence(), timeout=600.0)
        except asyncio.TimeoutError:
            timeout_text = "The stars are dim now, and the story is ready to sleep. Goodnight."
            await process_transcription_and_respond(timeout_text, for_esp32=for_esp32, bedtime_autoplay=True)
        finally:
            if for_esp32:
                with suppress(Exception):
                    await websocket.send_json({"type": "server", "msg": "SESSION.END"})
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

    # --- Bedtime branch ---

    if _is_bedtime():
        bedtime_sequence_task = asyncio.create_task(_run_bedtime_autoplay(for_esp32=is_esp32))
        bedtime_disconnect_task = asyncio.create_task(_wait_for_bedtime_disconnect())
        done, pending = await asyncio.wait(
            {bedtime_sequence_task, bedtime_disconnect_task}, return_when=asyncio.FIRST_COMPLETED,
        )
        cancel_event.set()
        for t in pending:
            t.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await t
        return

    # --- Normal message loop ---

    try:
        while True:
            try:
                message = await websocket.receive()
            except Exception:
                break
            if message.get("type") == "websocket.disconnect":
                break

            if is_esp32:
                if "bytes" in message:
                    audio_buffer.extend(message["bytes"])
                    while len(audio_buffer) >= vad_frame_bytes:
                        frame = bytes(audio_buffer[:vad_frame_bytes])
                        audio_buffer = audio_buffer[vad_frame_bytes:]
                        if vad.is_speech(frame, 16000):
                            if not is_speaking:
                                is_speaking = True
                                logger.info(f"{label} Speech started")
                            speech_frames.append(frame)
                            silence_count = 0
                        elif is_speaking:
                            speech_frames.append(frame)
                            silence_count += 1
                            if silence_count > SILENCE_FRAMES:
                                is_speaking = False
                                logger.info(f"{label} Speech ended")
                                transcription = await pipeline.transcribe(b"".join(speech_frames))
                                speech_frames.clear()
                                silence_count = 0
                                await process_transcription_and_respond(transcription, for_esp32=True)
                elif "text" in message:
                    try:
                        data = json.loads(message["text"])
                        if data.get("type") == "instruction":
                            msg = data.get("msg")
                            if msg == "end_of_speech" and speech_frames:
                                is_speaking = False
                                transcription = await pipeline.transcribe(b"".join(speech_frames))
                                speech_frames.clear()
                                silence_count = 0
                                await process_transcription_and_respond(transcription, for_esp32=True)
                            elif msg == "INTERRUPT" and not _is_bedtime():
                                cancel_event.set()
                                speech_frames.clear()
                                audio_buffer.clear()
                        if "system_prompt" in data:
                            session_system_prompt = data["system_prompt"]
                    except Exception:
                        pass
            else:
                if "text" in message:
                    try:
                        data = json.loads(message["text"])
                        mt = data.get("type")
                        if mt == "config":
                            session_voice = data.get("voice", "dave")
                            session_system_prompt = data.get("system_prompt")
                        elif mt == "audio":
                            audio_buffer.extend(base64.b64decode(data["data"]))
                            if current_tts_task and not current_tts_task.done() and not _is_bedtime():
                                cancel_event.set()
                                try:
                                    await current_tts_task
                                except asyncio.CancelledError:
                                    pass
                                cancel_event.clear()
                                current_tts_task = None
                        elif mt == "end_of_speech":
                            if audio_buffer:
                                transcription = await pipeline.transcribe(bytes(audio_buffer))
                                audio_buffer.clear()
                                if transcription and transcription.strip():
                                    async def _run(t=transcription):
                                        try:
                                            await process_transcription_and_respond(t, for_esp32=False)
                                        except asyncio.CancelledError:
                                            pass
                                        except Exception as e:
                                            logger.error(f"{label} Response error: {e}")
                                            import traceback; traceback.print_exc()
                                    current_tts_task = asyncio.create_task(_run())
                        elif mt == "cancel":
                            if current_tts_task and not current_tts_task.done() and not _is_bedtime():
                                cancel_event.set()
                            audio_buffer.clear()
                    except Exception as e:
                        logger.error(f"Error parsing message: {e}")
    except WebSocketDisconnect:
        logger.info(f"{label} Disconnected")
    except Exception as e:
        logger.error(f"{label} WebSocket error: {e}")
    finally:
        ws_open = False
        for task in (bedtime_sequence_task, bedtime_disconnect_task, current_tts_task):
            if task and not task.done():
                cancel_event.set()
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
        if is_esp32:
            try:
                status = db_service.db_service.update_esp32_device(
                    {"ws_status": "disconnected", "ws_last_seen": time.time(), "session_id": None}
                )
                push_device_event(app, status)
            except Exception:
                pass
            if app.state.esp32_ws is websocket:
                app.state.esp32_ws = None
                app.state.esp32_session_id = None
        else:
            manager.disconnect(websocket)
        try:
            db_service.db_service.end_session(session_id)
        except Exception:
            pass
        logger.info(f"{label} Session ended: {session_id}")


@app.websocket("/ws/esp32")
async def websocket_esp32_compat(websocket: WebSocket):
    await websocket_unified(websocket, client_type=CLIENT_TYPE_ESP32)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) >= 6 and sys.argv[1:5] == ["-B", "-S", "-I", "-c"]:
        code = sys.argv[5]
        if isinstance(code, str) and code.startswith("from multiprocessing."):
            exec(code, {"__name__": "__main__"})
            return

    parser = argparse.ArgumentParser(description="Voice Pipeline WebSocket Server")
    parser.add_argument("--stt_model", type=str, default=STT)
    parser.add_argument("--llm_model", type=str, default=LLM)
    parser.add_argument("--silence_duration", type=float, default=1.5)
    parser.add_argument("--silence_threshold", type=float, default=0.03)
    parser.add_argument("--streaming_interval", type=float, default=2.0)
    parser.add_argument("--output_sample_rate", type=int, default=24_000)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    app.state.stt_model = args.stt_model
    app.state.llm_model = args.llm_model
    app.state.silence_threshold = args.silence_threshold
    app.state.silence_duration = args.silence_duration
    app.state.streaming_interval = args.streaming_interval
    app.state.output_sample_rate = args.output_sample_rate
    app.state.server_port = args.port

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
