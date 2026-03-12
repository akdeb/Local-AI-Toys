import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Plus } from "lucide-react";
import { ExperienceModal } from "../components/ExperienceModal";
import { VoiceActionButtons } from "../components/VoiceActionButtons";
import { useVoicePlayback } from "../hooks/useVoicePlayback";
import { VoiceClone } from "../components/VoiceClone";

export const VoicesPage = () => {
  const navigate = useNavigate();
  const [voices, setVoices] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [downloadingVoiceId, setDownloadingVoiceId] = useState<string | null>(null);
  const [downloadedVoiceIds, setDownloadedVoiceIds] = useState<Set<string>>(new Set());
  const [audioSrcByVoiceId, setAudioSrcByVoiceId] = useState<Record<string, string>>({});
  const [searchParams, setSearchParams] = useSearchParams();

  const toTimestamp = (v: any) => {
    if (v == null) return 0;
    if (typeof v === "number") return Number.isFinite(v) ? v : 0;
    if (typeof v === "string") {
      const asNum = Number(v);
      if (Number.isFinite(asNum)) return asNum;
      const ms = Date.parse(v);
      if (Number.isFinite(ms)) return Math.floor(ms / 1000);
    }
    return 0;
  };

  const [createVoiceOpen, setCreateVoiceOpen] = useState(false);

  const [createStartOpen, setCreateStartOpen] = useState(false);
  const [createExperienceType, setCreateExperienceType] = useState<"personality" | "game" | "story">("personality");
  const [createExperienceVoiceId, setCreateExperienceVoiceId] = useState<string | null>(null);
  const [createExperienceVoiceName, setCreateExperienceVoiceName] = useState<string | null>(null);

  const sortedVoices = useMemo(() => {
    const arr = Array.isArray(voices) ? voices.slice() : [];
    arr.sort((a, b) => {
      const aG = Boolean(a?.is_global);
      const bG = Boolean(b?.is_global);
      if (aG !== bG) return aG ? 1 : -1;
      const aT = toTimestamp(a?.created_at);
      const bT = toTimestamp(b?.created_at);
      if (aT !== bT) return bT - aT;
      return 0;
    });
    return arr;
  }, [voices]);


  const selectedVoiceId = useMemo(() => {
    const v = searchParams.get("voice_id");
    return v ? String(v) : null;
  }, [searchParams]);

  const selectedRef = useRef<HTMLDivElement | null>(null);
  const { playingVoiceId, isPaused, toggle: toggleVoice } = useVoicePlayback(async (voiceId) => {
    let src = audioSrcByVoiceId[voiceId];
    if (!src) {
      const b64 = await api.readVoiceBase64(voiceId);
      if (!b64) return null;
      src = `data:audio/wav;base64,${b64}`;
      setAudioSrcByVoiceId((prev) => ({ ...prev, [voiceId]: src! }));
    }
    return src;
  });

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        setError(null);
        const data = await api.getVoices();
        if (!cancelled) setVoices(Array.isArray(data) ? data : []);
      } catch (e: any) {
        if (!cancelled) setError(e?.message || "Failed to load voices");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    const loadAll = async () => {
      await load();
      try {
        const ids = await api.listDownloadedVoices();
        if (!cancelled) setDownloadedVoiceIds(new Set(Array.isArray(ids) ? ids : []));
      } catch {
        if (!cancelled) setDownloadedVoiceIds(new Set());
      }
    };

    loadAll();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const create = searchParams.get("create");
    if (create !== "voice") return;
    setCreateVoiceOpen(true);
    const next = new URLSearchParams(searchParams);
    next.delete("create");
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);


  useEffect(() => {
    if (!selectedVoiceId) return;
    if (!selectedRef.current) return;
    selectedRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [selectedVoiceId, voices.length]);

  const downloadVoice = async (voiceId: string) => {
    setDownloadingVoiceId(voiceId);
    try {
      await api.downloadVoice(voiceId);
      setDownloadedVoiceIds((prev) => {
        const next = new Set(prev);
        next.add(voiceId);
        return next;
      });
      try {
        window.dispatchEvent(new CustomEvent('voice:downloaded', { detail: { voiceId } }));
      } catch {
        // ignore
      }
    } catch (e: any) {
      console.error("download_voice failed", e);
      const msg =
        typeof e === "string"
          ? e
          : e?.message
            ? String(e.message)
            : e?.toString
              ? String(e.toString())
              : "Failed to download voice";
      setError(msg);
    } finally {
      setDownloadingVoiceId(null);
    }
  };

  const togglePlay = async (voiceId: string) => {
    if (!downloadedVoiceIds.has(voiceId)) return;
    try {
      await toggleVoice(voiceId);
    } catch (e) {
      console.error("toggleVoice failed", e);
    }
  };

  const isDownloaded = (voiceId: string) => downloadedVoiceIds.has(String(voiceId));

  return (
    <div>
      <div className="flex justify-between items-center mb-8">
        <h2 className="text-3xl font-black">VOICES</h2>
      </div>

      <ExperienceModal
        open={createStartOpen}
        mode="create"
        experienceType={createExperienceType}
        createVoiceId={createExperienceVoiceId}
        createVoiceName={createExperienceVoiceName}
        onClose={() => {
          setCreateExperienceType("personality");
          setCreateExperienceVoiceId(createExperienceVoiceId);
          setCreateExperienceVoiceName(createExperienceVoiceName);
          setCreateStartOpen(false);
        }}
        onSuccess={async () => {
          navigate('/');
        }}
      />

      <VoiceClone
        open={createVoiceOpen}
        onClose={() => setCreateVoiceOpen(false)}
        onCreated={async (voiceId) => {
          try {
            const data = await api.getVoices();
            setVoices(Array.isArray(data) ? data : []);
          } catch {
            // ignore
          }
          setDownloadedVoiceIds((prev) => {
            const next = new Set(prev);
            next.add(String(voiceId));
            return next;
          });
        }}
      />

      {loading && <div className="retro-card font-mono text-sm mb-4">Loading…</div>}
      {error && <div className="retro-card font-mono text-sm mb-4">{error}</div>}

      {!loading && !error && voices.length === 0 && (
        <div className="retro-card font-mono text-sm mb-4">No voices found.</div>
      )}

      <div className="flex flex-col gap-4">
        {sortedVoices.map((v) => (
          <div
            key={v.voice_id}
            ref={selectedVoiceId === v.voice_id ? selectedRef : null}
            className={`retro-card relative ${selectedVoiceId === v.voice_id ? "retro-selected" : ""}`}
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <h3 className="text-md font-black inline-flex items-center gap-2">
                  <span>{v.voice_name || v.voice_id}</span>
                  {/* <span
                    className="inline-flex items-center justify-center align-middle"
                    title={String(v.gender).toLowerCase() === "female" ? "female voice" : "male voice"}
                  >
                    <span className="text-sm leading-none relative top-[1px]">
                      {String(v.gender).toLowerCase() === "female" ? "🙋‍♀️" : "🙋‍♂️"}
                    </span>
                  </span> */}
                </h3>
                
                <p className="text-gray-600 text-xs font-medium mt-2">
                  {v.voice_description ? v.voice_description : "—"}
                </p>
                {v.transcript ? (
                  <blockquote className="mt-2 pl-3 border-l-2 border-gray-300 font-mono text-xs text-gray-400 italic">
                    {String(v.transcript)}
                  </blockquote>
                ) : null}
              </div>

              <div className="shrink-0 pt-1">
                <div className="flex flex-col items-end gap-2">
                  <button
                    disabled={!isDownloaded(v.voice_id)}
                    type="button"
                    className="retro-btn retro-btn-sm"
                    onClick={() => {
                      setCreateExperienceVoiceId(String(v.voice_id));
                      setCreateExperienceVoiceName(String(v.voice_name || v.voice_id));
                      setCreateStartOpen(true);
                    }}
                  >
                    <span className="inline-flex items-center gap-2">
                    <Plus size={16} />
                    </span>
                  </button>
                  <VoiceActionButtons
                    voiceId={String(v.voice_id)}
                    isDownloaded={isDownloaded((v.voice_id))}
                    downloadingVoiceId={downloadingVoiceId}
                    onDownload={(id) => downloadVoice(id)}
                    onTogglePlay={(id) => togglePlay(id)}
                    isPlaying={playingVoiceId === String(v.voice_id)}
                    isPaused={isPaused}
                    size="small"
                  />
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
