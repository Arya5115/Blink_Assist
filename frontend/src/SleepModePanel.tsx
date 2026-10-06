import { useEffect, useRef, useState } from "react";

type Telemetry = { face: boolean; blinking: boolean; calibration: number; threshold: number | null } | null;
type MonitorState = { sleep_mode_on: boolean; camera_loss_alerted: boolean };
type Props = {
  telemetry: Telemetry;
  loadState: () => Promise<Response>;
  post: (path: string, data: object) => Promise<unknown>;
  add: (kind: string, text: string, status?: string) => void;
  announce: (text: string) => void;
};

const CLOSED_EYE_THRESHOLD_MS = 40 * 1000;
const CAMERA_LOSS_GRACE_MS = 30 * 1000;
const TELEMETRY_STALE_MS = 2500;

export default function SleepModePanel({ telemetry, loadState, post, add, announce }: Props) {
  const [sleepModeOn, setSleepModeOn] = useState(false);
  const [cameraLossAlerted, setCameraLossAlerted] = useState(false);
  const [closedSeconds, setClosedSeconds] = useState(0);
  const [message, setMessage] = useState("");
  const telemetryRef = useRef<Telemetry>(telemetry);
  const lastTelemetryAt = useRef(telemetry ? Date.now() : 0);
  const sleepModeOnRef = useRef(false);
  const cameraLossAlertedRef = useRef(false);
  const closedSince = useRef<number | null>(null);
  const unavailableSince = useRef<number | null>(null);
  const busy = useRef(false);
  const lastAttempt = useRef<{ action: string; at: number }>({ action: "", at: 0 });
  const postRef = useRef(post);
  const addRef = useRef(add);
  const announceRef = useRef(announce);
  const loadStateRef = useRef(loadState);

  telemetryRef.current = telemetry;
  if (telemetry) lastTelemetryAt.current = Date.now();
  postRef.current = post;
  addRef.current = add;
  announceRef.current = announce;
  loadStateRef.current = loadState;

  const applyState = (state: MonitorState) => {
    sleepModeOnRef.current = state.sleep_mode_on;
    cameraLossAlertedRef.current = state.camera_loss_alerted;
    setSleepModeOn(state.sleep_mode_on);
    setCameraLossAlerted(state.camera_loss_alerted);
  };

  useEffect(() => {
    let mounted = true;
    void loadStateRef.current().then(async response => {
      if (!response.ok) throw new Error("Unable to load automatic safety status.");
      const state = await response.json() as MonitorState;
      if (mounted) applyState(state);
    }).catch(() => { if (mounted) setMessage("Automatic safety status could not be loaded."); });
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (busy.current) return;
      const now = Date.now();
      const latest = telemetryRef.current;
      const telemetryFresh = latest !== null && now - lastTelemetryAt.current <= TELEMETRY_STALE_MS;
      const faceDetected = telemetryFresh && latest?.face === true;
      const detectorReady = faceDetected && latest?.threshold !== null && latest?.calibration >= 1;

      if (!faceDetected) {
        if (unavailableSince.current === null) {
          unavailableSince.current = latest ? lastTelemetryAt.current : now;
        }
        closedSince.current = null;
        setClosedSeconds(0);
        if (now - unavailableSince.current >= CAMERA_LOSS_GRACE_MS && !cameraLossAlertedRef.current) {
          if (lastAttempt.current.action === "camera_lost" && now - lastAttempt.current.at < 5000) return;
          lastAttempt.current = { action: "camera_lost", at: now };
          busy.current = true;
          void postRef.current("/safety/sleep_mode/", { action: "camera_lost" }).then(saved => {
            const state = saved as MonitorState;
            applyState(state);
            if (state.camera_loss_alerted) {
              addRef.current("Camera", "Face/camera unavailable for 30 seconds; caregiver alerted", "PENDING");
              setMessage("Face/camera unavailable. Caregiver has been alerted.");
              announceRef.current("The camera cannot detect your face. Your caregiver has been alerted.");
            }
          }).catch(error => setMessage(error instanceof Error ? error.message : "Camera-loss alert could not be recorded.")).finally(() => { busy.current = false; });
        }
        return;
      }

      unavailableSince.current = null;
      if (cameraLossAlertedRef.current) {
        if (lastAttempt.current.action === "camera_recovered" && now - lastAttempt.current.at < 5000) return;
        lastAttempt.current = { action: "camera_recovered", at: now };
        busy.current = true;
        void postRef.current("/safety/sleep_mode/", { action: "camera_recovered" }).then(saved => {
          const state = saved as MonitorState;
          applyState(state);
          if (!state.camera_loss_alerted) {
            addRef.current("Camera", "Face/camera detection recovered");
            setMessage("Face/camera detection recovered.");
          }
        }).catch(error => setMessage(error instanceof Error ? error.message : "Camera recovery could not be recorded.")).finally(() => { busy.current = false; });
        return;
      }

      if (!detectorReady) {
        closedSince.current = null;
        setClosedSeconds(0);
        return;
      }

      if (sleepModeOnRef.current) {
        if (!latest?.blinking) {
          if (lastAttempt.current.action === "wake" && now - lastAttempt.current.at < 5000) return;
          lastAttempt.current = { action: "wake", at: now };
          busy.current = true;
          void postRef.current("/safety/sleep_mode/", { action: "wake" }).then(saved => {
            const state = saved as MonitorState;
            applyState(state);
            if (!state.sleep_mode_on) {
              closedSince.current = null;
              setClosedSeconds(0);
              addRef.current("Sleep Mode", "Eyes reopened; Sleep Mode turned off");
              setMessage("Eyes reopened. Sleep Mode is now off.");
              announceRef.current("Good Mornign Sunshine.");
            }
          }).catch(error => setMessage(error instanceof Error ? error.message : "Wake alert could not be recorded.")).finally(() => { busy.current = false; });
        }
        return;
      }

      if (!latest?.blinking) {
        closedSince.current = null;
        setClosedSeconds(0);
        return;
      }

      if (closedSince.current === null) closedSince.current = now;
      const elapsed = now - closedSince.current;
      setClosedSeconds(Math.floor(elapsed / 1000));
      if (elapsed > CLOSED_EYE_THRESHOLD_MS) {
        if (lastAttempt.current.action === "sleep" && now - lastAttempt.current.at < 5000) return;
        lastAttempt.current = { action: "sleep", at: now };
        busy.current = true;
        void postRef.current("/safety/sleep_mode/", { action: "sleep", closed_duration_seconds: elapsed / 1000 }).then(saved => {
          const state = saved as MonitorState;
          applyState(state);
          if (state.sleep_mode_on) {
            addRef.current("Sleep Mode", "Eyes closed continuously for more than 40 seconds; caregiver silently alerted", "PENDING");
            setMessage("Sleep Mode is on. Caregiver alerted silently.");
          }
        }).catch(error => setMessage(error instanceof Error ? error.message : "Sleep alert could not be recorded.")).finally(() => { busy.current = false; });
      }
    }, 250);
    return () => window.clearInterval(timer);
  }, []);

  return <section className="sleep-mode-panel" aria-label="Automatic sleep and camera monitoring">
    <div className="sleep-mode-copy">
      <p className="eyebrow">AUTOMATIC SAFETY MONITORING</p>
      <h2>Sleep Mode <span className={sleepModeOn ? "sleep-mode-on" : "sleep-mode-off"}>{sleepModeOn ? "ON" : "MONITORING"}</span></h2>
      <p>{sleepModeOn ? "Eyes reopened will turn Sleep Mode off and notify your caregiver." : telemetry?.threshold === null ? "Calibrating eye detection. Keep your eyes open and face in view." : telemetry?.face ? `Eyes closed: ${Math.floor(closedSeconds / 60)}m ${closedSeconds % 60}s / 40s` : "Waiting for face/camera data."}</p>
      {cameraLossAlerted && <p className="sleep-mode-warning" role="status">Face/camera unavailable · caregiver alerted · waiting for recovery</p>}
      {message && <p className="sleep-mode-message" role="status">{message}</p>}
    </div>
    <span className="sleep-mode-indicator" aria-live="polite">{sleepModeOn ? "SLEEP MODE ON" : cameraLossAlerted ? "CHECK CAMERA" : "MONITORING"}</span>
  </section>;
}
