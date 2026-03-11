import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { CheckCircle2, Loader2 } from "lucide-react";
import logoPng from "../assets/logo.png";
import { api } from "../api";
import { SETUP_COPY, STARTUP_DEFAULT_MESSAGE } from "../constants";

interface SetupStatus {
  python_installed: boolean;
  python_version: string | null;
  python_path: string | null;
  venv_exists: boolean;
  venv_path: string | null;
  deps_installed: boolean;
}

type BootstrapStep =
  | "checking"
  | "downloading-python"
  | "creating-venv"
  | "installing-deps"
  | "downloading-models"
  | "requesting-permissions"
  | "starting-backend"
  | "finalizing"
  | "complete";

export const SetupPage = () => {
  const navigate = useNavigate();
  const [step, setStep] = useState<BootstrapStep>("checking");
  const [, setStatus] = useState<SetupStatus | null>(null);
  const [progress, setProgress] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const activeStepLabel =
    step === "installing-deps"
      ? SETUP_COPY.activeStepDownloadingPackages
      : step === "downloading-models"
      ? SETUP_COPY.activeStepDownloadingModels
      : step === "downloading-python"
        ? SETUP_COPY.activeStepDownloadingPython
      : step === "creating-venv" || step === "checking"
        ? SETUP_COPY.activeStepPreparing
        : step === "requesting-permissions"
          ? SETUP_COPY.activeStepPermissions
          : step === "starting-backend"
            ? SETUP_COPY.activeStepStarting
            : step === "finalizing"
              ? SETUP_COPY.activeStepFinalizing
        : "Ready";
  const progressPercent =
    step === "complete"
      ? 100
      : step === "finalizing"
        ? 95
        : step === "starting-backend"
          ? 85
          : step === "requesting-permissions"
            ? 80
            : step === "downloading-models"
              ? 70
              : step === "installing-deps"
                ? 55
        : step === "creating-venv"
          ? 40
          : step === "downloading-python"
            ? 20
            : 10;

  useEffect(() => {
    const unlistenSetup = listen<string>("setup-progress", (event) => {
      setProgress(event.payload);
    });
    const unlistenModels = listen<string>("model-download-progress", (event) => {
      setProgress(event.payload);
    });

    void checkStatus();

    return () => {
      unlistenSetup.then((fn) => fn());
      unlistenModels.then((fn) => fn());
    };
  }, []);

  const checkStatus = async () => {
    try {
      setStep("checking");
      setError(null);
      const result = await invoke<SetupStatus>("check_setup_status");
      setStatus(result);

      await runFullSetup(result);
    } catch (e: any) {
      setError(e?.message || String(e));
    }
  };

  const waitForBackendReady = async () => {
    setProgress(STARTUP_DEFAULT_MESSAGE);
    while (true) {
      try {
        const st = await api.startupStatus();
        const counts = st?.counts || {};
        if (!st?.seeded) {
          setProgress(
            `Seeding database... (voices: ${counts.voices ?? 0}, personalities: ${counts.personalities ?? 0})`,
          );
        } else if (!st?.pipeline_ready) {
          setProgress("Starting AI engine...");
        } else {
          setProgress("Ready");
        }
        if (st?.ready) {
          return;
        }
      } catch {
        setProgress(STARTUP_DEFAULT_MESSAGE);
      }
      await new Promise((r) => setTimeout(r, 500));
    }
  };

  const requestPermissions = async () => {
    setStep("requesting-permissions");
    setProgress("Requesting microphone permission...");
    try {
      if (navigator.mediaDevices?.getUserMedia) {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach((t) => t.stop());
      }
    } catch {
      // Non-fatal: user can enable this later in System Settings.
    }

    setProgress("Requesting local network permission...");
    try {
      await invoke("trigger_local_network_prompt");
    } catch {
      // Non-fatal.
    }
  };

  const runFullSetup = async (status?: SetupStatus) => {
    try {
      setError(null);
      const setupStatus = status ?? (await invoke<SetupStatus>("check_setup_status"));

      if (!setupStatus.python_installed) {
        setStep("downloading-python");
        setProgress(`${SETUP_COPY.activeStepDownloadingPython}...`);
        await invoke("ensure_python_runtime");
      }

      if (!setupStatus.venv_exists) {
        setStep("creating-venv");
        setProgress(`${SETUP_COPY.activeStepPreparing}...`);
        await invoke("create_python_venv");
      }

      if (!setupStatus.deps_installed) {
        setStep("installing-deps");
        setProgress(`${SETUP_COPY.activeStepDownloadingPackages}...`);
        await invoke("install_python_deps");
      }

      setStep("downloading-models");
      setProgress(`${SETUP_COPY.activeStepDownloadingModels}...`);
      await invoke("download_all_models");

      await requestPermissions();

      setStep("starting-backend");
      setProgress(STARTUP_DEFAULT_MESSAGE);
      await invoke("start_backend");
      await waitForBackendReady();

      setStep("finalizing");
      setProgress("Finalizing setup...");
      await invoke("mark_setup_complete");

      setStep("complete");
      setProgress("Setup complete. Opening app...");
      navigate("/", { replace: true });
    } catch (e: any) {
      setError(e?.message || String(e));
    }
  };

  return (
    <div className="min-h-screen bg-(--color-retro-bg) flex items-center justify-center p-8">
      <div className="max-w-lg w-full">
        <div className="mb-8">
          <div className="flex items-center justify-center gap-2">
            <img src={logoPng} alt="" className="w-10 h-10" />
            <div className="text-4xl font-black tracking-wider brand-font mt-2">{SETUP_COPY.appName}</div>
            <div className="text-xs">{SETUP_COPY.appSuffix}</div>
            <div />
          </div>
            <div className="text-lg mt-2 text-center font-semibold text-gray-900">{SETUP_COPY.tagline}</div>


          <div className="mt-6 text-center">

            <div className="mt-3 text-gray-700 text-sm leading-relaxed">
              {SETUP_COPY.privacyBlurb}
              <br /><br />
              {SETUP_COPY.durationBlurb}
            </div>
          </div>
        </div>

          <div className="space-y-4">
            <div className="mb-4">
              <div className="h-4 w-full rounded-full bg-gray-100 overflow-hidden">
                <div
                  className="h-full bg-[#00c853] transition-all duration-300"
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
            </div>

              {error && (
                <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-xl">
                  <div className="text-sm text-red-700 font-mono break-all">{error}</div>
                  <button className="retro-btn mt-3 w-full" onClick={checkStatus}>
                    Retry
                  </button>
                </div>
              )}
          </div>

        <div className="mt-6 text-center text-xs text-gray-500 font-mono opacity-60">
          You can keep using your computer while this finishes.
        </div>
      </div>
      {!error && (
        <div className="fixed bottom-6 right-6 pointer-events-none">
          <div className="flex items-start gap-3 rounded-2xl border border-gray-200 bg-white/95 px-4 py-3 shadow-lg">
            {step === "complete" ? (
              <CheckCircle2 className="w-4 h-4 text-white mt-0.5" fill="black" />
            ) : (
              <Loader2 className="w-4 h-4 animate-spin text-gray-500 mt-0.5" />
            )}
            <div className="text-sm">
              <div className="font-semibold text-gray-900">
                {step === "complete" ? SETUP_COPY.toastComplete : activeStepLabel}
              </div>
              {progress && step !== "complete" && (
                <div className="text-xs text-gray-500 mt-1">{progress}</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
