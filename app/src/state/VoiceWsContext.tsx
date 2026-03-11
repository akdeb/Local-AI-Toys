import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import { convertFileSrc } from "@tauri-apps/api/core";
import { api } from "../api";
import { useActiveUser } from "./ActiveUserContext";

const API_BASE = (import.meta as any).env?.VITE_API_BASE || "http://127.0.0.1:8000";

type VoiceMsg =
  | { type: "transcription"; text: string }
  | { type: "response"; text: string }
  | { type: "audio"; data: string }
  | { type: "audio_end" }
  | { type: "error"; message: string }
  | { type: "session_started"; session_id: string };

type Status = "disconnected" | "connecting" | "connected" | "error";

type TranscriptEntry = {
  id: string;
  role: "user" | "ai";
  text: string;
  timestamp: number;
};

type Ctx = {
  status: Status;
  error: string | null;
  characterName: string;
  characterId: string | null;
  characterImageSrc: string | null;
  connect: () => void;
  disconnect: () => void;
  isActive: boolean;
  latestSessionId: string | null;
  isRecording: boolean;
  isPaused: boolean;
  isSpeaking: boolean;
  micLevel: number;
  isBedtimeMode: boolean;
  transcript: TranscriptEntry[];
};

const VoiceWsContext = createContext<Ctx | null>(null);

export const useVoiceWs = () => {
  const ctx = useContext(VoiceWsContext);
  if (!ctx) throw new Error("VoiceWsContext missing");
  return ctx;
};

