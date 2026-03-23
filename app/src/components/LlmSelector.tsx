import { useMemo } from "react";
import llms from "../assets/llms.json";

export type LlmOption = {
  id: string;
  name: string;
  repo_id: string;
  params: string;
  quantization: string;
  specialty: string;
  thinking?: boolean;
};

type Props = {
  value: string;
  onChange: (repoId: string) => void;
  disabled?: boolean;
  label?: string;
  systemMemoryGb?: number | null;
};

const OPTIONS = llms as LlmOption[];

const parseParamBillions = (params: string): number => {
  const raw = String(params || "").trim().toUpperCase();
  const n = Number(raw.replace("B", ""));
  return Number.isFinite(n) ? n : 0;
};

const estimateUsage = (opt: LlmOption): { ramGb: number; diskGb: number } => {
  const p = parseParamBillions(opt.params);
  const q = String(opt.quantization || "").toLowerCase();
  const is4Bit = q.includes("4bit") || q.includes("mxfp4");
  const isBf16 = q.includes("bf16");

  // Conservative sizing for parent-friendly guidance.
  const ramGb = Math.max(3, Math.ceil((is4Bit ? p * 1.2 : isBf16 ? p * 2.6 : p * 2.0) + 2));
  const diskGb = Math.max(1, Math.ceil(is4Bit ? p * 0.7 : isBf16 ? p * 2.2 : p * 1.6));
  return { ramGb, diskGb };
};

const fitLabel = (ramNeed: number, systemMemoryGb?: number | null): string | null => {
  if (!systemMemoryGb) return null;
  if (ramNeed <= Math.floor(systemMemoryGb * 0.45)) return "Good fit";
  if (ramNeed <= Math.floor(systemMemoryGb * 0.7)) return "Possible";
  return "Heavy";
};

export const LlmSelector = ({
  value,
  onChange,
  disabled,
  label = "Model",
  systemMemoryGb,
}: Props) => {
  const presetMatch = useMemo(
    () => OPTIONS.find((opt) => opt.repo_id === value),
    [value]
  );
  const selectedInfo = presetMatch;
  const selectedUsage = selectedInfo ? estimateUsage(selectedInfo) : null;
  const selectedFit = selectedUsage ? fitLabel(selectedUsage.ramGb, systemMemoryGb) : null;

  return (
    <div className="space-y-2">
      {label ? (
        <label className="font-bold mb-2 uppercase text-xs opacity-40">{label}</label>
      ) : null}

      <div className="flex gap-2">
        <select
          className="retro-input bg-white flex-1 border border-gray-200"
          value={presetMatch?.repo_id || ""}
          onChange={(e) => {
            onChange(e.target.value);
          }}
          disabled={disabled}
        >
          <option value="" disabled>
            Select a model…
          </option>
          {OPTIONS.map((opt) => (
            <option key={opt.id} value={opt.repo_id}>
              {opt.name}
            </option>
          ))}
        </select>
      </div>

      {selectedInfo && selectedUsage && (
        <div className="text-xs text-gray-600">
          {selectedFit ? `${selectedFit}` : ""} ·
          ~{selectedUsage.ramGb}GB RAM · ~{selectedUsage.diskGb}GB Disk
          · {selectedInfo.params} params
        </div>
      )}
    </div>
  );
};
