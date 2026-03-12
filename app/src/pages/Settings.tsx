import { useEffect, useState } from 'react';
import { api } from '../api';
import { RefreshCw, Brain, Radio, MonitorUp, Rss, Zap, Mic, Network } from 'lucide-react';
import { ModelSwitchModal } from '../components/ModelSwitchModal';
import { LlmSelector } from '../components/LlmSelector';
import { invoke } from '@tauri-apps/api/core';

type ModelConfig = {
  llm: {
    backend: string;
    repo: string;
    file: string | null;
    loaded: boolean;
  };
  tts: {
    backend: string;
    loaded: boolean;
  };
};

type SystemProfile = {
  chip: string;
  model_identifier: string | null;
  total_memory_gb: number | null;
  arch: string;
};

const TTS_OPTIONS: Array<{
  id: 'chatterbox-turbo' | 'qwen3-tts';
  name: string;
  ramGb: number;
  diskGb: number;
  quality: string;
  note: string;
}> = [
  {
    id: 'qwen3-tts',
    name: 'Qwen3-TTS 4bit',
    ramGb: 4,
    diskGb: 2,
    quality: 'Balanced',
    note: 'Best default for most Apple Silicon Macs',
  },
  {
    id: 'chatterbox-turbo',
    name: 'Chatterbox Turbo',
    ramGb: 6,
    diskGb: 4,
    quality: 'Expressive',
    note: 'Great style/expressivity, usually heavier',
  },
];

