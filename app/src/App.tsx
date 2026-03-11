import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { Layout } from "./components/Layout";
import { Playground } from "./pages/Playground";
import { UsersPage } from "./pages/Users";
import { Conversations } from "./pages/Conversations";
import { Settings } from "./pages/Settings";
import { TestPage } from "./pages/Test";
import { ChatModePage } from "./pages/ChatMode";
import { SetupPage } from "./pages/Setup";
import { VoicesPage } from "./pages/Voices";
import { api } from "./api";
import { STARTUP_DEFAULT_MESSAGE } from "./constants";
import "./App.css";

function SetupGate() {
  const [checking, setChecking] = useState(true);
  const [needsSetup, setNeedsSetup] = useState(false);
  const [backendReady, setBackendReady] = useState(false);
  const [startupMsg, setStartupMsg] = useState<string>(STARTUP_DEFAULT_MESSAGE);

  useEffect(() => {
    let cancelled = false;

    const checkFirstLaunch = async () => {
      try {
        const isFirst = await invoke<boolean>("is_first_launch");
        if (!cancelled) setNeedsSetup(isFirst);
      } catch (e) {
        console.error("Failed to check first launch:", e);
        if (!cancelled) setNeedsSetup(true);
      } finally {
        if (!cancelled) setChecking(false);
      }
    };
    checkFirstLaunch();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (checking || needsSetup) {
      return;
    }

    let cancelled = false;
    let bootKickAttempted = false;

    const waitForBackend = async () => {
      while (!cancelled) {
        try {
          const st = await api.startupStatus();
          if (!cancelled) {
            const counts = st?.counts || {};
            if (!st?.seeded) {
              setStartupMsg(
                `Seeding database... (voices: ${counts.voices ?? 0}, personalities: ${counts.personalities ?? 0})`,
              );
            } else if (!st?.pipeline_ready) {
              setStartupMsg("Starting AI engine...");
            } else {
              setStartupMsg("Ready");
            }
          }

          if (st?.ready) {
            if (!cancelled) setBackendReady(true);
            return;
          }
        } catch {
          if (!bootKickAttempted) {
            bootKickAttempted = true;
            try {
              await invoke("start_backend");
            } catch {
              // Keep polling; setup gate stays on loading until backend is reachable.
            }
          }

          if (!cancelled) setStartupMsg(STARTUP_DEFAULT_MESSAGE);
        }

        await new Promise((r) => setTimeout(r, 500));
      }
    };

    waitForBackend();

    return () => {
      cancelled = true;
    };
  }, [checking, needsSetup]);

  if (checking) {
    return (
      <div className="min-h-screen bg-[var(--color-retro-bg)] flex items-center justify-center">
        <div className="text-center retro-card">
          <Logo />
          <div className="text-gray-500 font-mono">Loading...</div>
        </div>
      </div>
    );
  }

  if (needsSetup) {
    return <Navigate to="/setup" replace />;
  }

  if (!backendReady) {
    return (
      <div className="min-h-screen bg-[var(--color-retro-bg)] flex items-center justify-center">
        <div className="text-center retro-card">
          
          <Logo />
          <div className="text-gray-500 font-mono">{startupMsg}</div>
        </div>
      </div>
    );
  }

  return <Layout />;
}

import { ActiveUserProvider } from "./state/ActiveUserContext";
import { Logo } from "./components/Logo";

function App() {
  return (
    <ActiveUserProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/setup" element={<SetupPage />} />

          <Route path="/" element={<SetupGate />}>
            <Route index element={<Playground />} />
            <Route path="playground" element={<Playground />} />
            <Route path="voices" element={<VoicesPage />} />
            <Route path="users" element={<UsersPage />} />
            <Route path="conversations" element={<Conversations />} />
            <Route path="test" element={<TestPage />} />
            <Route path="chat" element={<ChatModePage />} />
            <Route path="settings" element={<Settings />} />
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </ActiveUserProvider>
  );
}

export default App;
