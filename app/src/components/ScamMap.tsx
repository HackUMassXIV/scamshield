import L from "leaflet";
import { useEffect, useRef } from "react";

export type Report = { id: string; scam_type: string; label: string; area: string; note: string; lat: number; lng: number; ts: number; seeded: boolean };

export const TYPE_COLOR: Record<string, string> = {
  immigration_uscis: "#D3263A", irs_gov: "#B4462B", rental_deposit: "#0D5C6B", fake_job_check: "#6B4FA3",
  bank_impersonation: "#1F6FB2", tech_support: "#5E6B7A", family_emergency: "#C27C0E",
};

export function ScamMap({ reports }: { reports: Report[] }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!el.current || map.current) return;
    map.current = L.map(el.current, { scrollWheelZoom: false }).setView([42.3895, -72.5265], 15);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map.current);
    layer.current = L.layerGroup().addTo(map.current);
    return () => {
      map.current?.remove();
      map.current = null;
    };
  }, []);

  const fitted = useRef(false);
  useEffect(() => {
    const g = layer.current;
    if (!g) return;
    g.clearLayers();
    if (!fitted.current && reports.length > 1 && map.current) {
      map.current.fitBounds(L.latLngBounds(reports.map((r) => [r.lat, r.lng] as [number, number])), { padding: [24, 24] });
      fitted.current = true;
    }
    const now = Date.now() / 1000;
    for (const r of reports) {
      const fresh = now - r.ts < 3600;
      L.circleMarker([r.lat, r.lng], {
        radius: fresh ? 11 : 7, color: "#fff", weight: 2, fillColor: TYPE_COLOR[r.scam_type] ?? "#18222E", fillOpacity: 0.9,
      })
        .bindPopup(`<b>${r.label}</b><br>${r.area} · ${new Date(r.ts * 1000).toLocaleDateString()}${r.note ? `<br>${r.note.replace(/</g, "&lt;")}` : ""}`)
        .addTo(g);
    }
  }, [reports]);

  return <div ref={el} className="h-[360px] w-full overflow-hidden rounded-xl" role="region" aria-label="Campus scam map" />;
}
