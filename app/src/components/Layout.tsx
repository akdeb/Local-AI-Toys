import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { useActiveUser } from '../state/ActiveUserContext';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { useEffect, useState } from 'react';
import { Bot, X, RefreshCw } from 'lucide-react';
import { convertFileSrc } from '@tauri-apps/api/core';
import { VoiceWsProvider, useVoiceWs } from '../state/VoiceWsContext';

const API_BASE = (import.meta as any).env?.VITE_API_BASE || 'http://127.0.0.1:8000';

const LayoutInner = () => {
  const { activeUser } = useActiveUser();
  const location = useLocation();
  const navigate = useNavigate();
  const voiceWs = useVoiceWs();
  const [activePersonalityName, setActivePersonalityName] = useState<string | null>(null);
  const [activePersonalityImageSrc, setActivePersonalityImageSrc] = useState<string | null>(null);
  const [activePersonalityImageError, setActivePersonalityImageError] = useState(false);
  const [activeVoiceId, setActiveVoiceId] = useState<string | null>(null);
  const [deviceConnected, setDeviceConnected] = useState<boolean>(false);
  const [deviceSessionId, setDeviceSessionId] = useState<string | null>(null);
  const [downloadedVoiceIds, setDownloadedVoiceIds] = useState<Set<string>>(new Set());
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Network monitoring
  const [initialIp, setInitialIp] = useState<string | null>(null);
  const [showNetworkBanner, setShowNetworkBanner] = useState(false);

  const GLOBAL_PERSONALITY_IMAGE_BASE_URL = 'https://pub-a64cd21521e44c81a85db631f1cdaacc.r2.dev';

  const personalityImageSrc = (p: any) => {
    if (!p) return null;
    if (p?.is_global) {
      const pid = p?.id != null ? String(p.id) : '';
      return pid ? `${GLOBAL_PERSONALITY_IMAGE_BASE_URL}/${encodeURIComponent(pid)}.png` : null;
    }
    const src = typeof p?.img_src === 'string' ? p.img_src.trim() : '';
    if (!src) return null;
    if (/^https?:\/\//i.test(src)) return src;
    return convertFileSrc(src);
  };

  useEffect(() => {
    let cancelled = false;
    const checkIp = async () => {
      try {
        const info = await api.getNetworkInfo();
        if (cancelled) return;
        
        if (!initialIp) {
          setInitialIp(info.ip);
        } else if (info.ip !== initialIp) {
          setShowNetworkBanner(true);
        }
      } catch {
        // ignore errors
      }
    };

    checkIp();
    const interval = setInterval(checkIp, 30000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [initialIp]);

  const localActive = voiceWs.isActive;
  const sessionActive = deviceConnected || localActive;
  const isEsp32View =
    location.pathname === '/test' && new URLSearchParams(location.search).get('view') === 'esp32';

  const statusLabel = sessionActive ? 'Chat in progress' : 'Ready on device';
  const statusDotClass = sessionActive ? 'bg-emerald-500' : 'bg-green-400';
  const statusTextClass = sessionActive ? 'text-emerald-700' : 'text-gray-600';

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const ds = await api.getDeviceStatus().catch(() => null);
        if (!cancelled && ds) {
          setDeviceConnected(ds?.ws_status === 'connected');
          setDeviceSessionId(ds?.session_id || null);
        }

        const selectedId = activeUser?.current_personality_id;
        if (!selectedId) {
          if (!cancelled) setActivePersonalityName(null);
          return;
        }

        const ps = await api.getPersonalities(true).catch(() => []);
        const selected = ps.find((p: any) => p.id === selectedId);
        if (!cancelled) {
          setActivePersonalityName(selected?.name || null);
          setActiveVoiceId(selected?.voice_id ? String(selected.voice_id) : null);
          setActivePersonalityImageSrc(personalityImageSrc(selected));
          setActivePersonalityImageError(false);
        }
      } catch {
        // ignore
        if (!cancelled) {
          setActivePersonalityImageSrc(null);
          setActivePersonalityImageError(false);
        }
      }
    };

    load();
  }, [activeUser?.current_personality_id]);

  useEffect(() => {
    let cancelled = false;
    let retryTimer: number | null = null;
    let es: EventSource | null = null;
    const base = API_BASE.replace('localhost', '127.0.0.1').replace(/\/+$/, '');

    const start = () => {
      if (cancelled) return;
      try {
        es = new EventSource(`${base}/events/device`);
      } catch {
        retryTimer = window.setTimeout(start, 3000);
        return;
      }

      es.onmessage = (ev) => {
        if (cancelled) return;
        try {
          const data = JSON.parse(ev.data || '{}');
          setDeviceConnected(data?.ws_status === 'connected');
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
  }, [activeVoiceId]);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const ids = await api.listDownloadedVoices();
        if (!cancelled) setDownloadedVoiceIds(new Set(Array.isArray(ids) ? ids : []));
      } catch {
        if (!cancelled) setDownloadedVoiceIds(new Set());
      }
    };

    const onDownloaded = () => {
      void refresh();
    };

    window.addEventListener('voice:downloaded', onDownloaded as EventListener);
    return () => {
      cancelled = true;
      window.removeEventListener('voice:downloaded', onDownloaded as EventListener);
    };
  }, []);

  const canStartChat =
    sessionActive || !activeVoiceId || downloadedVoiceIds.has(String(activeVoiceId));

  useEffect(() => {
    const onDeleted = () => {
      navigate('/');
    };
    window.addEventListener('voicews:empty-session-deleted', onDeleted as EventListener);
    return () => {
      window.removeEventListener('voicews:empty-session-deleted', onDeleted as EventListener);
    };
  }, [navigate]);

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-(--color-retro-bg)">
      {showNetworkBanner && (
        <div className="bg-(--color-retro-blue) text-white px-4 py-3 flex items-center justify-between shadow-md z-50 shrink-0 border-b-2 border-black">
          <div className="font-mono text-sm flex items-center gap-2">
            <RefreshCw size={16} />
            <span>
              <strong>WiFi Change Detected: Refresh your app so your toy can find you.</strong>
            </span>
          </div>
          <button
            disabled={isRefreshing}
            onClick={async () => {
              try {
                setIsRefreshing(true);
                await api.restartMdns();
              } catch (e) {
                console.error("Failed to restart mDNS:", e);
              }
              setIsRefreshing(false);
              window.location.reload();
            }}
            className="retro-btn retro-btn-outline no-lift px-3 py-1.5 text-xs uppercase font-bold flex items-center gap-2"
          >
            Refresh
          </button>
        </div>
      )}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        <Sidebar />
        <main className={`flex-1 min-h-0 overflow-y-auto ${location.pathname === '/test' ? 'p-0 pb-36' : 'p-8 pb-36'}`}>
          <div className={`max-w-4xl mx-auto ${location.pathname === '/test' ? 'px-8 pt-0' : ''}`}>
            <Outlet />
          </div>

        {activeUser?.current_personality_id && (
          <div className="session-dock fixed bottom-0 z-20 left-64 right-0 pointer-events-none">
            <div className="session-dock-wrap max-w-4xl mx-auto px-8 pb-6 pointer-events-auto">
              <div className="session-dock-inner bg-white border border-gray-200 rounded-full px-5 py-4 flex items-center justify-between shadow-xl">
                <div className="min-w-0 flex items-center gap-4">
                  {activePersonalityImageSrc && !activePersonalityImageError && (
                    <div className="w-12 h-12 rounded-full overflow-hidden bg-white border border-gray-200">
                      <img
                        src={activePersonalityImageSrc}
                        alt=""
                        className="w-full h-full object-cover"
                        onError={() => setActivePersonalityImageError(true)}
                      />
                    </div>
                  )}
                  <div className="min-w-0">
                    <div className="font-mono text-xs text-gray-500 flex items-center gap-2">
                      <span className={`w-2 h-2 rounded-full border border-gray-300 ${statusDotClass} ${sessionActive ? 'retro-blink' : ''}`} />
                      <span className={statusTextClass}>{statusLabel}</span>
                    </div>
                    <div className="mt-1 font-black text-base text-black truncate">{activePersonalityName || '—'}</div>
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  <div className="flex flex-col items-end">
                    {!localActive && !deviceConnected && (
                      <button
                        type="button"
                        className="retro-btn retro-btn-purple no-lift px-4 py-2 text-sm flex items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
                        onClick={() => {
                          if (!canStartChat) return;
                          navigate('/test');
                          voiceWs.connect();
                        }}
                        disabled={!canStartChat}
                      >
                        <Bot size={18} className="shrink-0" /> Preview
                      </button>
                    )}
                    {!localActive && deviceConnected && !isEsp32View && (
                      <button
                        type="button"
                        className="retro-btn retro-btn-green no-lift px-4 py-2 text-sm flex items-center gap-2 animate-pulse"
                        onClick={() => navigate('/test?view=esp32')}
                      >
                        <Bot size={18} className="shrink-0" /> View
                      </button>
                    )}
                    {!localActive && deviceConnected && isEsp32View && (
                      <button
                        type="button"
                        className="retro-btn retro-btn-outline no-lift px-4 py-2 text-sm flex items-center gap-2"
                        onClick={async () => {
                          try {
                            await api.disconnectDevice();
                            setDeviceConnected(false);
                            const sid = deviceSessionId;
                            if (sid) {
                              navigate(`/conversations?session=${encodeURIComponent(sid)}`);
                            } else {
                              navigate('/conversations');
                            }
                          } catch {
                            // ignore
                          }
                        }}
                      >
                        <X size={18} className="shrink-0" /> End
                      </button>
                    )}
                    {localActive && (
                      <button
                        type="button"
                        className="retro-btn retro-btn-outline no-lift px-4 py-2 text-sm flex items-center gap-2"
                        onClick={() => {
                          voiceWs.disconnect();
                          const sid = voiceWs.latestSessionId;
                          if (sid) {
                            navigate(`/conversations?session=${encodeURIComponent(sid)}`);
                          } else {
                            navigate('/conversations');
                          }
                        }}
                      >
                        <X size={18} className="shrink-0" /> End
                      </button>
                    )}
                    {!canStartChat && !sessionActive && !deviceConnected && (
                      <div className="mt-1 font-mono text-xs text-gray-500">
                        Download voice to start chat
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
        </main>
      </div>
    </div>
  );
};

export const Layout = () => {
  return (
    <VoiceWsProvider>
      <LayoutInner />
    </VoiceWsProvider>
  );
};
