import { useEffect, useState } from "react";
import type { useLiveSocket } from "../hooks/useLiveSocket";

type Socket = ReturnType<typeof useLiveSocket>;

interface Props {
  socket: Socket;
  faceActive: boolean;
  voiceActive: boolean;
  sttActive: boolean;
  onFaceActive: (v: boolean) => void;
  onVoiceActive: (v: boolean) => void;
  onSttActive: (v: boolean) => void;
  consent: boolean;
}

export function ControlPanel({
  socket,
  faceActive,
  voiceActive,
  sttActive,
  onFaceActive,
  onVoiceActive,
  onSttActive,
  consent,
}: Props) {
  const [pitch, setPitch] = useState(0);
  const [wet, setWet] = useState(1);
  const [faceModels, setFaceModels] = useState<string[]>(["passthrough"]);
  const [voiceModels, setVoiceModels] = useState<string[]>(["passthrough"]);
  const [faceModel, setFaceModel] = useState("passthrough");
  const [voiceModel, setVoiceModel] = useState("passthrough");
  const [background, setBackground] = useState("native");

  useEffect(() => {
    socket.send({ channel: "face", action: "list_models" });
    socket.send({ channel: "voice", action: "list_models" });
    return socket.subscribe((msg) => {
      if (msg.channel === "face" && msg.type === "ack" && msg.models) {
        setFaceModels(msg.models as string[]);
      }
      if (msg.channel === "voice" && msg.type === "ack" && msg.models) {
        setVoiceModels(msg.models as string[]);
      }
      if (msg.channel === "face" && msg.type === "ack" && msg.action === "start") {
        if (msg.ok === false) onFaceActive(false);
      }
      if (msg.channel === "voice" && msg.type === "ack" && msg.action === "start") {
        if (msg.ok === false) onVoiceActive(false);
      }
      if (msg.channel === "stt" && msg.type === "ack" && msg.action === "start") {
        if (msg.ok === false) onSttActive(false);
      }
    });
  }, [socket, onFaceActive, onVoiceActive, onSttActive]);

  const toggleFace = () => {
    const next = !faceActive;
    onFaceActive(next);
    socket.send({
      channel: "face",
      action: next ? "start" : "stop",
      capture: "browser",
    });
  };

  const toggleVoice = () => {
    const next = !voiceActive;
    onVoiceActive(next);
    socket.send({ channel: "voice", action: next ? "start" : "stop" });
  };

  const toggleStt = () => {
    if (!consent && !sttActive) return;
    const next = !sttActive;
    onSttActive(next);
    socket.send({ channel: "stt", action: next ? "start" : "stop" });
  };

  return (
    <section className="panel control-panel">
      <header className="panel-head">
        <h2>Controls</h2>
      </header>

      <div className="control-block">
        <div className="row between">
          <h3>Face</h3>
          <button type="button" className={faceActive ? "btn on" : "btn"} onClick={toggleFace}>
            {faceActive ? "Stop" : "Start"}
          </button>
        </div>
        <label className="field">
          <span>Model</span>
          <select
            value={faceModel}
            onChange={(e) => {
              setFaceModel(e.target.value);
              socket.send({ channel: "face", action: "set_model", model_id: e.target.value });
            }}
          >
            {faceModels.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Background</span>
          <select
            value={background}
            onChange={(e) => {
              setBackground(e.target.value);
              socket.send({ channel: "face", action: "set_background", mode: e.target.value });
            }}
          >
            <option value="native">Native</option>
            <option value="custom">Custom image</option>
          </select>
        </label>
      </div>

      <div className="control-block">
        <div className="row between">
          <h3>Voice</h3>
          <button type="button" className={voiceActive ? "btn on" : "btn"} onClick={toggleVoice}>
            {voiceActive ? "Stop" : "Start"}
          </button>
        </div>
        <label className="field">
          <span>Model</span>
          <select
            value={voiceModel}
            onChange={(e) => {
              setVoiceModel(e.target.value);
              socket.send({ channel: "voice", action: "set_model", model_id: e.target.value });
            }}
          >
            {voiceModels.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Pitch ({pitch} st)</span>
          <input
            type="range"
            min={-12}
            max={12}
            step={1}
            value={pitch}
            onChange={(e) => {
              const v = Number(e.target.value);
              setPitch(v);
              socket.send({ channel: "voice", action: "set_pitch", semitones: v });
            }}
          />
        </label>
        <label className="field">
          <span>Wet / dry ({Math.round(wet * 100)}%)</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={wet}
            onChange={(e) => {
              const v = Number(e.target.value);
              setWet(v);
              socket.send({ channel: "voice", action: "set_mix", wet: v });
            }}
          />
        </label>
      </div>

      <div className="control-block">
        <div className="row between">
          <h3>Speech-to-text</h3>
          <button
            type="button"
            className={sttActive ? "btn on" : "btn"}
            onClick={toggleStt}
            disabled={!consent && !sttActive}
            title={!consent ? "Grant consent first" : undefined}
          >
            {sttActive ? "Stop" : "Start"}
          </button>
        </div>
        {!consent && <p className="hint">Enable call-capture consent before starting STT.</p>}
      </div>
    </section>
  );
}
