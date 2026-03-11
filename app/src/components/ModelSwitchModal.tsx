import { Loader2, Download, AlertCircle, Check } from "lucide-react";

type Stage = "downloading" | "loading" | "complete" | "error";

interface ModelSwitchModalProps {
  isOpen: boolean;
  stage: Stage;
  progress: number;
  message: string;
  error?: string;
  title?: string;
  downloadingLabel?: string;
  loadingLabel?: string;
  onRetry?: () => void;
  onClose?: () => void;
}

export const ModelSwitchModal = ({
  isOpen,
  stage,
  progress,
  message,
  error,
  title = "Switching Model",
  downloadingLabel = "Downloading Model",
  loadingLabel = "Loading Weights",
  onRetry,
  onClose,
}: ModelSwitchModalProps) => {
  if (!isOpen) return null;

  const canClose = stage === "complete" || stage === "error";

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="bg-[var(--color-retro-bg)] border-4 border-black rounded-2xl p-8 max-w-md w-full mx-4 retro-shadow">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <div>
            <h2 className="text-xl font-black uppercase tracking-wide">{title}</h2>
          </div>
        </div>

        {/* Progress Section */}
        <div className="space-y-4">
          {/* Stage indicator */}
          <div className="flex items-center gap-3">
            {stage === "downloading" && (
              <>
                <Download className="w-5 h-5 text-blue-600 animate-bounce" />
                <span className="font-bold text-blue-600 uppercase text-sm">{downloadingLabel}</span>
              </>
            )}
            {stage === "loading" && (
              <>
                <Loader2 className="w-5 h-5 text-purple-600 animate-spin" />
                <span className="font-bold text-purple-600 uppercase text-sm">{loadingLabel}</span>
              </>
            )}
            {stage === "complete" && (
              <>
                <Check className="w-5 h-5 text-green-600" />
                <span className="font-bold text-green-600 uppercase text-sm">Complete!</span>
              </>
            )}
            {stage === "error" && (
              <>
                <AlertCircle className="w-5 h-5 text-red-600" />
                <span className="font-bold text-red-600 uppercase text-sm">Error</span>
              </>
            )}
          </div>

          {/* Progress bar */}
          {(stage === "downloading" || stage === "loading") && (
            <div className="w-full bg-gray-200 rounded-full h-4 border-2 border-black overflow-hidden">
              <div
                className={`h-full transition-all duration-300 ${
                  stage === "downloading" ? "bg-blue-500" : "bg-purple-500"
                }`}
                style={{ width: `${Math.min(100, progress * 100)}%` }}
              />
            </div>
          )}

          {/* Message */}
          <div className="rounded-xl p-4">
            <p className="font-mono text-sm text-gray-700">
              {error || message || "Processing..."}
            </p>
          </div>

          {/* Progress percentage */}
          {(stage === "downloading" || stage === "loading") && (
            <div className="text-center">
              <span className="font-black text-2xl">{Math.round(progress * 100)}%</span>
            </div>
          )}
        </div>

        {/* Actions */}
        {canClose && (
          <div className="mt-6 flex gap-3">
            {stage === "error" && onRetry && (
              <button
                onClick={onRetry}
                className="retro-btn flex-1 bg-purple-500 text-white"
              >
                Retry
              </button>
            )}
            {onClose && (
              <button
                onClick={onClose}
                className={`retro-btn flex-1 ${stage === "complete" ? "retro-btn-green" : ""}`}
              >
                {stage === "complete" ? "Done" : "Close"}
              </button>
            )}
          </div>
        )}

        {/* Warning */}
        {!canClose && (
          <div className="mt-6 text-center">
            <p className="text-xs text-gray-500 font-mono">
              ⚠️ Do not close this window or the app
            </p>
          </div>
        )}
      </div>
    </div>
  );
};
