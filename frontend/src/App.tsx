import { useEffect, useRef, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

type EvtType = "single" | "double" | "sustained";
type CommandKey = "SINGLE" | "DOUBLE" | "EMERGENCY";

interface EyePoint {
  x: number;
  y: number;
}

interface DetectResp {
  face: boolean;
  ear: number | null;
  ear_left: number | null;
  ear_right: number | null;
  threshold: number | null;
  calibration: number;
  event: { type: EvtType; duration_ms: number } | null;
  counts: { single: number; double: number; sustained: number; total: number };
  blinking: boolean;
  eye_landmarks: {
    left: EyePoint[];
    right: EyePoint[];
  };
}

interface DashboardStats {
  faceDetected: boolean;
  leftEar: number;
  rightEar: number;
  bilateralEar: number;
  threshold: number;
  fps: number;
  totalBlinks: number;
  latencyMs: number;
  calibrating: boolean;
  calibProgress: number;
  eyesClosed: boolean;
  command: CommandKey | null;
  counts: {
    SINGLE: number;
    DOUBLE: number;
    EMERGENCY: number;
  };
  eyeLandmarks: {
    left: EyePoint[];
    right: EyePoint[];
  };
}

interface CommandFlash {
  label: string;
  detail: string;
}

interface LogEntry {
  id: number;
  time: string;
  command: CommandKey;
  message: string;
}

interface HistoryPoint {
  index: number;
  ear: number;
  threshold: number;
}

interface DemoState {
  phase: number;
  nextBlinkIn: number;
  blinkDuration: number;
  calibProgress: number;
  counts: DashboardStats["counts"];
}

const API = import.meta.env.VITE_API_URL || "/api";
const SEND_FPS = 12;
const HISTORY_SIZE = 90;
const MAX_EAR = 0.5;

const COMMAND_META: Record<CommandKey, { title: string; subtitle: string; icon: string; message: string }> = {
  SINGLE: {
    title: "Caregiver Call",
    subtitle: "Single blink (<400 ms)",
    icon: "Bell",
    message: "Caregiver call activated",
  },
  DOUBLE: {
    title: "Appliance Toggle",
    subtitle: "Double blink (<600 ms gap)",
    icon: "Lamp",
    message: "Appliance toggled via relay",
  },
  EMERGENCY: {
    title: "Emergency Alert",
    subtitle: "5 consecutive blinks",
    icon: "Alert",
    message: "Emergency alert triggered",
  },
};

export default function App() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const captureCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const eyeCanvasRef = useRef<HTMLCanvasElement>(null);
  const overlayCanvasRef = useRef<HTMLCanvasElement>(null);
  const timerRef = useRef<number | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const startedRef = useRef(false);
  const mountedAtRef = useRef(Date.now());
  const armedRef = useRef(false);

  const [started, setStarted] = useState(false);
  const [systemArmed, setSystemArmed] = useState(false);
  const [backendConnected, setBackendConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<DashboardStats>(() => getInitialStats());
  const [history, setHistory] = useState<HistoryPoint[]>(() =>
    Array.from({ length: HISTORY_SIZE }, (_, index) => ({
      index,
      ear: 0.3,
      threshold: 0.21,
    })),
  );
  const [activeCommand, setActiveCommand] = useState<CommandKey | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([
    {
      id: 1,
      time: "--:--:--",
      command: "SINGLE",
      message: "Waiting for backend connection",
    },
  ]);
  const [lastBlinkAt, setLastBlinkAt] = useState(Date.now());
  const [permissionState, setPermissionState] = useState<"idle" | "requesting" | "granted" | "denied">("idle");
  const [commandFlash, setCommandFlash] = useState<CommandFlash>({
    label: "System idle",
    detail: "Waiting for camera activation and live blink analysis.",
  });

  useEffect(() => {
    armedRef.current = systemArmed;
  }, [systemArmed]);

  useEffect(() => {
    startedRef.current = started;
  }, [started]);

  useEffect(() => {
    startLoop();
    startCamera();
    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
      stopCamera();
    };
  }, []);

  useEffect(() => {
    drawEyeVisualizer(eyeCanvasRef.current, stats);
    drawCameraOverlay(overlayCanvasRef.current, stats, started, systemArmed);
  }, [stats, started, systemArmed]);

  const startCamera = async () => {
    if (streamRef.current) {
      setStarted(true);
      setSystemArmed(true);
      setPermissionState("granted");
      return;
    }

    try {
      setError(null);
      setPermissionState("requesting");
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: 640, height: 480, facingMode: "user" },
        audio: false,
      });

      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }

      captureCanvasRef.current = document.createElement("canvas");
      captureCanvasRef.current.width = 640;
      captureCanvasRef.current.height = 480;
      setPermissionState("granted");
      setStarted(true);
      setSystemArmed(true);
    } catch (cameraError: unknown) {
      const message = cameraError instanceof Error ? cameraError.message : "Camera permission denied";
      setPermissionState("denied");
      setError(message);
    }
  };

  const stopCamera = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
    setStarted(false);
    setSystemArmed(false);
    setPermissionState("idle");
    setStats(getInitialStats());
    setActiveCommand(null);
    setBackendConnected(false);
    setHistory(
      Array.from({ length: HISTORY_SIZE }, (_, index) => ({
        index,
        ear: 0,
        threshold: 0.21,
      })),
    );
    setCommandFlash({
      label: "Camera stopped",
      detail: "Enable the camera again to restart face detection and blink analysis.",
    });
  };

  const startCalibration = async () => {
    await startCamera();
    await resetSession();
    setSystemArmed(true);
  };

  const activateSystem = async () => {
    await startCamera();
    setSystemArmed(true);
    setCommandFlash({
      label: "Camera active",
      detail: "Waiting for a face to be detected before blink analysis begins.",
    });
  };

  const togglePause = () => {
    const next = !systemArmed;
    setSystemArmed(next);
    setCommandFlash(
      next
        ? {
            label: "Analysis resumed",
            detail: "Blink detection is active again and will respond once your face is detected.",
          }
        : {
            label: "Analysis paused",
            detail: "Camera stays on, but EAR tracking and command execution are paused.",
          },
    );
  };

  const refreshDashboard = async () => {
    await resetSession();
    setCommandFlash({
      label: "Dashboard refreshed",
      detail: "Counters, chart history, and calibration state have been refreshed.",
    });
  };

  const resetSession = async () => {
    try {
      await fetch(`${API}/reset/`, { method: "POST" });
    } catch {
      // Demo mode should still reset locally when the backend is unavailable.
    }

    resetDemoState();
    mountedAtRef.current = Date.now();
    setLastBlinkAt(Date.now());
    setActiveCommand(null);
    setCommandFlash({
      label: "System reset",
      detail: "Calibration and blink counters cleared. Ready to begin a fresh session.",
    });
    setLogs([
      {
        id: Date.now(),
        time: new Date().toLocaleTimeString(),
        command: "SINGLE",
        message: "Session reset complete",
      },
    ]);
    setStats(getInitialStats());
    setHistory(
      Array.from({ length: HISTORY_SIZE }, (_, index) => ({
        index,
        ear: 0.3,
        threshold: 0.21,
      })),
    );
  };

  const startLoop = () => {
    const tick = async () => {
      if (startedRef.current && armedRef.current) {
        const liveStats = await readLiveStats();
        if (liveStats) {
          applyStats(liveStats, true);
        }
      }

      timerRef.current = window.setTimeout(tick, 1000 / SEND_FPS);
    };

    tick();
  };

  const readLiveStats = async (): Promise<DashboardStats | null> => {
    const video = videoRef.current;
    const canvas = captureCanvasRef.current;
    if (!video || !canvas || video.readyState < 2) {
      return null;
    }

    const ctx = canvas.getContext("2d");
    if (!ctx) {
      return null;
    }

    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    const dataUrl = canvas.toDataURL("image/jpeg", 0.68);
    const startedAt = performance.now();

    try {
      const response = await fetch(`${API}/detect/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image: dataUrl }),
      });
      const data: DetectResp = await response.json();
      const latencyMs = Math.round(performance.now() - startedAt);
      setBackendConnected(true);
      return mapLiveStats(data, latencyMs);
    } catch {
      setBackendConnected(false);
      return null;
    }
  };

  const applyStats = (nextStats: DashboardStats, isLive: boolean) => {
    setStats(nextStats);
    setBackendConnected(isLive);
    if (nextStats.faceDetected) {
      setHistory((current) => {
        const nextIndex = current.length > 0 ? current[current.length - 1].index + 1 : 0;
        const nextPoint = {
          index: nextIndex,
          ear: nextStats.bilateralEar,
          threshold: nextStats.threshold,
        };
        return [...current.slice(-HISTORY_SIZE + 1), nextPoint];
      });
    }

    if (nextStats.command) {
      const command = nextStats.command;
      setActiveCommand(nextStats.command);
      setLastBlinkAt(Date.now());
      setCommandFlash({
        label: COMMAND_META[command].title,
        detail: COMMAND_META[command].message,
      });
      window.setTimeout(() => {
        setActiveCommand((current) => (current === command ? null : current));
      }, nextStats.command === "EMERGENCY" ? 2800 : 1400);
      setLogs((current) => {
        const entry: LogEntry = {
          id: Date.now(),
          time: new Date().toLocaleTimeString(),
          command,
          message: COMMAND_META[command].message,
        };
        return [entry, ...current].slice(0, 10);
      });
    } else if (!nextStats.faceDetected && startedRef.current && armedRef.current) {
      setActiveCommand(null);
      setCommandFlash({
        label: "Waiting for face detection",
        detail: "Analytics will begin as soon as a face is clearly visible in the camera frame.",
      });
    }
  };

  const responsiveOk = (Date.now() - lastBlinkAt) / 1000 < 180;
  const uptimeText = formatDuration(Math.floor((Date.now() - mountedAtRef.current) / 1000));
  const statusMode = !started ? "Camera off" : !systemArmed ? "Paused" : backendConnected ? "Live mode" : "Waiting backend";
  const totalBlinks = stats.counts.SINGLE + stats.counts.DOUBLE + stats.counts.EMERGENCY;
  const analysisReady = started && systemArmed && stats.faceDetected;

  return (
    <div className="dashboard-shell">
      <div className="orb orb-left" />
      <div className="orb orb-right" />

      <header className="topbar">
        <div className="logo-block">
          <div className="logo-icon">
            <span />
          </div>
          <div>
            <h1>BlinkAssist Dashboard</h1>
            <p>Real-time blink monitoring and command execution console</p>
          </div>
        </div>

        <div className="header-pills">
          <StatusPill label={statusMode} online={backendConnected} />
          <StatusPill label={stats.faceDetected ? "Face detected" : "No face"} online={stats.faceDetected} />
        </div>
      </header>

      <main className="main-grid">
        <div className="column-stack">
          <section className="panel camera-panel">
            <div className="panel-title">Live Camera</div>
            <div className="camera-stage">
              <video ref={videoRef} className={`camera-video ${started ? "visible" : ""}`} playsInline muted />
              <canvas ref={overlayCanvasRef} className="camera-overlay-canvas" width={640} height={480} />
              <div className={`camera-overlay ${started ? "hidden" : ""}`}>
                <div className="camera-overlay-card">
                  <h3>Enable camera access</h3>
                  <p>
                    {permissionState === "requesting"
                      ? "Waiting for browser permission..."
                      : permissionState === "denied"
                        ? error ?? "Camera access was denied."
                        : "Start the camera to show your face, track blinks, and power the live dashboard."}
                  </p>
                  <button onClick={activateSystem}>
                    {permissionState === "requesting" ? "Requesting permission" : "Start Camera"}
                  </button>
                </div>
              </div>
              {started && (
                <div className="camera-hud">
                  <span className={`hud-pill ${stats.faceDetected ? "good" : "bad"}`}>
                    {stats.faceDetected ? "Face locked" : "Searching face"}
                  </span>
                  <span className={`hud-pill ${stats.eyesClosed ? "bad" : "good"}`}>
                    {stats.eyesClosed ? "Blinking" : "Eyes open"}
                  </span>
                </div>
              )}
            </div>
            <div className="camera-actions">
              <button onClick={activateSystem}>Start Camera</button>
              <button className="secondary" onClick={togglePause}>
                {systemArmed ? "Pause" : "Start Analysis"}
              </button>
              <button className="ghost" onClick={refreshDashboard}>Refresh</button>
              <button className="ghost" onClick={stopCamera}>Stop Camera</button>
            </div>
          </section>

          <section className="panel">
            <div className="panel-title">Eye Aspect Ratio</div>
            <div className={`big-ear ${analysisReady && stats.eyesClosed ? "closed" : ""}`}>
              {analysisReady ? stats.bilateralEar.toFixed(3) : "--"}
            </div>
            <p className="big-ear-label">Bilateral EAR (L+R)/2</p>

            <Gauge label="Left Eye" value={analysisReady ? stats.leftEar : null} threshold={stats.threshold} closed={analysisReady && stats.eyesClosed} />
            <Gauge label="Right Eye" value={analysisReady ? stats.rightEar : null} threshold={stats.threshold} closed={analysisReady && stats.eyesClosed} />

            <div className="threshold-row">
              <span>Threshold t</span>
              <strong>{stats.threshold.toFixed(3)}</strong>
            </div>

            <div className="calibration-wrap">
              <div className="calibration-labels">
                <span>{stats.calibrating ? "Calibrating..." : "Calibration complete"}</span>
                <span>{Math.round(stats.calibProgress * 100)}%</span>
              </div>
              <div className="calibration-track">
                <div className="calibration-fill" style={{ width: `${stats.calibProgress * 100}%` }} />
              </div>
            </div>
          </section>

          <section className="panel">
            <div className="panel-title">Eye Landmark Visualizer</div>
            <canvas ref={eyeCanvasRef} className="eye-canvas" height={150} />
            <p className="panel-note">Live eye geometry and openness rendered from the current detector state.</p>
          </section>
        </div>

        <div className="column-stack">
          <section className="panel">
            <div className="panel-title">System Performance</div>
            <div className="metrics-grid">
              <MetricCard label="FPS" value={stats.fps.toFixed(1)} />
              <MetricCard label="Total Blinks" value={analysisReady ? String(totalBlinks) : "--"} />
              <MetricCard label="Uptime" value={started ? uptimeText : "--"} />
              <MetricCard label="Latency" value={analysisReady ? `<${stats.latencyMs}ms` : "--"} />
            </div>

            <div className="executed-command">
              <div className="executed-label">Executed Command</div>
              <div className={`executed-card ${activeCommand ? activeCommand.toLowerCase() : "idle"}`}>
                <div>
                  <strong>{commandFlash.label}</strong>
                  <p>{commandFlash.detail}</p>
                </div>
                <span className="executed-state">{activeCommand ?? "IDLE"}</span>
              </div>
            </div>

            <div className="control-row">
              <button onClick={startCalibration}>Recalibrate</button>
              <button className="ghost" onClick={togglePause}>{systemArmed ? "Pause Analysis" : "Resume Analysis"}</button>
            </div>

            {error && <p className="error-text">{error}</p>}
          </section>

          <section className="panel">
            <div className="panel-title">EAR History (last few seconds)</div>
            <div className="chart-wrap">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={analysisReady ? history : []}>
                  <defs>
                    <linearGradient id="earGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#00e5ff" stopOpacity={0.34} />
                      <stop offset="95%" stopColor="#00e5ff" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="rgba(0,229,255,0.08)" vertical={false} />
                  <XAxis hide dataKey="index" />
                  <YAxis domain={[0, 0.5]} tick={{ fill: "#4a6080", fontSize: 10 }} width={32} axisLine={false} tickLine={false} />
                  <Tooltip
                    formatter={(value) =>
                      typeof value === "number" ? value.toFixed(3) : String(value ?? "")
                    }
                    contentStyle={{
                      background: "#08111d",
                      border: "1px solid rgba(0,229,255,0.14)",
                      borderRadius: "12px",
                      color: "#cfe8ff",
                    }}
                  />
                  <ReferenceLine y={stats.threshold} stroke="#ffd700" strokeDasharray="4 4" />
                  <Area type="monotone" dataKey="ear" stroke="#00e5ff" strokeWidth={2} fill="url(#earGradient)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </section>

          <section className="panel">
            <div className="panel-title">Commands</div>
            <div className="command-stack">
              <CommandCard
                command="SINGLE"
                count={stats.counts.SINGLE}
                active={activeCommand === "SINGLE"}
              />
              <CommandCard
                command="DOUBLE"
                count={stats.counts.DOUBLE}
                active={activeCommand === "DOUBLE"}
              />
              <CommandCard
                command="EMERGENCY"
                count={stats.counts.EMERGENCY}
                active={activeCommand === "EMERGENCY"}
              />
            </div>
          </section>
        </div>

        <div className="column-stack">
          <section className="panel">
            <div className="panel-title">Safety Monitors</div>
            <div className="safety-stack">
              <SafetyItem
                title="Face Detected"
                subtitle="Camera coverage OK"
                ok={stats.faceDetected}
                okLabel="OK"
                badLabel="ALERT"
              />
              <SafetyItem
                title="Eyes Open"
                subtitle="EAR above threshold"
                ok={!stats.eyesClosed}
                okLabel="OPEN"
                badLabel="CLOSED"
              />
              <SafetyItem
                title="Responsive"
                subtitle="Blink detected recently"
                ok={responsiveOk}
                okLabel="OK"
                badLabel="ALERT"
              />
              <SafetyItem
                title="Calibrated"
                subtitle="Adaptive threshold set"
                ok={!stats.calibrating}
                okLabel="READY"
                badLabel="PENDING"
              />
            </div>
          </section>

          <section className="panel grow">
            <div className="panel-title">Blink Event Log</div>
            <div className="log-stack">
              {logs.map((log) => (
                <div key={log.id} className="log-entry">
                  <span className="log-time">{log.time}</span>
                  <span className={`log-command ${log.command}`}>{log.command}</span>
                  <span className="log-message">{log.message}</span>
                </div>
              ))}
            </div>
          </section>

        </div>
      </main>

      <footer className="footer">
        BlinkAssist interactive console · React frontend · Django + OpenCV + MediaPipe backend
      </footer>
    </div>
  );
}

function StatusPill({ label, online }: { label: string; online: boolean }) {
  return (
    <div className="status-pill">
      <span className={`status-dot ${online ? "online" : "offline"}`} />
      <span>{label}</span>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric-card">
      <div className="metric-value">{value}</div>
      <div className="metric-label">{label}</div>
    </div>
  );
}

function Gauge({
  label,
  value,
  threshold,
  closed,
}: {
  label: string;
  value: number | null;
  threshold: number;
  closed: boolean;
}) {
  const fill = value === null ? "0%" : `${Math.min(100, (value / MAX_EAR) * 100)}%`;
  const thresholdLeft = `${Math.min(100, (threshold / MAX_EAR) * 100)}%`;

  return (
    <div className="gauge-block">
      <div className="gauge-header">
        <span>{label}</span>
        <span className={closed ? "closed" : ""}>{value === null ? "--" : value.toFixed(3)}</span>
      </div>
      <div className="gauge-track">
        <div className="gauge-fill" style={{ width: fill }} />
        <div className="threshold-marker" style={{ left: thresholdLeft }} />
      </div>
    </div>
  );
}

function CommandCard({
  command,
  count,
  active,
}: {
  command: CommandKey;
  count: number;
  active: boolean;
}) {
  const meta = COMMAND_META[command];
  return (
    <div className={`command-card ${command.toLowerCase()} ${active ? "active" : ""}`}>
      <div className="command-icon">{meta.icon}</div>
      <div className="command-copy">
        <h3>{meta.title}</h3>
        <p>{meta.subtitle}</p>
      </div>
      <div className="command-count">{count}x</div>
      <div className="command-badge">{command}</div>
    </div>
  );
}

function SafetyItem({
  title,
  subtitle,
  ok,
  okLabel,
  badLabel,
}: {
  title: string;
  subtitle: string;
  ok: boolean;
  okLabel: string;
  badLabel: string;
}) {
  return (
    <div className={`safety-item ${ok ? "ok" : "alert"}`}>
      <div className="safety-copy">
        <h4>{title}</h4>
        <p>{subtitle}</p>
      </div>
      <div className={`safety-badge ${ok ? "ok" : "bad"}`}>{ok ? okLabel : badLabel}</div>
    </div>
  );
}

function getInitialStats(): DashboardStats {
  return {
    faceDetected: false,
    leftEar: 0,
    rightEar: 0,
    bilateralEar: 0,
    threshold: 0.21,
    fps: 0,
    totalBlinks: 0,
    latencyMs: 0,
    calibrating: false,
    calibProgress: 0,
    eyesClosed: false,
    command: null,
    counts: { SINGLE: 0, DOUBLE: 0, EMERGENCY: 0 },
    eyeLandmarks: { left: [], right: [] },
  };
}

let demoState: DemoState = {
  phase: 0,
  nextBlinkIn: 2.5,
  blinkDuration: 0,
  calibProgress: 0,
  counts: { SINGLE: 0, DOUBLE: 0, EMERGENCY: 0 },
};

function mapLiveStats(data: DetectResp, latencyMs: number): DashboardStats {
  let command: CommandKey | null = null;
  if (data.event?.type === "single") {
    command = "SINGLE";
  } else if (data.event?.type === "double") {
    command = "DOUBLE";
  } else if (data.event?.type === "sustained") {
    command = "EMERGENCY";
  }

  return {
    faceDetected: data.face,
    leftEar: data.ear_left ?? data.ear ?? 0.3,
    rightEar: data.ear_right ?? data.ear ?? 0.3,
    bilateralEar: data.ear ?? 0.3,
    threshold: data.threshold ?? 0.21,
    fps: SEND_FPS,
    totalBlinks: data.counts.total,
    latencyMs,
    calibrating: data.threshold === null,
    calibProgress: data.calibration,
    eyesClosed: data.blinking || ((data.ear ?? 1) < (data.threshold ?? 0.21)),
    command,
    counts: {
      SINGLE: data.counts.single,
      DOUBLE: data.counts.double,
      EMERGENCY: data.counts.sustained,
    },
    eyeLandmarks: data.eye_landmarks,
  };
}

function buildDemoStats(): DashboardStats {
  const demo = demoTick();
  return {
    faceDetected: true,
    leftEar: demo.leftEar,
    rightEar: demo.rightEar,
    bilateralEar: demo.bilateralEar,
    threshold: 0.21,
    fps: 29.6,
    totalBlinks: demo.counts.SINGLE + demo.counts.DOUBLE + demo.counts.EMERGENCY,
    latencyMs: 120,
    calibrating: demo.calibrating,
    calibProgress: demo.calibProgress,
    eyesClosed: demo.eyesClosed,
    command: demo.command,
    counts: demo.counts,
    eyeLandmarks: { left: [], right: [] },
  };
}

function demoTick() {
  demoState.phase += 1 / SEND_FPS;
  if (demoState.calibProgress < 1) {
    demoState.calibProgress = Math.min(1, demoState.calibProgress + 0.012);
  }

  demoState.nextBlinkIn -= 1 / SEND_FPS;
  if (demoState.nextBlinkIn <= 0 && demoState.blinkDuration <= 0) {
    demoState.blinkDuration = Math.random() < 0.18 ? 2.1 : 0.25;
    demoState.nextBlinkIn = 3 + Math.random() * 2.2;
  }

  let baseEar = 0.305 + Math.sin(demoState.phase * 0.9) * 0.014 + Math.sin(demoState.phase * 2.1) * 0.008;
  let command: CommandKey | null = null;
  if (demoState.blinkDuration > 0) {
    baseEar = demoState.blinkDuration > 1 ? 0.11 : 0.15;
    demoState.blinkDuration -= 1 / SEND_FPS;
    if (demoState.blinkDuration <= 0) {
      if (baseEar < 0.12) {
        demoState.counts.EMERGENCY += 1;
        command = "EMERGENCY";
      } else if (Math.random() < 0.35) {
        demoState.counts.DOUBLE += 1;
        command = "DOUBLE";
      } else {
        demoState.counts.SINGLE += 1;
        command = "SINGLE";
      }
    }
  }

  return {
    leftEar: baseEar + (Math.random() - 0.5) * 0.01,
    rightEar: baseEar + (Math.random() - 0.5) * 0.01,
    bilateralEar: baseEar,
    eyesClosed: baseEar < 0.21,
    calibrating: demoState.calibProgress < 1,
    calibProgress: demoState.calibProgress,
    command,
    counts: { ...demoState.counts },
  };
}

function resetDemoState() {
  demoState = {
    phase: 0,
    nextBlinkIn: 2.5,
    blinkDuration: 0,
    calibProgress: 0,
    counts: { SINGLE: 0, DOUBLE: 0, EMERGENCY: 0 },
  };
}

function drawEyeVisualizer(canvas: HTMLCanvasElement | null, stats: DashboardStats) {
  if (!canvas) {
    return;
  }

  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return;
  }

  const width = canvas.clientWidth || 300;
  const height = 150;
  canvas.width = width;
  canvas.height = height;
  ctx.clearRect(0, 0, width, height);

  ctx.fillStyle = "rgba(0,0,0,0.28)";
  ctx.fillRect(0, 0, width, height);

  const openRatio = Math.max(0.08, Math.min(1, (stats.bilateralEar - stats.threshold * 0.45) / 0.22));
  drawStylizedEye(ctx, width * 0.3, height * 0.52, openRatio, stats.eyeLandmarks.left);
  drawStylizedEye(ctx, width * 0.7, height * 0.52, openRatio, stats.eyeLandmarks.right);

  ctx.fillStyle = "rgba(74,96,128,0.92)";
  ctx.font = "10px 'Space Mono', monospace";
  ctx.textAlign = "center";
  ctx.fillText("LEFT", width * 0.3, height - 10);
  ctx.fillText("RIGHT", width * 0.7, height - 10);
}

function drawCameraOverlay(
  canvas: HTMLCanvasElement | null,
  stats: DashboardStats,
  started: boolean,
  systemArmed: boolean
) {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);

  if (!started || !systemArmed || !stats.faceDetected) return;

  ctx.fillStyle = "rgba(0, 255, 153, 0.8)";
  const drawDots = (landmarks: EyePoint[]) => {
    landmarks.forEach((p) => {
      const x = width - p.x;
      const y = p.y;
      ctx.beginPath();
      ctx.arc(x, y, 2.5, 0, Math.PI * 2);
      ctx.fill();
    });
  };

  if (stats.eyeLandmarks.left) drawDots(stats.eyeLandmarks.left);
  if (stats.eyeLandmarks.right) drawDots(stats.eyeLandmarks.right);

  ctx.font = "bold 16px 'Space Mono', monospace";
  ctx.fillStyle = "#ffd700";
  ctx.textAlign = "left";
  ctx.fillText(`EAR: ${stats.bilateralEar.toFixed(3)}`, 20, 30);
  
  ctx.font = "14px 'Space Mono', monospace";
  ctx.fillStyle = "#00e5ff";
  ctx.fillText(`Single: ${stats.counts.SINGLE} | Double: ${stats.counts.DOUBLE} | 5-Blink: ${stats.counts.EMERGENCY}`, 20, 55);

  if (stats.command) {
    ctx.fillStyle = "#ff3060";
    ctx.font = "bold 24px 'Space Mono', monospace";
    ctx.fillText(`CMD: ${stats.command}`, 20, 85);
  }
}

function drawStylizedEye(
  ctx: CanvasRenderingContext2D,
  centerX: number,
  centerY: number,
  openRatio: number,
  landmarks: EyePoint[],
) {
  const radiusX = 58;
  const radiusY = 22 * openRatio;

  ctx.save();
  ctx.translate(centerX, centerY);

  ctx.beginPath();
  ctx.ellipse(0, 0, radiusX, Math.max(2, radiusY), 0, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(255,255,255,0.06)";
  ctx.fill();

  if (openRatio > 0.18) {
    const iris = ctx.createRadialGradient(0, 0, 0, 0, 0, 20 * openRatio);
    iris.addColorStop(0, "rgba(0,229,255,0.95)");
    iris.addColorStop(0.5, "rgba(0,143,204,0.45)");
    iris.addColorStop(1, "rgba(0,34,55,0.1)");
    ctx.beginPath();
    ctx.arc(0, 0, 16 * openRatio, 0, Math.PI * 2);
    ctx.fillStyle = iris;
    ctx.fill();

    ctx.beginPath();
    ctx.arc(0, 0, 6 * openRatio, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(3,6,10,0.95)";
    ctx.fill();
  }

  ctx.beginPath();
  ctx.moveTo(-radiusX, 0);
  ctx.bezierCurveTo(-radiusX / 2, -24 * openRatio, radiusX / 2, -24 * openRatio, radiusX, 0);
  ctx.strokeStyle = "rgba(0,229,255,0.88)";
  ctx.lineWidth = 1.5;
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo(-radiusX, 0);
  ctx.bezierCurveTo(-radiusX / 2, 24 * openRatio, radiusX / 2, 24 * openRatio, radiusX, 0);
  ctx.stroke();

  if (landmarks.length > 0) {
    const xs = landmarks.map((point) => point.x);
    const ys = landmarks.map((point) => point.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const scaleX = maxX - minX === 0 ? 1 : (radiusX * 1.7) / (maxX - minX);
    const scaleY = maxY - minY === 0 ? 1 : (20 * openRatio + 10) / (maxY - minY);

    ctx.fillStyle = "rgba(0,255,153,0.75)";
    landmarks.forEach((point) => {
      const px = (point.x - (minX + maxX) / 2) * scaleX;
      const py = (point.y - (minY + maxY) / 2) * scaleY;
      ctx.beginPath();
      ctx.arc(px, py, 2.2, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  ctx.restore();
}

function formatDuration(seconds: number) {
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins}m${secs}s`;
}
