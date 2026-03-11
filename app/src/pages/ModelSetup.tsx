import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import {
  Loader2,
  CheckCircle2,
  Download,
  Mic,
  Volume2,
  AlertCircle,
  Brain,
} from "lucide-react";
import { LlmSelector } from "../components/LlmSelector";
import { STARTUP_DEFAULT_MESSAGE } from "../constants";

interface ModelInfo {
  id: string;
  name: string;
  model_type: string;
  repo_id: string;
  downloaded: boolean;
  size_estimate: string | null;
}

interface ModelStatus {
  models: ModelInfo[];
  all_downloaded: boolean;
}

interface LocalModelInfo {
  id: string;
  name: string;
  model_type: string;
  repo_id: string;
  downloaded: boolean;
  size_estimate: string | null;
}

export const ModelSetupPage = () => {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [downloadingAll, setDownloadingAll] = useState(false);
  const [progress, setProgress] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  const [localModels, setLocalModels] = useState<LocalModelInfo[]>([]);
  const [selectedLlmRepoId, setSelectedLlmRepoId] = useState<string>("");
  const [savingLlm, setSavingLlm] = useState(false);

  useEffect(() => {
    const unlisten = listen<string>("model-download-progress", (event) => {
      setProgress(event.payload);
    });

    checkModels();
    refreshLocalModels();
    loadSelectedLlm();

    return () => {
      unlisten.then((fn) => fn());
    };
  }, []);

  const refreshLocalModels = async () => {
    try {
      const result = await invoke<LocalModelInfo[]>("scan_local_models");
      setLocalModels(result);
    } catch (e: any) {
      // Non-fatal; keep page usable
      console.warn("Failed to scan local models:", e);
    }
  };

  const loadSelectedLlm = async () => {
    try {
      // Uses the Python sidecar settings endpoint
      const res = await fetch("http://127.0.0.1:8000/settings/llm_model").then((r) => r.json());
      if (typeof res?.value === "string") {
        setSelectedLlmRepoId(res.value);
      }
    } catch (e) {
      // ignore
    }
  };

  const saveSelectedLlm = async (repoId: string) => {
    try {
      setSavingLlm(true);
      setError(null);
      await fetch("http://127.0.0.1:8000/settings/llm_model", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: repoId || null }),
      });
      setSelectedLlmRepoId(repoId);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setSavingLlm(false);
    }
  };

  const checkModels = async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await invoke<ModelStatus>("check_models_status");
      setModelStatus(result);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  const downloadModel = async (repoId: string) => {
    let ok = false;
    try {
      setDownloading(repoId);
      setError(null);
      setProgress(`Downloading ${repoId}...`);
      await invoke("download_model", { repoId });
      await checkModels();
      ok = true;
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setDownloading(null);
      setProgress("");
    }
    return ok;
  };
  const handleLlmPrimaryAction = async () => {
    if (!selectedLlmRepoId) return;
    if (llmDownloaded) {
      await saveSelectedLlm(selectedLlmRepoId);
      return;
    }
    const ok = await downloadModel(selectedLlmRepoId);
    if (ok) {
      await saveSelectedLlm(selectedLlmRepoId);
    }
  };

  const getRequiredModels = () => (modelStatus?.models || []).filter((m) => m.model_type !== "llm");

  const downloadRequiredModels = async () => {
    try {
      setDownloadingAll(true);
      setError(null);
      for (const model of getRequiredModels()) {
        await invoke("download_model", { repoId: model.repo_id });
      }
      await checkModels();
      await refreshLocalModels();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setDownloadingAll(false);
      setProgress("");
    }
  };

  const handleContinue = async () => {
    try {
      // Request permissions as part of first-run setup.
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach((t) => t.stop());
      } catch (e: any) {
        const name = String(e?.name || "");
        if (name === "NotAllowedError" || name === "SecurityError") {
          setError("Microphone permission was denied. Enable Microphone for OpenToys in Settings > Permissions, then continue.");
          return;
        }
        setError(e?.message || "Failed to request microphone permission.");
        return;
      }

      try {
        await invoke("trigger_local_network_prompt");
      } catch {
        // Non-fatal.
      }
      try {
        await invoke("open_system_permission", { kind: "local-network" });
      } catch {
        // Non-fatal: user can enable from Settings page.
      }

      setProgress(STARTUP_DEFAULT_MESSAGE);
      await invoke("start_backend");
      await invoke("mark_setup_complete");
      setError(null);
      navigate("/", { replace: true });
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setProgress("");
    }
  };

  const getModelIcon = (modelType: string) => {
    switch (modelType) {
      case "stt":
        return <Mic className="w-5 h-5" />;
      case "llm":
        return <Brain className="w-5 h-5" />;
      case "tts":
        return <Volume2 className="w-5 h-5" />;
      default:
        return <Download className="w-5 h-5" />;
    }
  };

  const getModelTypeLabel = (modelType: string) => {
    switch (modelType) {
      case "stt":
        return "Speech-to-Text";
      case "llm":
        return "Language Model";
      case "tts":
        return "Text-to-Speech";
      default:
        return modelType.toUpperCase();
    }
  };

  const localLlms = localModels
    .filter((m) => m.model_type === "llm")
    .sort((a, b) => a.repo_id.localeCompare(b.repo_id));
  const requiredModels = getRequiredModels();
  const pendingModels = requiredModels.filter((m) => !m.downloaded);
  const llmDownloaded = !!selectedLlmRepoId && localLlms.some((m) => m.repo_id === selectedLlmRepoId);
  const allDownloaded = pendingModels.length === 0 && llmDownloaded;

  return (
    <div className="min-h-screen bg-(--color-retro-bg) flex items-center justify-center p-8">
      <div className="max-w-2xl w-full">
        <div className="text-center mb-6">
          <h1 className="text-2xl font-black tracking-wider">AI Model Setup</h1>
        </div>

        <div className="retro-card space-y-4">
          <div className="font-bold uppercase text-sm flex items-center gap-2 border-b-2 border-black pb-2">
            <Brain className="w-4 h-4" />
                               <div className="text-xs text-gray-500 uppercase tracking-wider font-semibold">
                        Language Model
                      </div>
          </div>

          <LlmSelector
            value={selectedLlmRepoId}
            onChange={(repoId) => setSelectedLlmRepoId(repoId)}
            label="Pick an LLM that suits your needs"
          />

          <div className="flex items-center gap-3">
            <button
              className="retro-btn text-xs py-1.5 px-4"
              onClick={handleLlmPrimaryAction}
              disabled={
                !selectedLlmRepoId ||
                savingLlm ||
                downloadingAll ||
                downloading === selectedLlmRepoId
              }
            >
              {downloading === selectedLlmRepoId
                ? "Downloading..."
                : savingLlm
                  ? "Saving..."
                  : llmDownloaded
                    ? "Set as active"
                    : "Download & set active"}
            </button>
            {llmDownloaded && (
              <div className="ml-auto flex items-center gap-1 rounded-full bg-emerald-500 text-white px-3 py-1">
                <CheckCircle2 className="w-4 h-4" />
                <span className="text-xs font-semibold uppercase tracking-wide">Ready</span>
              </div>
            )}
          </div>

          {selectedLlmRepoId && (
            <div className="text-[10px] font-mono text-gray-400 truncate">
              {selectedLlmRepoId}
            </div>
          )}
          {selectedLlmRepoId && llmDownloaded && (
            <div className="text-xs text-gray-500 mt-1">
              Active model will be applied on restart.
            </div>
          )}
        </div>

        <div className="retro-card mt-6">
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
            </div>
          ) : (
            <>
              <div className="space-y-4">
                {requiredModels.map((model) => (
                  <div
                    key={model.id}
                    className="bg-white border border-gray-200 rounded-2xl p-4 flex items-center gap-4 shadow-sm"
                  >
                    <div
                      className={`w-10 h-10 flex items-center justify-center rounded-full ${
                        model.downloaded ? "bg-green-500 text-white" : "bg-gray-100 text-gray-500"
                      }`}
                    >
                      {getModelIcon(model.model_type)}
                    </div>

                    <div className="flex-1 min-w-0">
                      <div className="text-xs text-gray-500 uppercase tracking-wider font-semibold">
                        {getModelTypeLabel(model.model_type)}
                      </div>
                      <div className="font-bold text-sm text-gray-900 mt-0.5">{model.name}</div>
                      <div className="text-[10px] font-mono text-gray-400 truncate mt-1">
                        {model.repo_id}
                      </div>
                    </div>

                    <div className="flex items-center gap-3">
                      {model.downloaded ? (
                        <div className="flex items-center gap-1 rounded-full px-3 py-1">
                          <CheckCircle2 className="w-4 h-4 text-white"  fill="black" />
                          <span className="text-xs font-semibold uppercase tracking-wide">Ready</span>
                        </div>
                      ) : downloading === model.repo_id || downloadingAll ? (
                        <div className="flex items-center gap-2 text-blue-600">
                          <Loader2 className="w-4 h-4 animate-spin" />
                          <span className="text-xs font-semibold uppercase tracking-wide">Downloading</span>
                        </div>
                      ) : (
                        <button
                          className="retro-btn text-xs py-1.5 px-4"
                          onClick={() => downloadModel(model.repo_id)}
                          disabled={!!downloading || downloadingAll}
                        >
                          Download
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>

              {progress && (
                <div className="mt-4 p-3 bg-blue-50 border border-blue-200 rounded-xl flex items-center gap-3">
                  <Loader2 className="w-4 h-4 animate-spin text-blue-600" />
                  <div className="text-sm text-blue-700 font-mono">{progress}</div>
                </div>
              )}

              {error && (
                <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-xl flex items-start gap-2">
                  <AlertCircle className="w-4 h-4 text-red-500 mt-0.5 shrink-0" />
                  <div className="text-sm text-red-700 font-mono break-all">{error}</div>
                </div>
              )}

              {!allDownloaded && pendingModels.length > 0 && (
                <div className="mt-6">
                  <button
                    className="retro-btn w-full flex items-center justify-center gap-2"
                    onClick={downloadRequiredModels}
                    disabled={!!downloading || downloadingAll}
                  >
                    {downloadingAll ? (
                      <>
                        <Loader2 className="w-4 h-4 animate-spin" />
                        Downloading...
                      </>
                    ) : (
                      <>
                        <Download className="w-4 h-4" />
                        Download Required ({pendingModels.length})
                      </>
                    )}
                  </button>
                </div>
              )}
            </>
          )}
        </div>

        {allDownloaded && (
          <div className="mt-6">
            <button
              className="retro-btn retro-btn-green w-full flex items-center justify-center gap-2"
              onClick={handleContinue}
            >
              Continue to App →
            </button>
          </div>
        )}
      </div>
    </div>
  );
};
