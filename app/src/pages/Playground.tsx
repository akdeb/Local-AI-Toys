import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import { Image as ImageIcon, Pencil, BookOpen, Moon, Maximize2 } from 'lucide-react';
import { useActiveUser } from '../state/ActiveUserContext';
import { ExperienceModal, ExperienceForModal } from '../components/ExperienceModal';
import { Link, useSearchParams } from 'react-router-dom';
import { convertFileSrc } from '@tauri-apps/api/core';
import { Modal } from '../components/Modal';
import { VoiceActionButtons } from '../components/VoiceActionButtons';
import { useVoicePlayback } from '../hooks/useVoicePlayback';

type ExperienceType = 'personality' | 'game' | 'story';

export const Playground = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab: ExperienceType = 'personality';
  const [bedtimeMode, setBedtimeMode] = useState(false);
  
  const [experiences, setExperiences] = useState<any[]>([]);
  const [voices, setVoices] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [brokenImgById, setBrokenImgById] = useState<Record<string, boolean>>({});
  const [imgRefreshById, setImgRefreshById] = useState<Record<string, number>>({});
  const [downloadedVoiceIds, setDownloadedVoiceIds] = useState<Set<string>>(new Set());
  const [downloadingVoiceId, setDownloadingVoiceId] = useState<string | null>(null);
  const [audioSrcByVoiceId, setAudioSrcByVoiceId] = useState<Record<string, string>>({});
  
  // Modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [modalMode, setModalMode] = useState<'create' | 'edit'>('create');
  const [selectedExperience, setSelectedExperience] = useState<ExperienceForModal | null>(null);
  const [infoOpen, setInfoOpen] = useState(false);
  const [infoExperience, setInfoExperience] = useState<any | null>(null);

  const { activeUserId, activeUser, refreshUsers } = useActiveUser();
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

  const GLOBAL_IMAGE_BASE_URL = 'https://pub-a64cd21521e44c81a85db631f1cdaacc.r2.dev';

  const imgSrcFor = (p: any) => {
    const refreshKey = p?.id != null ? imgRefreshById[String(p.id)] : undefined;
    if (p?.is_global) {
      const id = p?.id != null ? String(p.id) : '';
      if (!id) return null;
      return `${GLOBAL_IMAGE_BASE_URL}/${encodeURIComponent(id)}.png`;
    }
    const src = typeof p?.img_src === 'string' ? p.img_src.trim() : '';
    if (!src) return null;
    if (/^https?:\/\//i.test(src)) return src;
    const base = convertFileSrc(src);
    return refreshKey ? `${base}?v=${refreshKey}` : base;
  };

  const toTimestamp = (v: any) => {
    if (v == null) return 0;
    if (typeof v === 'number') return Number.isFinite(v) ? v : 0;
    if (typeof v === 'string') {
      const asNum = Number(v);
      if (Number.isFinite(asNum)) return asNum;
      const ms = Date.parse(v);
      if (Number.isFinite(ms)) return Math.floor(ms / 1000);
    }
    return 0;
  };

  const load = async () => {
    try {
      setError(null);
      const data = await api.getExperiences(false, activeTab);
      setExperiences(data);
      setBrokenImgById({});
    } catch (e: any) {
      setError(e?.message || 'Failed to load experiences');
    } finally {
      setLoading(false);
    }
  };

  const sortedExperiences = useMemo(() => {
    const arr = Array.isArray(experiences) ? experiences.slice() : [];
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
  }, [experiences]);

  useEffect(() => {
    setLoading(true);
    load();
  }, [activeTab]);

  useEffect(() => {
    const focusId = searchParams.get('focus');
    if (!focusId || loading) return;
    const el = document.getElementById(`experience-${focusId}`);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [searchParams, experiences, loading]);

  useEffect(() => {
    const create = searchParams.get('create');
    if (!create) return;
    setModalMode('create');
    setSelectedExperience(null);
    setModalOpen(true);
    const next = new URLSearchParams(searchParams);
    next.delete('create');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  useEffect(() => {
    let cancelled = false;
    const loadMode = async () => {
      try {
        const res = await api.getAppMode();
        if (!cancelled) setBedtimeMode((res?.mode || '').toLowerCase() === 'bedtime');
      } catch {
        if (!cancelled) setBedtimeMode(false);
      }
    };
    loadMode();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadVoices = async () => {
      try {
        const data = await api.getVoices();
        if (!cancelled) setVoices(Array.isArray(data) ? data : []);
      } catch {
        if (!cancelled) setVoices([]);
      }
    };
    loadVoices();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadDownloaded = async () => {
      try {
        const ids = await api.listDownloadedVoices();
        if (!cancelled) setDownloadedVoiceIds(new Set(Array.isArray(ids) ? ids : []));
      } catch {
        if (!cancelled) setDownloadedVoiceIds(new Set());
      }
    };
    loadDownloaded();
    return () => {
      cancelled = true;
    };
  }, []);

  const assignToActiveUser = async (experienceId: string) => {
    if (!activeUserId) {
      setError('Select an active user first');
      return;
    }
    try {
      setError(null);
      await api.updateUser(activeUserId, { current_personality_id: experienceId });
      await refreshUsers();
    } catch (e: any) {
      setError(e?.message || 'Failed to assign experience');
    }
  };

  const deleteExperience = async (p: any) => {
    if (p?.is_global) return;
    try {
      setError(null);
      await api.deleteExperience(p.id);
      await load();
    } catch (err: any) {
      setError(err?.message || 'Failed to delete experience');
    }
  };

  const voiceById = useMemo(() => {
    const m = new Map<string, any>();
    for (const v of voices) {
      if (v?.voice_id) m.set(String(v.voice_id), v);
    }
    return m;
  }, [voices]);

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
      console.error('download_voice failed', e);
      const msg = typeof e === 'string' ? e : e?.message ? String(e.message) : String(e);
      setError(msg || 'Failed to download voice');
    } finally {
      setDownloadingVoiceId(null);
    }
  };

  const togglePlay = async (voiceId: string) => {
    if (!downloadedVoiceIds.has(voiceId)) return;
    try {
      await toggleVoice(voiceId);
    } catch (e) {
      console.error('toggleVoice failed', e);
    }
  };

  const handleEdit = (p: any, e: React.MouseEvent) => {
    e.stopPropagation();
    setModalMode('edit');
    setSelectedExperience(p);
    setModalOpen(true);
  };

  const toggleBedtimeMode = async () => {
    const next = !bedtimeMode;
    setBedtimeMode(next);
    try {
      const mode = next ? 'bedtime' : 'story';
      await api.setAppMode(mode);
      window.dispatchEvent(new CustomEvent('app-mode-changed', { detail: { mode } }));
    } catch (e) {
      console.error('Failed to set bedtime mode', e);
      setBedtimeMode(!next);
    }
  };

  return (
    <div>
      <ExperienceModal 
        open={modalOpen}
        mode={modalMode}
        experience={selectedExperience}
        experienceType={activeTab}
        imageSrc={selectedExperience ? imgSrcFor(selectedExperience) : null}
        imageBroken={Boolean(selectedExperience && brokenImgById[String(selectedExperience.id)])}
        onImageError={() => {
          if (!selectedExperience) return;
          setBrokenImgById((prev) => ({ ...prev, [String(selectedExperience.id)]: true }));
        }}
        onImageSelect={async (f) => {
          if (!selectedExperience) return;
          const buf = await f.arrayBuffer();
          let binary = '';
          const bytes = new Uint8Array(buf);
          const chunkSize = 0x8000;
          for (let i = 0; i < bytes.length; i += chunkSize) {
            const chunk = bytes.subarray(i, i + chunkSize);
            binary += String.fromCharCode(...chunk);
          }
          const b64 = btoa(binary);
          const ext = (f.name.split('.').pop() || '').toLowerCase();
          const savedPath = await api.saveExperienceImageBase64(
            String(selectedExperience.id),
            b64,
            ext || null
          );
          const nextImgSrc = savedPath?.path || savedPath;
          await api.updateExperience(String(selectedExperience.id), { img_src: nextImgSrc });
          setSelectedExperience((prev) => {
            if (!prev) return prev;
            return { ...prev, img_src: nextImgSrc };
          });
          setBrokenImgById((prev) => ({ ...prev, [String(selectedExperience.id)]: false }));
          setImgRefreshById((prev) => ({ ...prev, [String(selectedExperience.id)]: Date.now() }));
          await load();
        }}
        onDelete={selectedExperience ? async () => {
          await deleteExperience(selectedExperience);
        } : undefined}
        onClose={() => setModalOpen(false)}
        onSuccess={async () => {
          await load();
        }}
      />

      <Modal
        open={infoOpen}
        title={infoExperience?.name || '—'}
        onClose={() => {
          setInfoOpen(false);
          setInfoExperience(null);
        }}
        panelClassName="w-full max-w-6xl"
      >
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5 h-[75vh]">
          <div
            className="h-full rounded-[24px] border bg-orange-50/50 border-gray-200 flex items-center justify-center overflow-hidden"
            style={{
              backgroundImage: `radial-gradient(circle, rgba(0,0,0,0.08) 1px, transparent 1px)`,
              backgroundSize: '6px 6px'
            }}
          >
            {infoExperience && imgSrcFor(infoExperience) && !brokenImgById[String(infoExperience.id)] ? (
              <img
                src={imgSrcFor(infoExperience) || ''}
                alt=""
                className="h-full w-full object-contain object-center p-4"
                onError={() => {
                  setBrokenImgById((prev) => ({ ...prev, [String(infoExperience.id)]: true }));
                }}
              />
            ) : (
              <ImageIcon size={24} className="text-gray-500" />
            )}
          </div>

          <div className="h-full overflow-y-auto pr-2 space-y-5">
            <div>
              <div className="text-xs font-bold uppercase tracking-wider text-gray-500">Voice</div>
              <div className="mt-1 flex items-center justify-between gap-3">
                <div className="min-w-0 flex-1">
                  {infoExperience?.voice_id ? (
                    <Link
                      to={`/voices?voice_id=${encodeURIComponent(String(infoExperience.voice_id))}`}
                      onClick={() => {
                        setInfoOpen(false);
                        setInfoExperience(null);
                      }}
                      className="block text-sm font-bold truncate"
                      title="View voice"
                    >
                      {voiceById.get(String(infoExperience.voice_id))?.voice_name || infoExperience.voice_id}
                    </Link>
                  ) : (
                    <div className="text-sm text-gray-700">—</div>
                  )}
                </div>
                {infoExperience?.voice_id && (
                  <div className="shrink-0">
                    <VoiceActionButtons
                      voiceId={String(infoExperience.voice_id)}
                      isDownloaded={downloadedVoiceIds.has(String(infoExperience.voice_id))}
                      downloadingVoiceId={downloadingVoiceId}
                      onDownload={(id) => downloadVoice(id)}
                      onTogglePlay={(id) => togglePlay(id)}
                      isPlaying={playingVoiceId === String(infoExperience.voice_id)}
                      isPaused={isPaused}
                      size="small"
                    />
                  </div>
                )}
              </div>
            </div>

            <div>
              <div className="text-xs font-bold uppercase tracking-wider text-gray-500">Subtitle</div>
              <div className="text-sm text-gray-700 whitespace-pre-wrap">
                {infoExperience?.short_description || '—'}
              </div>
            </div>

            <div>
              <div className="text-xs font-bold uppercase tracking-wider text-gray-500">Prompt</div>
              <div className="text-sm text-gray-700 whitespace-pre-wrap leading-relaxed">
                {infoExperience?.prompt || '—'}
              </div>
            </div>
          </div>
        </div>
      </Modal>

      {loading && (
        <div className="retro-card font-mono text-sm">Loading…</div>
      )}
      {error && (
        <div className="retro-card font-mono text-sm">{error}</div>
      )}
      {!loading && !error && experiences.length === 0 && (
        <div className="retro-card font-mono text-sm">
          No stories found.
        </div>
      )}

      <div className="flex items-center justify-between">
        <h2 className="text-3xl font-black flex items-center gap-3">
          <BookOpen size={28} />
          CARDS
        </h2>
        <div className="inline-flex items-center gap-3 rounded-full px-3 py-2">
          <span className="inline-flex items-center gap-2 text-sm font-bold uppercase tracking-wide text-gray-700">
            <Moon fill={bedtimeMode ? "currentColor" : "none"} size={14} />
            Bedtime mode
          </span>
          <button
            type="button"
            role="switch"
            aria-checked={bedtimeMode}
            aria-label="Toggle bedtime mode"
            onClick={toggleBedtimeMode}
            className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-colors duration-200 ${
              bedtimeMode ? 'bg-purple-500 border-purple-500' : 'bg-gray-200 border-gray-300'
            }`}
          >
            <span
              className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform duration-200 ${
                bedtimeMode ? 'translate-x-5' : 'translate-x-0.5'
              }`}
            />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 pt-8">
        {sortedExperiences.map((p) => (
          <div
            key={p.id}
            id={`experience-${p.id}`}
            role="button"
            tabIndex={0}
            onClick={() => assignToActiveUser(p.id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') assignToActiveUser(p.id);
            }}
            className={`retro-card group text-left cursor-pointer transition-shadow flex flex-col ${activeUser?.current_personality_id === p.id ? 'retro-selected' : 'retro-not-selected'}`}
style={{
  padding: "0rem"
}}
          >
            <div className={`flex flex-col items-start gap-4`}>
              <div className={`w-full`}>
                {!p.is_global ? (
                  <div
                    className={`w-full h-[160px] rounded-t-[24px] bg-orange-50/50 flex items-center justify-center cursor-pointer overflow-hidden`}
style={{
                            backgroundImage: `radial-gradient(circle, rgba(0,0,0,0.08) 1px, transparent 1px)`,
                            backgroundSize: '6px 6px'
                        }}
                    title="Upload image"
                  >
                    {imgSrcFor(p) && !brokenImgById[String(p.id)] ? (
                      <div className="w-full h-full flex items-center justify-center overflow-hidden">
                        <img
                          src={imgSrcFor(p) || ''}
                          alt=""
                          className="h-auto w-auto max-h-full max-w-full object-contain object-center origin-center transition-transform duration-200 group-hover:scale-105"
                          onError={() => {
                            setBrokenImgById((prev) => ({ ...prev, [String(p.id)]: true }));
                          }}
                        />
                      </div>
                    ) : (
                      <ImageIcon size={18} className="text-gray-600" />
                    )}
                  </div>
                ) : (
                  <div className="w-full h-[160px] rounded-t-[24px] bg-orange-50/50 flex items-center justify-center overflow-hidden"                         
style={{
                            backgroundImage: `radial-gradient(circle, rgba(0,0,0,0.08) 1px, transparent 1px)`,
                            backgroundSize: '6px 6px'
                        }}>
                    {imgSrcFor(p) && !brokenImgById[String(p.id)] ? (
                      <div className="w-full h-full flex items-center justify-center overflow-hidden">
                        <img
                          src={imgSrcFor(p) || ''}
                          alt=""
                          className="h-auto w-auto max-h-full max-w-full object-contain object-center origin-center transition-transform duration-200 group-hover:scale-105"
                          onError={() => {
                            setBrokenImgById((prev) => ({ ...prev, [String(p.id)]: true }));
                          }}
                        />
                      </div>
                    ) : (
                      <ImageIcon size={18} className="text-gray-600" />
                    )}
                  </div>
                )}
              </div>

              <div className="min-w-0 relative flex-1 p-4">
            <div className="absolute top-2 right-2 flex flex-col items-center gap-2 z-10">
              {p.is_global && (
                <button
                  type="button"
                  className="retro-icon-btn"
                  aria-label="Details"
                  onClick={(e) => {
                    e.stopPropagation();
                    setInfoExperience(p);
                    setInfoOpen(true);
                  }}
                  title="Details"
                >
                  <Maximize2 size={16} />
                </button>
              )}
              {!p.is_global && (
                <button
                  type="button"
                  className="retro-icon-btn"
                  aria-label="Edit"
                  onClick={(e) => handleEdit(p, e)}
                  title="Edit"
                >
                  <Pencil size={16} />
                </button>
              )}
            </div>

                <h3 className="text-lg font-black leading-tight wrap-break-word w-[96%] retro-clamp-2">{p.name}</h3>
                <p className="text-gray-600 text-xs font-medium mt-2 retro-clamp-2">
                  {p.short_description ? String(p.short_description) : '—'}
                </p>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