export const VoiceWsProvider = ({ children }: { children: React.ReactNode }) => {
  const { activeUser } = useActiveUser();

  const [status, setStatus] = useState<Status>("disconnected");
  const [error, setError] = useState<string | null>(null);
  const [voice, setVoice] = useState<string>("dave");
  const [systemPrompt, setSystemPrompt] = useState<string>("You are a helpful voice assistant. Be concise.");
  const [characterName, setCharacterName] = useState<string>("—");
  const [characterId, setCharacterId] = useState<string | null>(null);
  const [characterImageSrc, setCharacterImageSrc] = useState<string | null>(null);
  const [configReady, setConfigReady] = useState<boolean>(false);

  const [isRecording, setIsRecording] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [micLevel, setMicLevel] = useState<number>(0);
  const [isBedtimeMode, setIsBedtimeMode] = useState(false);
  const lastLevelAtRef = useRef<number>(0);
  const isBedtimeModeRef = useRef(false);

  const [latestSessionId, setLatestSessionId] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);

  const isRecordingRef = useRef(false);
  const isPausedRef = useRef(false);
  const vadSilenceFramesRef = useRef(0);
  const vadIsSpeechActiveRef = useRef(false);
  const autoStartedMicRef = useRef(false);
  const awaitingResumeRef = useRef(false);

  const wsRef = useRef<WebSocket | null>(null);
  const connectNonceRef = useRef(0);
  const connectTimeoutRef = useRef<number | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);

  const ttsSampleRate = 24000;
  const ttsAudioCtxRef = useRef<AudioContext | null>(null);
  const ttsPcmQueueRef = useRef<Int16Array[]>([]);
  const ttsScriptNodeRef = useRef<ScriptProcessorNode | null>(null);
  const ttsPlaybackActiveRef = useRef(false);
  const ttsCurrentChunkRef = useRef<Int16Array | null>(null);
  const ttsCurrentChunkOffsetRef = useRef(0);

  const wsUrl = useMemo(() => {
    const base = API_BASE.replace("localhost", "127.0.0.1");
    const u = new URL(base);
    u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
    u.pathname = "/ws";
    u.searchParams.set("client_type", "desktop");
    return u.toString();
  }, []);

  const GLOBAL_PERSONALITY_IMAGE_BASE_URL = "https://pub-a64cd21521e44c81a85db631f1cdaacc.r2.dev";

  const imageSrcForPersonality = (p: any) => {
    if (p?.is_global) {
      const personalityId = p?.id != null ? String(p.id) : "";
      if (!personalityId) return null;
      return `${GLOBAL_PERSONALITY_IMAGE_BASE_URL}/${encodeURIComponent(personalityId)}.png`;
    }
    const src = typeof p?.img_src === "string" ? p.img_src.trim() : "";
    if (!src) return null;
    if (/^https?:\/\//i.test(src)) return src;
    return convertFileSrc(src);
  };

  const base64EncodeBytes = (bytes: Uint8Array) => {
    const CHUNK = 0x8000;
    let binary = "";
    for (let i = 0; i < bytes.length; i += CHUNK) {
      const slice = bytes.subarray(i, Math.min(i + CHUNK, bytes.length));
      binary += String.fromCharCode(...slice);
    }
    return window.btoa(binary);
  };

  const stopTtsPlayback = () => {
    if (ttsScriptNodeRef.current) {
      try {
        ttsScriptNodeRef.current.disconnect();
      } catch {
        // ignore
      }
      ttsScriptNodeRef.current = null;
    }
    if (ttsAudioCtxRef.current) {
      try {
        ttsAudioCtxRef.current.close();
      } catch {
        // ignore
      }
      ttsAudioCtxRef.current = null;
    }
    ttsCurrentChunkRef.current = null;
    ttsCurrentChunkOffsetRef.current = 0;
    ttsPcmQueueRef.current = [];
    ttsPlaybackActiveRef.current = false;
    setIsSpeaking(false);
  };

  const resumeMic = () => {
    isPausedRef.current = false;
    setIsPaused(false);
    setMicLevel(0);
  };

  const startTtsPlayback = () => {
    if (ttsPlaybackActiveRef.current) return;

    const ctx = new AudioContext({ sampleRate: ttsSampleRate });
    ttsAudioCtxRef.current = ctx;

    if (ctx.state === "suspended") {
      void ctx.resume();
    }

    const bufferSize = 4096;
    const scriptNode = ctx.createScriptProcessor(bufferSize, 1, 1);
    ttsScriptNodeRef.current = scriptNode;

    scriptNode.onaudioprocess = (e) => {
      const output = e.outputBuffer.getChannelData(0);
      let outIdx = 0;

      while (outIdx < output.length) {
        if (!ttsCurrentChunkRef.current || ttsCurrentChunkOffsetRef.current >= ttsCurrentChunkRef.current.length) {
          const next = ttsPcmQueueRef.current.shift();
          if (!next) {
            while (outIdx < output.length) {
              output[outIdx++] = 0;
            }
            if (awaitingResumeRef.current && ttsPcmQueueRef.current.length === 0) {
              setTimeout(() => {
                stopTtsPlayback();
                awaitingResumeRef.current = false;
                // Start recording if not already started (first time after greeting)
                if (!autoStartedMicRef.current) {
                  autoStartedMicRef.current = true;
                  void startRecording();
                } else {
                  resumeMic();
                }
              }, (bufferSize / ttsSampleRate) * 1000 + 50);
            }
            break;
          }
          ttsCurrentChunkRef.current = next;
          ttsCurrentChunkOffsetRef.current = 0;
        }

        const chunk = ttsCurrentChunkRef.current!;
        const remaining = chunk.length - ttsCurrentChunkOffsetRef.current;
        const needed = output.length - outIdx;
        const toCopy = Math.min(remaining, needed);

        for (let i = 0; i < toCopy; i++) {
          output[outIdx++] = chunk[ttsCurrentChunkOffsetRef.current++] / 32768;
        }
      }
    };

    scriptNode.connect(ctx.destination);
    ttsPlaybackActiveRef.current = true;
    setIsSpeaking(true);
  };

  const enqueueTtsChunk = (base64Pcm: string) => {
    const binary = window.atob(base64Pcm);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const int16 = new Int16Array(bytes.buffer, bytes.byteOffset, Math.floor(bytes.byteLength / 2));

    ttsPcmQueueRef.current.push(int16);

    if (!ttsPlaybackActiveRef.current) {
      startTtsPlayback();
    }
  };

  const stopRecording = () => {
    isRecordingRef.current = false;
    isPausedRef.current = false;
    setIsRecording(false);
    setIsPaused(false);
    setMicLevel(0);

    try {
      processorRef.current?.disconnect();
    } catch {
      // ignore
    }
    try {
      sourceRef.current?.disconnect();
    } catch {
      // ignore
    }
    try {
      audioCtxRef.current?.close();
    } catch {
      // ignore
    }
    audioCtxRef.current = null;
    processorRef.current = null;
    sourceRef.current = null;

    try {
      mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
    } catch {
      // ignore
    }
    mediaStreamRef.current = null;
  };

  const applyBedtimeMode = (next: boolean) => {
    isBedtimeModeRef.current = next;
    setIsBedtimeMode(next);
    if (next && isRecordingRef.current) {
      stopRecording();
    }
  };

  const reconnectForModeChange = () => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    disconnect();
    window.setTimeout(() => {
      connect();
    }, 120);
  };

  const startRecording = async () => {
    if (isBedtimeModeRef.current) {
      stopRecording();
      return;
    }
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setError("Voice WebSocket is not connected");
      return;
    }

    setError(null);

    if (isPausedRef.current) {
      setError("Mic is paused while the assistant is speaking");
      return;
    }

    if (!navigator.mediaDevices?.getUserMedia) {
      setError("Microphone access requires a secure context (HTTPS) or localhost");
      return;
    }

    let mediaStream: MediaStream;
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
    } catch (e: any) {
      const name = String(e?.name || "");
      if (name === "NotAllowedError" || name === "SecurityError") {
        setError("Microphone access was denied. Enable microphone access for OpenToys and try again.");
      } else if (name === "NotFoundError" || name === "DevicesNotFoundError") {
        setError("No microphone was found. Connect a microphone and try again.");
      } else if (name === "NotReadableError" || name === "TrackStartError") {
        setError("Microphone is unavailable because another app is using it.");
      } else {
        setError(e?.message || "Failed to access microphone");
      }
      return;
    }
    mediaStreamRef.current = mediaStream;

    const audioCtx = new AudioContext();
    audioCtxRef.current = audioCtx;

    const source = audioCtx.createMediaStreamSource(mediaStream);
    sourceRef.current = source;

    const processor = audioCtx.createScriptProcessor(4096, 1, 1);
    processorRef.current = processor;

    const actualRate = audioCtx.sampleRate;

    vadSilenceFramesRef.current = 0;
    vadIsSpeechActiveRef.current = false;
    const SILENCE_THRESHOLD_FRAMES = 10;
    const RMS_THRESHOLD = 0.015;

    let utteranceBytes: number[] = [];

    processor.onaudioprocess = (e) => {
      const socket = wsRef.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      if (!isRecordingRef.current) return;
      if (isPausedRef.current) return;

      const input = e.inputBuffer.getChannelData(0);

      let sumSq = 0;
      for (let i = 0; i < input.length; i++) {
        const v = input[i] || 0;
        sumSq += v * v;
      }
      const rms = Math.sqrt(sumSq / Math.max(1, input.length));

      const now = performance.now();
      if (now - lastLevelAtRef.current > 80) {
        lastLevelAtRef.current = now;
        setMicLevel(Math.min(1, rms * 6));
      }

      if (rms > RMS_THRESHOLD) {
        vadSilenceFramesRef.current = 0;
        if (!vadIsSpeechActiveRef.current) {
          vadIsSpeechActiveRef.current = true;
        }
      } else {
        if (vadIsSpeechActiveRef.current) {
          vadSilenceFramesRef.current++;
        }
      }

      const ratio = 16000 / actualRate;
      const outputLen = Math.round(input.length * ratio);
      const resampled = new Float32Array(outputLen);
      for (let i = 0; i < outputLen; i++) {
        const srcIdx = i / ratio;
        const idx0 = Math.floor(srcIdx);
        const idx1 = Math.min(idx0 + 1, input.length - 1);
        const frac = srcIdx - idx0;
        resampled[i] = input[idx0] * (1 - frac) + input[idx1] * frac;
      }

      const pcm = new Int16Array(resampled.length);
      for (let i = 0; i < resampled.length; i++) {
        pcm[i] = Math.max(-32768, Math.min(32767, resampled[i] * 32768));
      }

      if (vadIsSpeechActiveRef.current) {
        const bytes = new Uint8Array(pcm.buffer);
        for (let i = 0; i < bytes.length; i++) utteranceBytes.push(bytes[i]);
      }

      if (vadIsSpeechActiveRef.current && vadSilenceFramesRef.current > SILENCE_THRESHOLD_FRAMES) {
        isPausedRef.current = true;
        setIsPaused(true);
        setMicLevel(0);

        const base64Data = base64EncodeBytes(new Uint8Array(utteranceBytes));
        utteranceBytes = [];
        try {
          socket.send(JSON.stringify({ type: "audio", data: base64Data }));
          socket.send(JSON.stringify({ type: "end_of_speech" }));
        } catch {
          // ignore
        }

        vadIsSpeechActiveRef.current = false;
        vadSilenceFramesRef.current = 0;
      }
    };

    source.connect(processor);
    processor.connect(audioCtx.destination);
    isRecordingRef.current = true;
    setIsRecording(true);
  };


  const disconnect = () => {
    if (connectTimeoutRef.current) {
      window.clearTimeout(connectTimeoutRef.current);
      connectTimeoutRef.current = null;
    }
    stopRecording();
    try {
      wsRef.current?.close(1000);
    } catch {
      // ignore
    }
    wsRef.current = null;
    setStatus("disconnected");
    stopTtsPlayback();
    awaitingResumeRef.current = false;
    autoStartedMicRef.current = false;
    resumeMic();
    setTranscript([]);
  };

  const connect = () => {
    if (status === "connected" || status === "connecting") return;

    connectNonceRef.current += 1;
    const nonce = connectNonceRef.current;

    setTranscript([]);
    try {
      wsRef.current?.close();
    } catch {
      // ignore
    }

    if (connectTimeoutRef.current) {
      window.clearTimeout(connectTimeoutRef.current);
      connectTimeoutRef.current = null;
    }

    setError(null);
    setStatus("connecting");

    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl);
    } catch (e: any) {
      setStatus("error");
      setError(e?.message || `Failed to create WebSocket: ${wsUrl}`);
      return;
    }

    wsRef.current = ws;

    connectTimeoutRef.current = window.setTimeout(() => {
      if (nonce !== connectNonceRef.current) return;
      if (ws.readyState === WebSocket.OPEN) return;
      try {
        ws.close();
      } catch {
        // ignore
      }
      setStatus("error");
      setError(`Can't connect to voice server (${wsUrl}). Is the Python sidecar running on port 8000?`);
    }, 6000);

    ws.onopen = () => {
      if (nonce !== connectNonceRef.current) return;
      if (connectTimeoutRef.current) {
        window.clearTimeout(connectTimeoutRef.current);
        connectTimeoutRef.current = null;
      }
      setStatus("connected");
      try {
        ws.send(JSON.stringify({ type: "config", voice, system_prompt: systemPrompt }));
      } catch {
        // ignore
      }
      // Don't start recording immediately - wait for greeting to finish (audio_end)
      // The server will send a greeting first, then we start listening
    };

    ws.onclose = (ev) => {
      if (nonce !== connectNonceRef.current) return;
      if (connectTimeoutRef.current) {
        window.clearTimeout(connectTimeoutRef.current);
        connectTimeoutRef.current = null;
      }
      setStatus("disconnected");
      if (ev?.code && ev.code !== 1000) {
        setError(`Voice WebSocket closed (code=${ev.code}${ev.reason ? `, reason=${ev.reason}` : ""}).`);
      }
      stopRecording();
    };

    ws.onerror = () => {
      if (nonce !== connectNonceRef.current) return;
      if (connectTimeoutRef.current) {
        window.clearTimeout(connectTimeoutRef.current);
        connectTimeoutRef.current = null;
      }
      setStatus("error");
      setError(`Voice WebSocket error (${wsUrl}).`);
      stopRecording();
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as VoiceMsg;

        if (msg.type === "audio") {
          if (msg.data) {
            enqueueTtsChunk(msg.data);
          }
        } else if (msg.type === "audio_end") {
          if (isBedtimeModeRef.current) {
            awaitingResumeRef.current = false;
            stopRecording();
            return;
          }
          awaitingResumeRef.current = true;
          if (!ttsPlaybackActiveRef.current && ttsPcmQueueRef.current.length === 0) {
            awaitingResumeRef.current = false;
            // Start recording if not already started (first time after greeting)
            if (!autoStartedMicRef.current) {
              autoStartedMicRef.current = true;
              void startRecording();
            } else {
              resumeMic();
            }
          }
        } else if (msg.type === "session_started") {
          setLatestSessionId(msg.session_id || null);
          setTranscript([]);
        } else if (msg.type === "transcription") {
          if (msg.text) {
            const entry: TranscriptEntry = {
              id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
              role: "user",
              text: msg.text,
              timestamp: Date.now(),
            };
            setTranscript((prev) => {
              const next = [...prev, entry];
              return next.length > 200 ? next.slice(-200) : next;
            });
          }
        } else if (msg.type === "response") {
          if (msg.text) {
            const entry: TranscriptEntry = {
              id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
              role: "ai",
              text: msg.text,
              timestamp: Date.now(),
            };
            setTranscript((prev) => {
              const next = [...prev, entry];
              return next.length > 200 ? next.slice(-200) : next;
            });
          }
        } else if (msg.type === "error") {
          setError(msg.message || "Unknown error");
        } else if ((msg as any).type === "bedtime_mode") {
          const micEnabled = Boolean((msg as any).mic_enabled);
          applyBedtimeMode(!micEnabled);
        }
      } catch {
        // ignore
      }
    };
  };

  useEffect(() => {
    let cancelled = false;
    const loadMode = async () => {
      try {
        const res = await api.getAppMode();
        if (!cancelled) {
          const bedtime = (res?.mode || "").toLowerCase() === "bedtime";
          const prev = isBedtimeModeRef.current;
          applyBedtimeMode(bedtime);
          if (prev !== bedtime) {
            reconnectForModeChange();
          }
        }
      } catch {
        if (!cancelled) applyBedtimeMode(false);
      }
    };

    const onModeChanged = (ev: Event) => {
      const detail = (ev as CustomEvent<{ mode?: string }>).detail;
      const bedtime = (detail?.mode || "").toLowerCase() === "bedtime";
      const prev = isBedtimeModeRef.current;
      applyBedtimeMode(bedtime);
      if (prev !== bedtime) {
        reconnectForModeChange();
      }
    };

    loadMode();
    window.addEventListener("app-mode-changed", onModeChanged as EventListener);
    return () => {
      cancelled = true;
      window.removeEventListener("app-mode-changed", onModeChanged as EventListener);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        if (!cancelled) setConfigReady(false);
        const ps = await api.getPersonalities(true);
        const selectedId = activeUser?.current_personality_id;
        const selected = ps.find((p: any) => p.id === selectedId);
        if (!cancelled && selected) {
          setCharacterName(selected.name || "—");
          setCharacterId(selected.id ? String(selected.id) : null);
          setVoice(selected.voice_id || "dave");
          setSystemPrompt(selected.prompt || "You are a helpful voice assistant. Be concise.");
          setCharacterImageSrc(imageSrcForPersonality(selected));
        } else if (!cancelled) {
          setCharacterImageSrc(null);
          setCharacterId(null);
        }
      } catch {
        // ignore
        if (!cancelled) {
          setCharacterImageSrc(null);
          setCharacterId(null);
        }
      } finally {
        if (!cancelled) setConfigReady(true);
      }
    };

    load();
  }, [activeUser?.current_personality_id]);

  useEffect(() => {
    if (!configReady) return;
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(JSON.stringify({ type: "config", voice, system_prompt: systemPrompt }));
    } catch {
      // ignore
    }
  }, [voice, systemPrompt, configReady]);

  useEffect(() => {
    return () => {
      if (connectTimeoutRef.current) {
        window.clearTimeout(connectTimeoutRef.current);
        connectTimeoutRef.current = null;
      }
      disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value: Ctx = useMemo(
    () => ({
      status,
      error,
      characterName,
      characterId,
      characterImageSrc,
      connect,
      disconnect,
      isActive: status === "connected" || status === "connecting",
      latestSessionId,
      isRecording,
      isPaused,
      isSpeaking,
      micLevel,
      isBedtimeMode,
      transcript,
    }),
    [
      status,
      error,
      characterName,
      characterId,
      characterImageSrc,
      latestSessionId,
      isRecording,
      isPaused,
      isSpeaking,
      micLevel,
      isBedtimeMode,
      transcript,
    ]
  );

  return <VoiceWsContext.Provider value={value}>{children}</VoiceWsContext.Provider>;
};
