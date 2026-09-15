import { useEffect, useState } from "react";
import { ComputeSettings } from "./components/ComputeSettings";
import { ControlPanel } from "./components/ControlPanel";
import { LivePreview } from "./components/LivePreview";
import { SuggestionsPanel } from "./components/SuggestionsPanel";
import { useLiveSocket } from "./hooks/useLiveSocket";
import type { LoadInfo, StatusMessage } from "./types";
import "./App.css";

export default function App() {
  const socket = useLiveSocket();
  const [consent, setConsent] = useState(false);
  const [faceActive, setFaceActive] = useState(false);
  const [voiceActive, setVoiceActive] = useState(false);
  const [sttActive, setSttActive] = useState(false);
  const [load, setLoad] = useState<LoadInfo | null>(null);
  const [statuses, setStatuses] = useState<StatusMessage[]>([]);
  const [memWarnGb, setMemWarnGb] = useState(13);

  useEffect(() => {
    return socket.subscribe((msg) => {
      if (msg.channel === "system" && msg.type === "hello") {
        if (typeof msg.consent === "boolean") setConsent(msg.consent);
        if (typeof msg.mem_warn_gb === "number") setMemWarnGb(msg.mem_warn_gb);
      }
      if (msg.channel === "system" && msg.type === "load") {
        setLoad({
          cpu: Number(msg.cpu ?? 0),
          mem_gb: Number(msg.mem_gb ?? 0),
          mem_total_gb: Number(msg.mem_total_gb ?? 0),
          mem_percent: Number(msg.mem_percent ?? 0),
          warn: Boolean(msg.warn),
        });
      }
      if (msg.channel === "system" && msg.type === "status") {
        const s = msg as unknown as StatusMessage;
        setStatuses((prev) => [s, ...prev].slice(0, 8));
      }
      if (msg.channel === "system" && msg.type === "ack" && msg.action === "set_consent") {
        if (typeof msg.granted === "boolean") setConsent(msg.granted);
      }
      if (msg.channel === "system" && msg.type === "ack" && msg.action === "get_config") {
        if (typeof msg.consent === "boolean") setConsent(msg.consent);
        if (typeof msg.mem_warn_gb === "number") setMemWarnGb(msg.mem_warn_gb);
      }
    });
  }, [socket]);

  const setConsentValue = (granted: boolean) => {
    setConsent(granted);
    socket.send({ channel: "system", action: "set_consent", granted });
    if (!granted) {
      setSttActive(false);
      socket.send({ channel: "stt", action: "stop" });
    }
  };

  return (
    <div className="app-shell">
      <div className="atmosphere" aria-hidden />
      <header className="topbar">
        <div className="brand-block">
          <p className="brand">Live Presence</p>
          <p className="tagline">Local face, voice, and talking-point assist for live calls</p>
        </div>
        <div className="top-meta">
          <span className={`conn ${socket.connected ? "ok" : "bad"}`}>
            {socket.connected ? "Connected" : "Reconnecting…"}
          </span>
          {load && (
            <div className="load-meter" title={`Warn at ${memWarnGb} GB`}>
              <span>CPU {(load.cpu * 100).toFixed(0)}%</span>
              <span>RAM {load.mem_gb.toFixed(1)} GB</span>
              <div className="meter-bar">
                <div
                  className={`meter-fill ${load.warn ? "warn" : ""}`}
                  style={{ width: `${Math.min(100, load.mem_percent ?? (load.mem_gb / 16) * 100)}%` }}
                />
              </div>
            </div>
          )}
        </div>
      </header>

      {!consent ? (
        <div className="consent-banner">
          <p>
            This app can capture both sides of a call for transcription and coaching suggestions.
            Confirm you have notice/consent where required before enabling STT.
          </p>
          <button type="button" className="btn on" onClick={() => setConsentValue(true)}>
            I understand — enable capture
          </button>
        </div>
      ) : (
        <div className="consent-banner consent-ok">
          <p>Call-capture consent is on. STT may transcribe both sides of the call.</p>
          <button type="button" className="btn" onClick={() => setConsentValue(false)}>
            Revoke
          </button>
        </div>
      )}

      {load?.warn && (
        <div className="offload-banner">
          Memory pressure is high (~{load.mem_gb.toFixed(1)} GB). Switch assistant or voice to your
          Ubuntu PC in Compute settings.
        </div>
      )}

      <main className="grid">
        <LivePreview socket={socket} active={faceActive} />
        <ControlPanel
          socket={socket}
          faceActive={faceActive}
          voiceActive={voiceActive}
          sttActive={sttActive}
          onFaceActive={setFaceActive}
          onVoiceActive={setVoiceActive}
          onSttActive={setSttActive}
          consent={consent}
        />
        <SuggestionsPanel socket={socket} />
        <ComputeSettings socket={socket} />
      </main>

      {statuses.length > 0 && (
        <footer className="status-strip">
          {statuses.map((s, i) => (
            <span key={`${s.text}-${i}`} className={`status ${s.level}`}>
              [{s.service}] {s.text}
            </span>
          ))}
        </footer>
      )}
    </div>
  );
}
