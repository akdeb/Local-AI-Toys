import { useEffect, useMemo, useRef, useState } from "react";
import { X, Image as ImageIcon, Mic } from "lucide-react";
import { api } from "../api";
import { invoke } from "@tauri-apps/api/core";

type Props = {
  open: boolean;
  onClose: () => void;
  onCreated: (voiceId: string) => Promise<void> | void;
};

export const VoiceClone = ({ open, onClose, onCreated }: Props) => {
  const [cloneName, setCloneName] = useState("");
  const [cloneDesc, setCloneDesc] = useState("");
  const [cloneFile, setCloneFile] = useState<File | null>(null);
  const [clonePreviewUrl, setClonePreviewUrl] = useState<string | null>(null);
  const [creatingVoice, setCreatingVoice] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [recording, setRecording] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState<number>(12);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const recordChunksRef = useRef<Blob[]>([]);
  const recordStopTimeoutRef = useRef<number | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<number | null>(null);

  const recordScript =
    "Quick brown foxes jump; brave wizards sing softly. Now: excited, curious, then calm—can you hear the difference?";

  useEffect(() => {
    if (!open) return;
    setSecondsLeft(12);
    setCreateError(null);
  }, [open]);

  useEffect(() => {
    return () => {
      if (clonePreviewUrl) URL.revokeObjectURL(clonePreviewUrl);
    };
  }, [clonePreviewUrl]);

  const cleanupRecordingStream = () => {
    if (mediaStreamRef.current) {
      try {
        mediaStreamRef.current.getTracks().forEach((t) => t.stop());
      } catch {
        // ignore
      }
      mediaStreamRef.current = null;
    }
  };

  const stopRecorder = async () => {
    if (recordStopTimeoutRef.current) {
      window.clearTimeout(recordStopTimeoutRef.current);
      recordStopTimeoutRef.current = null;
    }
    if (timerRef.current) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }

    const rec = recorderRef.current;
    if (!rec) return;
    try {
      if (rec.state !== "inactive") rec.stop();
    } catch {
      // ignore
    }
  };

  const encodeWav16 = (samples: Float32Array, sampleRate: number) => {
    const numChannels = 1;
    const bytesPerSample = 2;
    const blockAlign = numChannels * bytesPerSample;
    const byteRate = sampleRate * blockAlign;
    const dataSize = samples.length * bytesPerSample;
    const buffer = new ArrayBuffer(44 + dataSize);
    const view = new DataView(buffer);

    const writeStr = (off: number, s: string) => {
      for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i));
    };

    writeStr(0, "RIFF");
    view.setUint32(4, 36 + dataSize, true);
    writeStr(8, "WAVE");
    writeStr(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, numChannels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, byteRate, true);
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, 16, true);
    writeStr(36, "data");
    view.setUint32(40, dataSize, true);

    let offset = 44;
    for (let i = 0; i < samples.length; i++) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      offset += 2;
    }

    return buffer;
  };

  const decodeToWavFile = async (blob: Blob) => {
    const buf = await blob.arrayBuffer();
    const ctx = new AudioContext();
    const audio = await ctx.decodeAudioData(buf.slice(0));
    const input = audio.getChannelData(0);

    const targetRate = 16000;
    const ratio = targetRate / audio.sampleRate;
    const outLen = Math.max(1, Math.round(input.length * ratio));
    const resampled = new Float32Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const src = i / ratio;
      const idx0 = Math.floor(src);
      const idx1 = Math.min(idx0 + 1, input.length - 1);
      const frac = src - idx0;
      resampled[i] = input[idx0] * (1 - frac) + input[idx1] * frac;
    }

    await ctx.close();

    const wav = encodeWav16(resampled, targetRate);
    return new File([wav], "recording.wav", { type: "audio/wav" });
  };

  const fileToBase64 = async (file: File): Promise<string> => {
    const buf = await file.arrayBuffer();
    let binary = "";
    const bytes = new Uint8Array(buf);
    const chunkSize = 0x8000;
    for (let i = 0; i < bytes.length; i += chunkSize) {
      const chunk = bytes.subarray(i, i + chunkSize);
      binary += String.fromCharCode(...chunk);
    }
    return btoa(binary);
  };

  const slugify = (s: string) =>
    s
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/(^-|-$)/g, "");

  const startRecord10s = async () => {
    if (recording) return;

    setSecondsLeft(12);

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaStreamRef.current = stream;

    recordChunksRef.current = [];
    const rec = new MediaRecorder(stream);
    recorderRef.current = rec;
    setRecording(true);

    rec.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) recordChunksRef.current.push(e.data);
    };

    rec.onstop = async () => {
      setRecording(false);
      recorderRef.current = null;

      const blob = new Blob(recordChunksRef.current, { type: rec.mimeType || "audio/webm" });
      recordChunksRef.current = [];

      cleanupRecordingStream();

      const wavFile = await decodeToWavFile(blob);
      setCloneFile(wavFile);
      if (clonePreviewUrl) URL.revokeObjectURL(clonePreviewUrl);
      setClonePreviewUrl(URL.createObjectURL(wavFile));
    };

    rec.start();

    timerRef.current = window.setInterval(() => {
      setSecondsLeft((s) => Math.max(0, s - 1));
    }, 1000);

    recordStopTimeoutRef.current = window.setTimeout(() => {
      void stopRecorder();
    }, 12_000);
  };

  const chooseLabel = useMemo(() => {
    if (!cloneFile) return "Choose file";
    return cloneFile.name;
  }, [cloneFile]);

  const createVoiceClone = async () => {
    if (!cloneFile) return;
    if (!cloneName.trim()) return;

    setCreatingVoice(true);
    setCreateError(null);
    try {
      const uuid = globalThis.crypto?.randomUUID ? globalThis.crypto.randomUUID() : String(Date.now());
      const voiceId = `${slugify(cloneName)}-${uuid}`;
      const b64 = await fileToBase64(cloneFile);
      await invoke<string>("save_voice_wav_base64", { voiceId, base64Wav: b64 });
      await api.createVoice({ voice_id: voiceId, voice_name: cloneName.trim(), voice_description: cloneDesc.trim() || null });

      setCloneName("");
      setCloneDesc("");
      setCloneFile(null);
      if (clonePreviewUrl) URL.revokeObjectURL(clonePreviewUrl);
      setClonePreviewUrl(null);

      await onCreated(voiceId);
      onClose();
    } catch (e: any) {
      const detail =
        typeof e === "string"
          ? e
          : e?.message
            ? String(e.message)
            : e?.toString
              ? String(e.toString())
              : "Failed to create voice clone";
      setCreateError(detail);
    } finally {
      setCreatingVoice(false);
    }
  };

  useEffect(() => {
    if (!open) {
      void stopRecorder();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    return () => {
      void stopRecorder();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 backdrop-blur-sm flex items-center justify-center p-6">
      <button
        type="button"
        aria-label="Close"
        className="absolute inset-0 bg-black/40"
        onClick={() => {
          void stopRecorder();
          onClose();
        }}
      />

      <div className="relative w-full max-w-3xl retro-card">
        <div className="flex items-start justify-between gap-4 mb-4">
          <div className="text-xl font-black uppercase">Create Voice Clone</div>
          <button
            type="button"
            className="retro-icon-btn"
            onClick={() => {
              void stopRecorder();
              onClose();
            }}
            aria-label="Close"
          >
            <X />
          </button>
        </div>

        <div className="space-y-4">
          {createError && (
            <div className="font-mono text-sm text-red-600">{createError}</div>
          )}
          <div className="font-mono text-xs text-gray-700">
            Upload or record a clean sample. Best results with: 1. quiet room, 2. steady volume, 3. no background music.
          </div>

          <div>
            <label className="block font-bold mb-2 text-sm">AUDIO SAMPLE (.wav)</label>

            <div className="flex items-start gap-3">
              <label className="block flex-1 w-1/2 min-w-0">
                <input
                  type="file"
                  accept=".wav,audio/wav"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0] || null;

                    if (!f) {
                      setCloneFile(null);
                      if (clonePreviewUrl) URL.revokeObjectURL(clonePreviewUrl);
                      setClonePreviewUrl(null);
                      return;
                    }

                    const looksLikeWav =
                      /\.wav$/i.test(f.name) || String(f.type).toLowerCase() === 'audio/wav' || String(f.type).toLowerCase() === 'audio/x-wav';
                    if (!looksLikeWav) {
                      e.currentTarget.value = '';
                      setCloneFile(null);
                      if (clonePreviewUrl) URL.revokeObjectURL(clonePreviewUrl);
                      setClonePreviewUrl(null);
                      return;
                    }

                    setCloneFile(f);
                    if (clonePreviewUrl) URL.revokeObjectURL(clonePreviewUrl);
                    setClonePreviewUrl(URL.createObjectURL(f));
                  }}
                />

                <div className="retro-card-outline cursor-pointer flex items-center justify-between gap-3 px-4 py-3">
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="w-10 h-10 rounded-[12px] retro-dotted bg-white flex items-center justify-center shrink-0">
                      <ImageIcon size={16} className="text-gray-600" />
                    </div>
                    <div className="min-w-0">
                      <div className="font-bold text-sm">{cloneFile ? 'Selected' : 'Choose file'}</div>
                      <div className="font-mono text-xs text-gray-700 truncate">{chooseLabel}</div>
                    </div>
                  </div>
                </div>
              </label>

              <div className="relative w-10 self-stretch flex items-center justify-center">
                <div className="absolute inset-y-0 left-1/2 -translate-x-1/2 border-l-2 border-gray-300" />
                <div className="font-mono text-xs font-bold bg-white px-2 py-1">OR</div>
              </div>

              <div className="shrink-0 flex w-1/2 flex-col gap-3">
                <div className="flex items-center gap-3">
                  <button type="button" className="retro-btn" style={{ textTransform: 'none' }} onClick={startRecord10s} disabled={recording}>
                    <span className="inline-flex items-center gap-2">
                      <Mic size={16} />
                      {recording ? 'Recording…' : 'Record 12s'}
                    </span>
                  </button>
                  <div className="font-mono text-sm w-10 text-right">
                    {recording ? `${String(secondsLeft).padStart(2, '0')}s` : ''}
                  </div>
                </div>

                <div className="font-mono text-xs text-gray-700"><strong>Try reading:</strong> {recordScript}</div>
              </div>
            </div>
          </div>

          {clonePreviewUrl && (
            <div>
              <label className="block font-bold mb-2 uppercase text-sm">Preview</label>
              <audio controls src={clonePreviewUrl} className="w-full" />
            </div>
          )}

  <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block font-bold mb-2 uppercase text-sm">Name</label>
            <input
              className="retro-input"
              value={cloneName}
              onChange={(e) => setCloneName(e.target.value)}
              placeholder="Winnie the Pooh"
            />
          </div>

          <div>
            <label className="block font-bold mb-2 uppercase text-sm">Short Description</label>
            <input
              className="retro-input"
              value={cloneDesc}
              onChange={(e) => setCloneDesc(e.target.value)}
              placeholder="Bear-like cartoon voice"
            />
          </div>
          </div>

          <div className="flex justify-end">
            <button
              type="button"
              className="retro-btn"
              onClick={createVoiceClone}
              disabled={creatingVoice || !cloneFile || !cloneName.trim()}
            >
              {creatingVoice ? 'Saving…' : 'Submit'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
