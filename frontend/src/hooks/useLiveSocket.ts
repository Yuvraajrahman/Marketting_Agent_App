import { useCallback, useEffect, useRef, useState } from "react";
import type { InboundMessage, OutboundMessage } from "../types";

function wsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.hostname;
  if (import.meta.env.DEV) {
    return `${proto}://${host}:8000/ws`;
  }
  return `${proto}://${window.location.host}/ws`;
}

export function useLiveSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<InboundMessage | null>(null);
  const handlersRef = useRef<Set<(msg: InboundMessage) => void>>(new Set());
  const retryRef = useRef(0);
  const timerRef = useRef<number | null>(null);
  const queueRef = useRef<OutboundMessage[]>([]);

  const subscribe = useCallback((fn: (msg: InboundMessage) => void) => {
    handlersRef.current.add(fn);
    return () => {
      handlersRef.current.delete(fn);
    };
  }, []);

  const flushQueue = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const pending = queueRef.current;
    queueRef.current = [];
    for (const msg of pending) {
      ws.send(JSON.stringify(msg));
    }
  }, []);

  const send = useCallback((msg: OutboundMessage) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      queueRef.current.push(msg);
      return false;
    }
    ws.send(JSON.stringify(msg));
    return true;
  }, []);

  useEffect(() => {
    let closed = false;

    const connect = () => {
      if (closed) return;
      const ws = new WebSocket(wsUrl());
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        retryRef.current = 0;
        ws.send(JSON.stringify({ channel: "system", action: "get_config" }));
        flushQueue();
      };

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data) as InboundMessage;
          setLastMessage(msg);
          handlersRef.current.forEach((h) => h(msg));
        } catch {
          /* ignore */
        }
      };

      ws.onclose = () => {
        setConnected(false);
        wsRef.current = null;
        if (closed) return;
        const delay = Math.min(8000, 500 * 2 ** retryRef.current);
        retryRef.current += 1;
        timerRef.current = window.setTimeout(connect, delay);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connect();

    return () => {
      closed = true;
      if (timerRef.current) window.clearTimeout(timerRef.current);
      wsRef.current?.close();
    };
  }, [flushQueue]);

  return { connected, lastMessage, send, subscribe };
}
