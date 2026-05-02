"use client";

import { useEffect, useRef, useState } from "react";
import type { StreamTick, BotName } from "@/types";

/**
 * useLiveStream — opens a single SSE connection to /stream/{bot} and
 * keeps the latest tick in component state. Returns:
 *   - tick:        the most recent StreamTick (null until first message)
 *   - connected:   true when the EventSource is open
 *   - lastError:   short error string if connection failed
 *
 * Re-opens automatically when apiBase or bot changes. Closes on unmount.
 *
 * Why SSE over polling: the backend pushes one event/second carrying
 * indices + engine_state + near_miss + pnl + status, so we replace
 * 5 polling loops with one persistent connection.
 */
export function useLiveStream(apiBase: string, bot: BotName) {
  const [tick, setTick] = useState<StreamTick | null>(null);
  const [connected, setConnected] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!apiBase) return;

    const url = `${apiBase}/stream/${bot}`;
    const es = new EventSource(url);
    sourceRef.current = es;

    es.onopen = () => {
      setConnected(true);
      setLastError(null);
    };

    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as StreamTick;
        if (data.type === "error") {
          setLastError(data.message ?? "stream error");
          return;
        }
        setTick(data);
        setLastError(null);
      } catch (e) {
        setLastError(e instanceof Error ? e.message : String(e));
      }
    };

    es.onerror = () => {
      // EventSource auto-reconnects (browser-driven). We just surface
      // the connection state so the UI can show an OFFLINE pill.
      setConnected(false);
    };

    return () => {
      es.close();
      sourceRef.current = null;
      setConnected(false);
    };
  }, [apiBase, bot]);

  return { tick, connected, lastError };
}
