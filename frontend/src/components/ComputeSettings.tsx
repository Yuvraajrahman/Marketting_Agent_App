import { useEffect, useState } from "react";
import type { ServiceCompute } from "../types";
import type { useLiveSocket } from "../hooks/useLiveSocket";

type Socket = ReturnType<typeof useLiveSocket>;

const SERVICES = ["assistant", "voice", "face", "stt"] as const;

interface Props {
  socket: Socket;
}

const defaults: Record<(typeof SERVICES)[number], ServiceCompute> = {
  assistant: { location: "local", host: "127.0.0.1", port: 8000 },
  voice: { location: "local", host: "127.0.0.1", port: 8000 },
  face: { location: "local", host: "127.0.0.1", port: 8000 },
  stt: { location: "local", host: "127.0.0.1", port: 8000 },
};

export function ComputeSettings({ socket }: Props) {
  const [pcIp, setPcIp] = useState("192.168.1.42");
  const [services, setServices] = useState(defaults);

  useEffect(() => {
    return socket.subscribe((msg) => {
      if (msg.channel === "system" && msg.type === "ack" && msg.action === "get_config") {
        setServices((prev) => {
          const next = { ...prev };
          for (const s of SERVICES) {
            const c = msg[s] as ServiceCompute | undefined;
            if (c) next[s] = c;
          }
          const remoteHost = SERVICES.map((s) => next[s]).find((c) => c.location === "remote")?.host;
          if (remoteHost) setPcIp(remoteHost);
          return next;
        });
      }
    });
  }, [socket]);

  const setLocation = (service: (typeof SERVICES)[number], location: "local" | "remote") => {
    const host = location === "remote" ? pcIp : "127.0.0.1";
    setServices((prev) => ({
      ...prev,
      [service]: { ...prev[service], location, host },
    }));
    socket.send({
      channel: "system",
      action: "set_compute",
      service,
      location,
      host,
    });
  };

  return (
    <section className="panel compute-panel">
      <header className="panel-head">
        <h2>Compute</h2>
      </header>
      <label className="field">
        <span>Ubuntu PC LAN IP</span>
        <input
          type="text"
          value={pcIp}
          onChange={(e) => setPcIp(e.target.value)}
          placeholder="192.168.1.42"
        />
      </label>
      <p className="hint">Offload order: assistant → voice → face. Keep STT local.</p>
      <ul className="compute-list">
        {SERVICES.map((service) => {
          const c = services[service];
          return (
            <li key={service} className="compute-row">
              <span className="svc-name">{service}</span>
              <div className="toggle-group">
                <button
                  type="button"
                  className={c.location === "local" ? "btn tiny on" : "btn tiny"}
                  onClick={() => setLocation(service, "local")}
                >
                  Local
                </button>
                <button
                  type="button"
                  className={c.location === "remote" ? "btn tiny on" : "btn tiny"}
                  onClick={() => setLocation(service, "remote")}
                >
                  Remote
                </button>
              </div>
              <span className="svc-host">{c.host}</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
