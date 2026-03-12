import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ChatTranscript } from "../components/ChatTranscript";
import { useVoiceWs } from "../state/VoiceWsContext";
import { api } from "../api";

type TranscriptEntry = {
  id: string;
  role: "user" | "ai";
  text: string;
  timestamp: number;
};

export const TestPage = () => {
  const voiceWs = useVoiceWs();
  const [imageError, setImageError] = useState(false);
  const [searchParams] = useSearchParams();
  const viewOnly = searchParams.get("view") === "esp32";
  const [deviceConnected, setDeviceConnected] = useState(false);
  const [deviceSessionId, setDeviceSessionId] = useState<string | null>(null);
  const [deviceTranscript, setDeviceTranscript] = useState<TranscriptEntry[]>([]);

  useEffect(() => {
    if (viewOnly) return;
    if (!voiceWs.isActive) {
      voiceWs.connect();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewOnly]);

  useEffect(() => {
    setImageError(false);
  }, [voiceWs.characterImageSrc]);

  useEffect(() => {
    if (!viewOnly) return;
    let cancelled = false;
    let retryTimer: number | null = null;
    let es: EventSource | null = null;
    const base = (import.meta as any).env?.VITE_API_BASE || "http://127.0.0.1:8000";
    const url = base.replace("localhost", "127.0.0.1").replace(/\/+$/, "");

    const start = () => {
      if (cancelled) return;
      try {
        es = new EventSource(`${url}/events/device`);
      } catch {
        retryTimer = window.setTimeout(start, 3000);
        return;
      }

      es.onmessage = (ev) => {
        if (cancelled) return;
        try {
          const data = JSON.parse(ev.data || "{}");
          setDeviceConnected(data?.ws_status === "connected");
          setDeviceSessionId(data?.session_id || null);
        } catch {
          // ignore
        }
      };

      es.onerror = () => {
        if (cancelled) return;
        try {
          es?.close();
        } catch {
          // ignore
        }
        es = null;
        if (retryTimer == null) {
          retryTimer = window.setTimeout(start, 3000);
        }
      };
    };

    start();
    return () => {
      cancelled = true;
      if (retryTimer != null) window.clearTimeout(retryTimer);
      try {
        es?.close();
      } catch {
        // ignore
      }
      es = null;
    };
  }, [viewOnly]);

  useEffect(() => {
    if (!viewOnly || !deviceSessionId) {
      setDeviceTranscript([]);
      return;
    }
    let cancelled = false;
    let timer: number | null = null;
    const poll = async () => {
      try {
        const rows = await api.getConversationsBySession(deviceSessionId).catch(() => []);
        if (!cancelled) {
          const next = (Array.isArray(rows) ? rows : []).map((c: any) => {
            const ts =
              typeof c?.timestamp === "number"
                ? c.timestamp
                : Date.parse(c?.timestamp || "") || Date.now();
            return {
              id: String(c?.id ?? `${ts}-${Math.random().toString(16).slice(2)}`),
              role: c?.role === "ai" ? "ai" : "user",
              text: String(c?.transcript ?? ""),
              timestamp: ts,
            } as TranscriptEntry;
          });
          setDeviceTranscript(next);
        }
      } catch {
        // ignore
      }
      if (!cancelled) {
        timer = window.setTimeout(poll, 2000);
      }
    };
    poll();
    return () => {
      cancelled = true;
      if (timer != null) window.clearTimeout(timer);
    };
  }, [viewOnly, deviceSessionId]);

  const effectiveStatus = viewOnly ? (deviceConnected ? "connected" : "disconnected") : voiceWs.status;
  const statusDotClass =
    effectiveStatus === "connected"
      ? "bg-[#00c853]"
      : effectiveStatus === "error"
        ? "bg-red-500"
        : "bg-[#ffd400]";

  const micStatusLabel = useMemo(() => {
    if (viewOnly) return null;
    if (voiceWs.isBedtimeMode) return "bedtime story";
    if (voiceWs.isSpeaking) return "speaking";
    if (!voiceWs.isRecording) return voiceWs.status === "connected" ? "waiting" : null;
    if (voiceWs.isPaused) return "processing";
    return "listening";
  }, [voiceWs.isBedtimeMode, voiceWs.isRecording, voiceWs.isPaused, voiceWs.isSpeaking, voiceWs.status, viewOnly]);

  const orbScale = useMemo(() => {
    if (viewOnly) return 1;
    if (voiceWs.isBedtimeMode) return voiceWs.isSpeaking ? 1.06 : 1;
    const base = voiceWs.isRecording ? 1.03 : 1;
    const mic = voiceWs.isRecording && !voiceWs.isPaused ? voiceWs.micLevel * 0.18 : 0;
    const speak = voiceWs.isSpeaking ? 0.08 : 0;
    return base + mic + speak;
  }, [voiceWs.isBedtimeMode, voiceWs.isRecording, voiceWs.isPaused, voiceWs.micLevel, voiceWs.isSpeaking, viewOnly]);

  const displayTranscript = viewOnly ? deviceTranscript : voiceWs.transcript;

  return (
    <div className="live-page -mt-8">
      <div className="sticky top-0 z-30 pt-6 pb-4">
        <div className="live-header-surface flex justify-between items-start gap-6 rounded-[28px] border border-gray-200 bg-white px-6 py-6 shadow-[0_12px_30px_rgba(0,0,0,0.08)]">
          <div>
            <h2 className="text-3xl font-black live-title">LIVE</h2>
            <div className="mt-2 font-mono text-xs text-gray-600 live-meta">
              Character: <span className="font-bold text-black">{voiceWs.characterName}</span>
            </div>
            <div className="mt-1 font-mono text-xs text-gray-600 inline-flex items-center gap-2 live-meta">
              <span className={`w-2.5 h-2.5 rounded-full border border-black ${statusDotClass}`} />
              <span className="capitalize">{effectiveStatus}</span>
              {micStatusLabel && (
                <span className="text-gray-500">
                  • {micStatusLabel}
                </span>
              )}
            </div>
            {!viewOnly && voiceWs.error && (
              <div className="mt-3 font-mono text-xs text-red-700">{voiceWs.error}</div>
            )}
          </div>

        <div className="flex flex-col items-center">
            <div
              className="rounded-full shadow-[0_14px_30px_rgba(0,0,0,0.18)] transition-shadow"
              aria-hidden
              style={{
              width: 96,
              height: 96,
                transform: `scale(${orbScale})`,
                transition: "transform 80ms linear",
                opacity: voiceWs.status === "connected" ? 1 : 0.7,
              }}
            >
              {voiceWs.characterImageSrc && !imageError ? (
                <img
                  src={voiceWs.characterImageSrc}
                  alt=""
                  className="w-full h-full rounded-full border border-gray-500 object-cover bg-white live-orb-image"
                  onError={() => setImageError(true)}
                />
              ) : (
                <div className="w-full h-full rounded-full border border-gray-500 bg-orange-50/50" style={{
                            backgroundImage: `radial-gradient(circle, rgba(0,0,0,0.08) 1px, transparent 1px)`,
                            backgroundSize: '6px 6px'
                        }} />
              )}
            </div>

            <div className="mt-3 font-mono text-xs text-gray-600 text-center">
              {effectiveStatus === "connecting" && "Connecting…"}
              {effectiveStatus === "error" && "WebSocket error"}
              {effectiveStatus === "disconnected" && "Disconnected"}
              {effectiveStatus === "connected" && "Live"}
            </div>
          </div>
        </div>
      </div>

      <div className="space-y-3 pt-6 pb-4">
        <ChatTranscript
          messages={displayTranscript}
          isLive
          autoScroll
          scrollMarginTop={200}
        />
      </div>
    </div>
  );
};
