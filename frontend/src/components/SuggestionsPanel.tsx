import { useEffect, useState } from "react";
import type { TranscriptTurn } from "../types";
import type { useLiveSocket } from "../hooks/useLiveSocket";

type Socket = ReturnType<typeof useLiveSocket>;

interface Props {
  socket: Socket;
}

const STYLES = [
  { id: "rapport-building", label: "Rapport" },
  { id: "objection-handling", label: "Objections" },
  { id: "closing", label: "Closing" },
] as const;

export function SuggestionsPanel({ socket }: Props) {
  const [enabled, setEnabled] = useState(false);
  const [style, setStyle] = useState<string>("rapport-building");
  const [suggestion, setSuggestion] = useState("");
  const [turns, setTurns] = useState<TranscriptTurn[]>([]);
  const [streaming, setStreaming] = useState(false);

  useEffect(() => {
    return socket.subscribe((msg) => {
      if (msg.channel === "assistant" && msg.type === "suggestion" && typeof msg.text === "string") {
        setSuggestion(msg.text);
        setStreaming(!msg.done);
      }
      if (msg.channel === "stt" && msg.type === "transcript" && msg.text) {
        setTurns((prev) => [
          ...prev.slice(-40),
          { speaker: String(msg.speaker ?? "you"), text: String(msg.text), ts: Number(msg.ts) || Date.now() / 1000 },
        ]);
      }
      if (msg.channel === "assistant" && msg.type === "ack" && msg.action === "toggle") {
        if (typeof msg.enabled === "boolean") setEnabled(msg.enabled);
      }
    });
  }, [socket]);

  const toggle = () => {
    const next = !enabled;
    setEnabled(next);
    socket.send({ channel: "assistant", action: "toggle", enabled: next });
  };

  const refresh = () => {
    const transcript = turns
      .map((t) => `${t.speaker === "you" ? "You" : "Client"}: ${t.text}`)
      .join("\n");
    socket.send({ channel: "assistant", action: "suggest", transcript });
    setStreaming(true);
  };

  return (
    <section className="panel suggestions-panel">
      <header className="panel-head">
        <h2>Suggestions</h2>
        <button type="button" className={enabled ? "btn on" : "btn"} onClick={toggle}>
          {enabled ? "On" : "Off"}
        </button>
      </header>

      <div className="style-row">
        {STYLES.map((s) => (
          <button
            key={s.id}
            type="button"
            className={style === s.id ? "btn tiny on" : "btn tiny"}
            onClick={() => {
              setStyle(s.id);
              socket.send({ channel: "assistant", action: "set_style", style: s.id });
            }}
          >
            {s.label}
          </button>
        ))}
        <button type="button" className="btn tiny" onClick={refresh}>
          Refresh
        </button>
      </div>

      <div className={`suggestion-box ${streaming ? "streaming" : ""}`}>
        {suggestion || <span className="muted">Suggestions appear here when the assistant is on.</span>}
      </div>

      <h3 className="subhead">Transcript</h3>
      <ul className="transcript">
        {turns.length === 0 && <li className="muted">Waiting for speech…</li>}
        {turns.map((t, i) => (
          <li key={`${t.ts}-${i}`} className={t.speaker === "you" ? "you" : "client"}>
            <strong>{t.speaker === "you" ? "You" : "Client"}</strong>
            <span>{t.text}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
