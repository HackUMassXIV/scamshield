import { useEffect, useRef, useState } from "react";

/** Reconnecting JSON websocket. `onEvent` always sees the latest closure. */
export function useSocket(url: string | null, onEvent: (ev: any) => void) {
  const [status, setStatus] = useState<"connecting" | "open" | "closed">("connecting");
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    if (!url) return;
    let ws: WebSocket | null = null;
    let stop = false;
    let delay = 500;
    let timer: number | undefined;
    const connect = () => {
      setStatus("connecting");
      try {
        ws = new WebSocket(url);
      } catch {
        timer = window.setTimeout(connect, delay);
        return;
      }
      ws.onopen = () => {
        delay = 500;
        setStatus("open");
      };
      ws.onmessage = (m) => {
        try {
          handler.current(JSON.parse(m.data));
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        setStatus("closed");
        if (!stop) {
          timer = window.setTimeout(connect, delay);
          delay = Math.min(delay * 2, 8000);
        }
      };
    };
    connect();
    return () => {
      stop = true;
      window.clearTimeout(timer);
      ws?.close();
    };
  }, [url]);

  return status;
}
