import { useState } from "react";
import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import { SettingsSheet } from "./components/SettingsSheet";
import { SettingsProvider } from "./lib/context";
import Bank from "./pages/Bank";
import Guardian from "./pages/Guardian";
import Ops from "./pages/Ops";

function Nav({ onSettings }: { onSettings: () => void }) {
  const link = ({ isActive }: { isActive: boolean }) =>
    `rounded-lg px-3 py-1.5 font-bold ${isActive ? "bg-teal text-white" : "text-teal hover:bg-teal/10"}`;
  return (
    <header className="sticky top-0 z-[500] border-b border-line bg-fog/90 backdrop-blur">
      <nav className="mx-auto flex max-w-6xl items-center gap-1 px-4 py-2" aria-label="Main">
        <span className="mr-auto flex items-center gap-2 font-bold">
          <img src="/icons/icon-192.png" alt="" className="size-7 rounded-md" />
          <span className="hidden sm:inline">ScamShield</span>
        </span>
        <NavLink to="/guardian" className={link}>Guardian</NavLink>
        <NavLink to="/bank" className={link}>Bank</NavLink>
        <NavLink to="/ops" className={link}>Ops</NavLink>
        <button onClick={onSettings} className="ml-1 rounded-lg px-2 py-1.5 text-teal hover:bg-teal/10" aria-label="Settings">
          <svg viewBox="0 0 24 24" className="size-6" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
          </svg>
        </button>
      </nav>
    </header>
  );
}

export default function App() {
  const [open, setOpen] = useState(false);
  return (
    <SettingsProvider>
      <Nav onSettings={() => setOpen(true)} />
      <Routes>
        <Route path="/guardian" element={<Guardian />} />
        <Route path="/bank" element={<Bank />} />
        <Route path="/ops" element={<Ops />} />
        <Route path="*" element={<Navigate to="/guardian" replace />} />
      </Routes>
      {open && <SettingsSheet onClose={() => setOpen(false)} />}
    </SettingsProvider>
  );
}
