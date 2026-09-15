import { useEffect, useRef, useState } from "react";
import type { useLiveSocket } from "../hooks/useLiveSocket";

type Socket = ReturnType<typeof useLiveSocket>;

interface Props {
  socket: Socket;
  active: boolean;
}

export function LivePreview({ socket, active }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [mode, setMode] = useState<"camera" | "processed">("camera");
  const [frameUrl, setFrameUrl] = useState<string | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    return socket.subscribe((msg) => {
      if (msg.channel === "face" && msg.type === "frame" && msg.jpeg) {
        setFrameUrl(`data:image/jpeg;base64,${msg.jpeg}`);
        setMode("processed");
      }
    });
  }, [socket]);

  useEffect(() => {
    if (!active) {
      setMode("camera");
      setFrameUrl(null);
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
      if (videoRef.current) videoRef.current.srcObject = null;
      return;
    }

    let cancelled = false;
    (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { width: 640, height: 480 },
          audio: false,
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          await videoRef.current.play();
        }
      } catch {
        /* permission denied */
      }
    })();

    return () => {
      cancelled = true;
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    };
  }, [active]);

  // Periodically send browser frames for face processing when active
  useEffect(() => {
    if (!active) return;

    const tick = () => {
      const video = videoRef.current;
      const canvas = canvasRef.current;
      if (video && canvas && video.readyState >= 2) {
        canvas.width = video.videoWidth || 640;
        canvas.height = video.videoHeight || 480;
        const ctx = canvas.getContext("2d");
        if (ctx) {
          ctx.drawImage(video, 0, 0);
          const dataUrl = canvas.toDataURL("image/jpeg", 0.7);
          const b64 = dataUrl.split(",")[1];
          if (b64) {
            socket.send({ channel: "face", action: "frame", jpeg: b64 });
          }
        }
      }
      rafRef.current = window.setTimeout(tick, 200);
    };
    tick();
    return () => {
      if (rafRef.current) window.clearTimeout(rafRef.current);
    };
  }, [active, socket]);

  return (
    <section className="panel preview-panel">
      <header className="panel-head">
        <h2>Live preview</h2>
        <span className={`tag ${mode}`}>{mode}</span>
      </header>
      <div className="preview-stage">
        {frameUrl && mode === "processed" ? (
          <img src={frameUrl} alt="Processed feed" className="preview-img" />
        ) : (
          <video ref={videoRef} className="preview-img" muted playsInline />
        )}
        {!active && <div className="preview-empty">Start face capture to preview</div>}
      </div>
      <canvas ref={canvasRef} className="hidden-canvas" />
    </section>
  );
}