export const Settings = () => {
  const [models, setModels] = useState<ModelConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [llmRepo, setLlmRepo] = useState('');
  const [ttsBackend, setTtsBackend] = useState<'chatterbox-turbo' | 'qwen3-tts'>('qwen3-tts');
  const [savingTts, setSavingTts] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ports, setPorts] = useState<string[]>([]);
  const [selectedPort, setSelectedPort] = useState<string>('');
  const [flashing, setFlashing] = useState(false);
  const [flashLog, setFlashLog] = useState<string>('');
  const [laptopVolume, setLaptopVolume] = useState<number>(70);
  const [permissionFeedback, setPermissionFeedback] = useState<string | null>(null);
  const [openingPermission, setOpeningPermission] = useState<'microphone' | 'local-network' | null>(null);
  const [requestingPermission, setRequestingPermission] = useState<'microphone' | 'local-network' | null>(null);
  const [micEnabled, setMicEnabled] = useState(false);
  const [localNetworkRequested, setLocalNetworkRequested] = useState(false);
  const [systemProfile, setSystemProfile] = useState<SystemProfile | null>(null);

  // Model switch modal state
  const [showSwitchModal, setShowSwitchModal] = useState(false);
  const [switchStage, setSwitchStage] = useState<'downloading' | 'loading' | 'complete' | 'error'>('downloading');
  const [switchProgress, setSwitchProgress] = useState(0);
  const [switchMessage, setSwitchMessage] = useState('');
  const [switchError, setSwitchError] = useState<string | undefined>();
  const [switchTarget, setSwitchTarget] = useState<'llm' | 'tts'>('llm');
  const [pendingModelRepo, setPendingModelRepo] = useState<string>('');
  const [pendingTtsBackend, setPendingTtsBackend] = useState<'chatterbox-turbo' | 'qwen3-tts' | ''>('');

  const isLikelyDevicePort = (port: string) => /\/dev\/(cu|tty)\.(usbserial|usbmodem)/i.test(port);

  const getRecommendedPort = (candidates: string[]) => {
    const prefer = candidates.find((p) => isLikelyDevicePort(p));
    return prefer || '';
  };

  const recommendedPort = getRecommendedPort(ports);
  const flashEnabled = !!selectedPort && isLikelyDevicePort(selectedPort) && !flashing;

  const openPermissionPane = async (kind: 'microphone' | 'local-network') => {
    setOpeningPermission(kind);
    setPermissionFeedback(null);
    try {
      const msg = await invoke<string>('open_system_permission', { kind });
      setPermissionFeedback(msg || 'Opened System Settings.');
    } catch (e: any) {
      setPermissionFeedback(e?.message || 'Could not open System Settings automatically.');
    } finally {
      setOpeningPermission(null);
    }
  };

  const requestPermission = async (kind: 'microphone' | 'local-network') => {
    setRequestingPermission(kind);
    setPermissionFeedback(null);
    try {
      if (kind === 'microphone') {
        if (!navigator.mediaDevices?.getUserMedia) {
          throw new Error('Microphone permission is unavailable in this context.');
        }
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach((t) => t.stop());
        setMicEnabled(true);
        setPermissionFeedback('Microphone access granted.');
      } else {
        try {
          await invoke<string>('trigger_local_network_prompt');
        } catch {
          // Non-fatal.
        }
        const msg = await invoke<string>('open_system_permission', { kind: 'local-network' });
        setLocalNetworkRequested(true);
        setPermissionFeedback(msg || 'Opened Local Network settings.');
      }
    } catch (e: any) {
      const name = String(e?.name || '');
      if (kind === 'microphone' && (name === 'NotAllowedError' || name === 'SecurityError')) {
        setPermissionFeedback('Microphone access denied. Click "Open Settings" and allow OpenToys.');
      } else {
        setPermissionFeedback(e?.message || 'Permission request failed.');
      }
    } finally {
      setRequestingPermission(null);
    }
  };

  useEffect(() => {
    loadSettings();
    loadSystemProfile();
    return () => {};
  }, []);

  const loadSystemProfile = async () => {
    try {
      const profile = await invoke<SystemProfile>('get_system_profile');
      setSystemProfile(profile);
    } catch (e) {
      console.warn('Failed to read system profile:', e);
      setSystemProfile(null);
    }
  };

  useEffect(() => {
    let cancelled = false;
    const loadMicPermission = async () => {
      try {
        const perms = (navigator as any).permissions;
        if (!perms?.query) return;
        const status = await perms.query({ name: 'microphone' as PermissionName });
        if (!cancelled) setMicEnabled(status.state === 'granted');
      } catch {
        // ignore
      }
    };
    void loadMicPermission();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    refreshPorts();
  }, []);

  const refreshPorts = async () => {
    try {
      const res = await api.firmwarePorts();
      const nextPorts = (res?.ports || []) as string[];
      setPorts(nextPorts);
      const recommended = getRecommendedPort(nextPorts);
      if (recommended && (!selectedPort || !isLikelyDevicePort(selectedPort))) {
        setSelectedPort(recommended);
      }
    } catch {
      setPorts([]);
    }
  };

  const flashFirmware = async () => {
    if (!selectedPort || flashing) return;
    setFlashing(true);
    setFlashLog('Flashing… do not unplug the device.\n');
    try {
      const res = await api.flashFirmware({ port: selectedPort, chip: 'esp32s3', baud: 460800 });
      if (res?.output) setFlashLog(String(res.output));
      else setFlashLog(JSON.stringify(res, null, 2));
      if (res?.ok) {
        setFlashLog((prev) => prev + "\n\nDone." );
      }
    } catch (e: any) {
      setFlashLog(e?.message || 'Flashing failed');
    } finally {
      setFlashing(false);
    }
  };

  const loadSettings = async () => {
    setLoading(true);
    setError(null);
    try {
      const [modelData, volSetting] = await Promise.all([
        api.getModels(),
        api.getSetting('laptop_volume').catch(() => ({ key: 'laptop_volume', value: '70' })),
      ]);
      setModels(modelData);
      setLlmRepo(modelData.llm.repo);
      const normalizedTts =
        modelData?.tts?.backend === 'chatterbox-turbo' ? 'chatterbox-turbo' : 'qwen3-tts';
      setTtsBackend(normalizedTts);
      const raw = (volSetting as any)?.value;
      const parsed = raw != null ? Number(raw) : 70;
      setLaptopVolume(Number.isFinite(parsed) ? Math.max(0, Math.min(100, parsed)) : 70);

    } catch (e) {
      console.error('Failed to load settings:', e);
      setError('Failed to load settings.');
    } finally {
      setLoading(false);
    }
  };

  const handleSaveModel = async () => {
    if (!llmRepo.trim()) return;
    
    // Open the modal and start the switch process
    setSwitchTarget('llm');
    setPendingModelRepo(llmRepo);
    setPendingTtsBackend('');
    setShowSwitchModal(true);
    setSwitchStage('downloading');
    setSwitchProgress(0);
    setSwitchMessage('Starting...');
    setSwitchError(undefined);
    
    await performModelSwitch(llmRepo);
  };

  const handleSaveTts = async () => {
    try {
      setSavingTts(true);
      setSwitchTarget('tts');
      setPendingModelRepo('');
      setPendingTtsBackend(ttsBackend);
      setShowSwitchModal(true);
      setSwitchStage('downloading');
      setSwitchProgress(0);
      setSwitchMessage('Starting...');
      setSwitchError(undefined);
      await performTtsSwitch(ttsBackend);
    } catch (e) {
      console.error('Failed to set TTS backend:', e);
      setError('Failed to update TTS backend.');
    } finally {
      setSavingTts(false);
    }
  };

  const performModelSwitch = async (modelRepo: string) => {
    try {
      for await (const update of api.switchModel(modelRepo)) {
        if (update.stage === 'error') {
          setSwitchStage('error');
          setSwitchError(update.error);
          setSwitchProgress(0);
          setSwitchMessage('Failed');
          return;
        }
        
        setSwitchStage(update.stage);
        setSwitchProgress(update.progress ?? 0);
        setSwitchMessage(update.message ?? '');
        
        if (update.stage === 'complete') {
          // Refresh settings to show the new model
          await loadSettings();
        }
      }
    } catch (e: any) {
      console.error('Model switch failed:', e);
      setSwitchStage('error');
      setSwitchError(e?.message || 'Unknown error');
    }
  };

  const performTtsSwitch = async (backend: 'chatterbox-turbo' | 'qwen3-tts') => {
    try {
      for await (const update of api.switchTts(backend)) {
        if (update.stage === 'error') {
          setSwitchStage('error');
          setSwitchError(update.error);
          setSwitchProgress(0);
          setSwitchMessage('Failed');
          return;
        }

        setSwitchStage(update.stage);
        setSwitchProgress(update.progress ?? 0);
        setSwitchMessage(update.message ?? '');

        if (update.stage === 'complete') {
          await loadSettings();
        }
      }
    } catch (e: any) {
      console.error('TTS switch failed:', e);
      setSwitchStage('error');
      setSwitchError(e?.message || 'Unknown error');
    }
  };

  const handleRetrySwitch = () => {
    setSwitchStage('downloading');
    setSwitchProgress(0);
    setSwitchMessage('Retrying...');
    setSwitchError(undefined);

    if (switchTarget === 'llm' && pendingModelRepo) {
      performModelSwitch(pendingModelRepo);
      return;
    }
    if (switchTarget === 'tts' && pendingTtsBackend) {
      performTtsSwitch(pendingTtsBackend);
    }
  };

  const handleCloseModal = () => {
    setShowSwitchModal(false);
    setPendingModelRepo('');
    setPendingTtsBackend('');
  };

  const selectedTtsMeta = TTS_OPTIONS.find((o) => o.id === ttsBackend) || null;

  return (
    <div className="settings-page space-y-6">
      <div className="flex items-start justify-between gap-4">
        <h2 className="text-3xl font-black flex items-center gap-3 settings-title">
          SETTINGS
        </h2>
        {systemProfile && (
          <div className="rounded-xl bg-white px-3 py-2 text-right">
            <div className="text-xs font-bold text-gray-900">{systemProfile.chip || 'Unknown chip'}</div>
            <div className="text-[10px] text-gray-600 font-mono">
              {systemProfile.total_memory_gb ? `${systemProfile.total_memory_gb} GB RAM` : 'RAM unknown'}
            </div>
          </div>
        )}
      </div>
      
      {error && (
        <div className="p-4 bg-red-50 border border-red-200 text-red-700 font-bold rounded-[12px]">
          {error}
        </div>
      )}
      
      <div className="retro-card settings-shell space-y-8 border border-gray-200 shadow-[0_12px_28px_rgba(0,0,0,0.06)]">
        
        {/* LLM Section */}
        <div className="settings-section space-y-4">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-2">
              <Brain className="w-5 h-5" />
              <h3 className="font-bold uppercase text-lg">Language Model (LLM)</h3>
            </div>
            <button
              onClick={handleSaveModel}
              disabled={showSwitchModal || loading || !llmRepo || llmRepo === models?.llm.repo}
              className="retro-btn retro-btn-outline settings-action text-gray-900 disabled:opacity-50 flex items-center gap-2"
            >
              <Rss className="w-4 h-4" />
              Update
            </button>
          </div>
          
          <div className="flex gap-2 items-start">
            <div className="flex-1">
              <LlmSelector
                value={llmRepo}
                onChange={(repoId) => setLlmRepo(repoId)}
                disabled={showSwitchModal || loading}
                systemMemoryGb={systemProfile?.total_memory_gb ?? null}
                label=""
              />
            </div>
          </div>
          <p className="text-[10px] mt-2 opacity-60">
            {models?.llm.loaded ? (
              <span className="text-green-600 font-bold">● LLM Active</span>
            ) : (
              <span className="text-red-500 font-bold">● LLM Not Active</span>
            )}
          </p>
        </div>

        <div className="settings-section pt-8 border-t border-gray-200 space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Brain className="w-5 h-5" />
              <h3 className="font-bold uppercase text-lg">Text to Speech (TTS)</h3>
            </div>
            <button
              onClick={handleSaveTts}
              disabled={savingTts || loading || ttsBackend === (models?.tts?.backend === 'chatterbox-turbo' ? 'chatterbox-turbo' : 'qwen3-tts')}
              className="retro-btn retro-btn-outline settings-action text-gray-900 disabled:opacity-50 flex items-center gap-2"
            >
              <Rss className="w-4 h-4" />
              Update
            </button>
          </div>
          <select
            className="retro-input bg-white border border-gray-200 w-full"
            value={ttsBackend}
            onChange={(e) => setTtsBackend((e.target.value as 'chatterbox-turbo' | 'qwen3-tts'))}
            disabled={savingTts || loading}
          >
            {TTS_OPTIONS.map((opt) => (
              <option key={opt.id} value={opt.id}>
                {opt.name}
              </option>
            ))}
          </select>
          {selectedTtsMeta && (
            <div className="text-xs text-gray-600">
              ~{selectedTtsMeta.ramGb}GB RAM · ~{selectedTtsMeta.diskGb}GB Disk · {selectedTtsMeta.quality}
            </div>
          )}
          <p className="text-[10px] mt-2 opacity-60">
            {models?.tts?.loaded ? (
              <span className="text-green-600 font-bold">● TTS Active</span>
            ) : (
              <span className="text-red-500 font-bold">● TTS Not Active</span>
            )}
          </p>
        </div>

        <div className="settings-section pt-8 border-t border-gray-200">
          <div className="flex items-center gap-2 justify-between">
            <div className="flex flex-col gap-1">
              <h3 className="flex items-center gap-2 font-bold uppercase text-lg">
            <MonitorUp className="w-5 h-5" />
            Connect your ESP32 Here
          </h3>
            </div>
              <button
                type="button"
                className="retro-btn retro-btn-outline settings-action text-gray-900 disabled:opacity-50 flex items-center gap-2"
                onClick={flashFirmware}
                disabled={!flashEnabled}
              >
                <Zap size={16} />{flashing ? 'Flashing…' : 'Flash'}
              </button>
          </div>
          <div className="mt-5">
            <div className="flex items-center justify-between gap-3 mb-2">
              <div className="text-xs text-gray-500 uppercase">Serial Port</div>
              <button
                type="button"
                className="inline-flex items-center gap-2 text-xs font-bold uppercase opacity-60 hover:opacity-100 disabled:opacity-30"
                onClick={refreshPorts}
                disabled={flashing}
              >
                <RefreshCw className={flashing ? "w-4 h-4 animate-spin" : "w-4 h-4"} />
                Refresh
              </button>
            </div>

            <div className="flex flex-col sm:flex-row gap-3 sm:items-center">
              <select
                className="retro-input bg-white border border-gray-200 flex-1"
                value={selectedPort}
                onChange={(e) => setSelectedPort(e.target.value)}
                disabled={flashing}
              >
                {ports.length === 0 && <option value="">No ports found</option>}
                {ports.map((p) => (
                  <option key={p} value={p} disabled={!isLikelyDevicePort(p)}>
                    {p}{recommendedPort && p === recommendedPort ? ' (recommended)' : ''}{!isLikelyDevicePort(p) ? ' (not a device)' : ''}
                  </option>
                ))}
              </select>
            </div>

            <div className="mt-2 text-[10px] opacity-60 font-mono">
              On MacOS, pick /dev/cu.usbserial-* (often -210/-110/-10) or /dev/cu.usbmodem*. Avoid Bluetooth ports.
            </div>
          </div>

          <div className="mt-4">
            <div className="text-xs text-gray-500 uppercase mb-2">Output</div>
            <pre className="bg-white border border-gray-200 rounded-[12px] p-3 text-xs font-mono whitespace-pre-wrap max-h-56 overflow-auto">
              {flashLog || '—'}
            </pre>
          </div>
        </div>

        {/* Device Status Section */}
        <div className="settings-section pt-8 border-t border-gray-200">
          <h3 className="flex items-center gap-2 font-bold uppercase text-lg">
            <Radio className="w-5 h-5" />
            Device Settings
          </h3>
          
          {/* <div className="grid grid-cols-1 md:grid-cols-2 mt-2 gap-4">
            <div className="p-4 flex items-start flex-col sm:flex-row gap-4 justify-between">
              <div>
                <div className="text-xs text-gray-500 uppercase mb-1 flex items-center gap-1">
                  <Wifi className="w-3 h-3" /> Connection
                </div>
                <div className={`text-lg font-black ${device?.ws_status === 'connected' ? 'text-green-600' : 'text-red-500'}`}>
                  {device?.ws_status === 'connected' ? 'ONLINE' : 'OFFLINE'}
                </div>
              </div>
              <div className="text-right">
                <div className="text-xs text-gray-500 uppercase mb-1">MAC Address</div>
                <div className="font-mono font-bold tracking-widest text-sm">
                  {device?.mac_address || 'Not found'}
                </div>
              </div>
            </div>
          </div> */}
          <div className="py-4">
            <div className="text-xs text-gray-500 uppercase mb-2">Laptop Volume</div>
            <div className="flex items-center gap-4">
              <input
                type="range"
                min="0"
                max="100"
                value={laptopVolume}
                onChange={(e) => {
                  const vol = Math.max(0, Math.min(100, Number(e.target.value)));
                  setLaptopVolume(vol);
                  api.setSetting('laptop_volume', String(vol)).catch(console.error);
                }}
                className="retro-range w-full h-2 bg-white rounded-lg appearance-none cursor-pointer"
                style={{
                  background: `linear-gradient(#9b5cff 0 0) 0/${Math.max(0, Math.min(100, laptopVolume))}% 100% no-repeat, white`,
                }}
              />
              <span className="font-black w-12 text-right">{laptopVolume}%</span>
            </div>
          </div>
        </div>

        <div className="settings-section pt-8 border-t border-gray-200 space-y-4">
          <div className="flex items-center gap-2">
            <Network className="w-5 h-5" />
            <h3 className="font-bold uppercase text-lg">Permissions</h3>
          </div>
          <div className="space-y-3">
            <div className="rounded-xl border border-gray-200 bg-white px-4 py-3 flex items-center justify-between gap-4">
              <div className="min-w-0">
                <div className="text-sm font-bold uppercase tracking-wide flex items-center gap-2">
                  <Mic className="w-4 h-4" />
                  Microphone
                </div>
                <div className="text-xs text-gray-500 mt-1">
                  {micEnabled ? 'Granted' : 'Not granted'}
                </div>
              </div>
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  role="switch"
                  aria-checked={micEnabled}
                  aria-label="Request microphone access"
                  onClick={() => void requestPermission('microphone')}
                  disabled={requestingPermission !== null}
                  className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-colors duration-200 ${
                    micEnabled ? 'bg-green-500 border-green-500' : 'bg-gray-200 border-gray-300'
                  }`}
                >
                  <span
                    className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform duration-200 ${
                      micEnabled ? 'translate-x-5' : 'translate-x-0.5'
                    }`}
                  />
                </button>
                <button
                  type="button"
                  className="text-xs font-bold uppercase opacity-60 hover:opacity-100"
                  onClick={() => void openPermissionPane('microphone')}
                  disabled={openingPermission !== null}
                >
                  {openingPermission === 'microphone' ? 'Opening…' : 'Open Settings'}
                </button>
              </div>
            </div>

            <div className="rounded-xl border border-gray-200 bg-white px-4 py-3 flex items-center justify-between gap-4">
              <div className="min-w-0">
                <div className="text-sm font-bold uppercase tracking-wide flex items-center gap-2">
                  <Network className="w-4 h-4" />
                  Local Network
                </div>
                <div className="text-xs text-gray-500 mt-1">
                  {localNetworkRequested ? 'Requested' : 'Not requested'}
                </div>
              </div>
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  role="switch"
                  aria-checked={localNetworkRequested}
                  aria-label="Request local network access"
                  onClick={() => void requestPermission('local-network')}
                  disabled={requestingPermission !== null}
                  className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-colors duration-200 ${
                    localNetworkRequested ? 'bg-green-500 border-green-500' : 'bg-gray-200 border-gray-300'
                  }`}
                >
                  <span
                    className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform duration-200 ${
                      localNetworkRequested ? 'translate-x-5' : 'translate-x-0.5'
                    }`}
                  />
                </button>
                <button
                  type="button"
                  className="text-xs font-bold uppercase opacity-60 hover:opacity-100"
                  onClick={() => void openPermissionPane('local-network')}
                  disabled={openingPermission !== null}
                >
                  {openingPermission === 'local-network' ? 'Opening…' : 'Open Settings'}
                </button>
              </div>
            </div>
          </div>
          {permissionFeedback && (
            <div className="text-xs font-mono text-gray-600  py-2">
              {permissionFeedback}
            </div>
          )}
        </div>
      </div>

      {/* Model Switch Modal */}
      <ModelSwitchModal
        isOpen={showSwitchModal}
        stage={switchStage}
        progress={switchProgress}
        message={switchMessage}
        error={switchError}
        title={switchTarget === 'tts' ? 'Switching Voice Engine' : 'Switching Model'}
        downloadingLabel={switchTarget === 'tts' ? 'Downloading Voice Model' : 'Downloading Model'}
        loadingLabel={switchTarget === 'tts' ? 'Loading Voice Weights' : 'Loading Weights'}
        onRetry={handleRetrySwitch}
        onClose={handleCloseModal}
      />
    </div>
  );
};
