import { useEffect, useRef } from "react";

export type ChatMessage = {
  id: string;
  role: "ai" | "user";
  text: string;
  timestamp: number;
};

type ChatTranscriptProps = {
  messages: ChatMessage[];
  isLive?: boolean;
  autoScroll?: boolean;
  scrollMarginTop?: number;
  emptyLabel?: string;
  className?: string;
};

export const ChatTranscript = ({
  messages,
  isLive = false,
  autoScroll = false,
  scrollMarginTop = 0,
  emptyLabel = "Experience loading...",
  className = "",
}: ChatTranscriptProps) => {
  const endRef = useRef<HTMLDivElement | null>(null);
  const lastAiRef = useRef<HTMLDivElement | null>(null);

  const lastAiId = [...messages].reverse().find((m) => m.role === "ai")?.id;

  useEffect(() => {
    if (!autoScroll) return;
    if (lastAiRef.current) {
      lastAiRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [autoScroll, messages.length, lastAiId]);

  return (
    <div className={`bg-transparent border-0 shadow-none rounded-none ${className}`}>
      {messages.length === 0 ? (
        <div className="p-8 text-center font-mono text-gray-500">{emptyLabel}</div>
      ) : (
        <div className="p-4 space-y-3">
          {messages.map((entry) => {
            const isAi = entry.role === "ai";
            const isLastAi = isAi && entry.id === lastAiId;
            return (
              <div
                key={entry.id}
                ref={isLastAi ? lastAiRef : undefined}
                style={isLastAi && scrollMarginTop ? { scrollMarginTop } : undefined}
                className={`flex ${isAi ? "justify-start" : "justify-end"}`}
              >
                <div
                  className={`max-w-[78%] rounded-2xl px-4 py-3 ${
                    isAi ? "bg-[#f0f0f0] text-gray-900" : "bg-purple-400 text-white"
                  }`}
                  style={
                    isAi && isLive
                      ? {
                          animation: "aiFadeIn 220ms ease-out",
                          animationFillMode: "both",
                        }
                      : undefined
                  }
                >
                  <div className={`text-[10px] font-bold uppercase tracking-wider mb-1 ${isAi ? "text-gray-600" : "text-white/80"}`}>
                    {isAi ? "AI" : "You"}
                  </div>
                  <div className="font-medium leading-relaxed whitespace-pre-wrap">{entry.text}</div>
                  <div className={`mt-2 font-mono text-[10px] text-right ${isAi ? "text-gray-500" : "text-white/80"}`}>
                    {new Date(entry.timestamp).toLocaleString()}
                  </div>
                </div>
              </div>
            );
          })}
          <div ref={endRef} style={scrollMarginTop ? { scrollMarginTop } : undefined} />
        </div>
      )}

      <style>{`
        @keyframes aiFadeIn {
          from { opacity: 0; transform: translateY(6px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
};
