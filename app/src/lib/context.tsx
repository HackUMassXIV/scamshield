import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { edge } from "./api";
import { loadSettings, saveSettings, type Settings } from "./config";
import type { AppConfig } from "./types";

type Ctx = { s: Settings; set: (p: Partial<Settings>) => void; cfg: AppConfig | null; cfgError: string | null; reload: () => void };
const C = createContext<Ctx | null>(null);

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [s, setS] = useState<Settings>(loadSettings);
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  const [cfgError, setErr] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let live = true;
    edge<AppConfig>(s, "/api/app-config")
      .then((c) => live && (setCfg(c), setErr(null)))
      .catch((e) => live && setErr(e.message));
    return () => {
      live = false;
    };
  }, [s.edgeUrl, s.guardianKey, nonce]);

  const set = (p: Partial<Settings>) =>
    setS((old) => {
      const next = { ...old, ...p };
      saveSettings(next);
      return next;
    });
  return <C.Provider value={{ s, set, cfg, cfgError, reload: () => setNonce((n) => n + 1) }}>{children}</C.Provider>;
}

export function useApp(): Ctx {
  const c = useContext(C);
  if (!c) throw new Error("useApp outside SettingsProvider");
  return c;
}
