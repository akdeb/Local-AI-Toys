const API_BASE = (import.meta as any).env?.VITE_API_BASE || "http://127.0.0.1:8000";

const request = async (path: string, init?: RequestInit) => {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    const contentType = res.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      try {
        const data: any = await res.json();
        const msg =
          (typeof data?.detail === "string" && data.detail) ||
          (typeof data?.message === "string" && data.message) ||
          (typeof data?.error === "string" && data.error) ||
          "";
        const err: any = new Error(msg || `Request failed: ${res.status}`);
        err.status = res.status;
        throw err;
      } catch (e: any) {
        const err: any = new Error(e?.message || `Request failed: ${res.status}`);
        err.status = res.status;
        throw err;
      }
    }

    const text = await res.text().catch(() => "");
    const err: any = new Error(text || `Request failed: ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
};

export const api = {
  health: async () => {
    return request(`/health`);
  },

  getNetworkInfo: async () => {
    return request(`/network-info`);
  },

  restartMdns: async () => {
    return request(`/restart-mdns`, {
      method: "POST",
    });
  },

  startupStatus: async () => {
    return request(`/startup-status`);
  },

  getVoices: async () => {
    return request(`/voices`);
  },

  downloadVoice: async (voiceId: string) => {
    return request(`/assets/voices/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice_id: voiceId }),
    });
  },

  listDownloadedVoices: async () => {
    const res = await request(`/assets/voices/list`);
    return Array.isArray(res?.voices) ? res.voices : [];
  },

  readVoiceBase64: async (voiceId: string) => {
    const res = await request(`/assets/voices/${encodeURIComponent(voiceId)}/base64`);
    return typeof res?.base64 === "string" ? res.base64 : null;
  },

  saveExperienceImageBase64: async (experienceId: string, base64Image: string, ext?: string | null) => {
    return request(`/assets/images/save`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        experience_id: experienceId,
        base64_image: base64Image,
        ext: ext || null,
      }),
    });
  },

  getActiveUser: async () => {
    return request(`/active-user`);
  },

  setActiveUser: async (userId: string | null) => {
    return request(`/active-user`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId }),
    });
  },

  getAppMode: async () => {
    return request(`/app-mode`);
  },

  setAppMode: async (mode: string) => {
    return request(`/app-mode`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
  },

  // Experiences (personalities, games, stories)
  getExperiences: async (includeHidden = false, type?: 'personality' | 'game' | 'story') => {
    const params = new URLSearchParams();
    if (includeHidden) params.set('include_hidden', 'true');
    if (type) params.set('type', type);
    const qs = params.toString() ? `?${params.toString()}` : '';
    return request(`/experiences${qs}`);
  },

  createExperience: async (data: {
    name: string;
    prompt: string;
    short_description?: string;
    tags?: string[];
    voice_id: string;
    type: 'personality' | 'game' | 'story';
    img_src?: string;
  }) => {
    return request(`/experiences`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  },

  generateExperience: async (description: string, type: 'personality' | 'game' | 'story' = 'personality', voice_id?: string) => {
    return request(`/experiences/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description, type, voice_id }),
    });
  },

  updateExperience: async (id: string, data: any) => {
    return request(`/experiences/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  },

  deleteExperience: async (id: string) => {
    return request(`/experiences/${id}`, {
      method: "DELETE",
    });
  },

  // Personalities (backward compatible)
  getPersonalities: async (includeHidden = false) => {
    const qs = includeHidden ? `?include_hidden=true` : ``;
    return request(`/personalities${qs}`);
  },
  createPersonality: async (data: any) => {
    return request(`/personalities`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  },

  generatePersonality: async (description: string) => {
    return request(`/personalities/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description }),
    });
  },

  generatePersonalityWithVoice: async (description: string, voice_id: string) => {
    return request(`/personalities/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description, voice_id }),
    });
  },

  updatePersonality: async (id: string, data: any) => {
    return request(`/personalities/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  },

  deletePersonality: async (id: string) => {
    return request(`/personalities/${id}`, {
      method: "DELETE",
    });
  },

  // Voices
  createVoice: async (data: { voice_id: string; voice_name: string; voice_description?: string | null }) => {
    return request(`/voices`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  },

  // Users
  getUsers: async () => {
    return request(`/users`);
  },
  createUser: async (data: any) => {
    return request(`/users`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  },

  updateUser: async (id: string, data: any) => {
    return request(`/users/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  },

  // Conversations
  getConversations: async (limit = 50, offset = 0) => {
    return request(`/conversations?limit=${limit}&offset=${offset}`);
  },

  getConversationsBySession: async (sessionId: string) => {
    return request(`/conversations?session_id=${encodeURIComponent(sessionId)}`);
  },

  getSessions: async (limit = 50, offset = 0, userId?: string | null) => {
    const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (userId) qs.set("user_id", userId);
    return request(`/sessions?${qs.toString()}`);
  },

  getDeviceStatus: async () => {
    return request(`/device`);
  },

  updateDevice: async (data: any) => {
    return request(`/device`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  },
  disconnectDevice: async () => {
    return request(`/device/disconnect`, {
      method: "POST",
    });
  },

  getModels: async () => {
    return request(`/models`);
  },

  setModels: async (data: { model_repo: string }) => {
    return request(`/models`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  },

  firmwarePorts: async () => {
    return request(`/firmware/ports`);
  },

  // Settings (app_state)
  getSetting: async (key: string) => {
    return request(`/settings/${encodeURIComponent(key)}`);
  },

  setSetting: async (key: string, value: string | null) => {
    return request(`/settings/${encodeURIComponent(key)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    });
  },

  flashFirmware: async (data: { port: string; baud?: number; chip?: string }) => {
    return request(`/firmware/flash`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  },

  /**
   * Switch to a new LLM model. Downloads the model first, then hot-swaps it.
   * Returns an async generator that yields progress updates.
   */
  switchModel: async function* (modelRepo: string): AsyncGenerator<{
    stage: "downloading" | "loading" | "complete" | "error";
    progress?: number;
    message?: string;
    error?: string;
  }> {
    const res = await fetch(`${API_BASE}/models/switch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_repo: modelRepo }),
    });

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      yield { stage: "error", error: text || `Request failed: ${res.status}` };
      return;
    }

    const reader = res.body?.getReader();
    if (!reader) {
      yield { stage: "error", error: "No response body" };
      return;
    }

    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.trim()) {
          try {
            const data = JSON.parse(line);
            yield data;
          } catch {
            // Skip malformed lines
          }
        }
      }
    }

    // Process any remaining buffer
    if (buffer.trim()) {
      try {
        const data = JSON.parse(buffer);
        yield data;
      } catch {
        // Skip malformed data
      }
    }
  },

  switchTts: async function* (ttsBackend: "chatterbox-turbo" | "qwen3-tts"): AsyncGenerator<{
    stage: "downloading" | "loading" | "complete" | "error";
    progress?: number;
    message?: string;
    error?: string;
  }> {
    const res = await fetch(`${API_BASE}/models/switch-tts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tts_backend: ttsBackend }),
    });

    // Backward-compatible fallback for older backends that don't expose /models/switch-tts yet.
    if (res.status === 404) {
      try {
        yield {
          stage: "downloading",
          progress: 0.1,
          message: "Preparing voice engine...",
        };
        yield {
          stage: "loading",
          progress: 0.4,
          message: "Applying voice engine...",
        };
        await request(`/settings/tts_backend`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value: ttsBackend }),
        });
        yield {
          stage: "loading",
          progress: 1.0,
          message: "Voice engine loaded!",
        };
        yield {
          stage: "complete",
          progress: 1.0,
          message: `Switched to ${ttsBackend}`,
        };
      } catch (e: any) {
        yield {
          stage: "error",
          error: e?.message || "Failed to switch TTS backend",
        };
      }
      return;
    }

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      yield { stage: "error", error: text || `Request failed: ${res.status}` };
      return;
    }

    const reader = res.body?.getReader();
    if (!reader) {
      yield { stage: "error", error: "No response body" };
      return;
    }

    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          yield JSON.parse(line);
        } catch {
          // Skip malformed lines
        }
      }
    }

    if (buffer.trim()) {
      try {
        yield JSON.parse(buffer);
      } catch {
        // Skip malformed data
      }
    }
  },
};
