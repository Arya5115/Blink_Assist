"""Validated line-based Arduino gateway; run in the worker process only."""
import os
import threading


class ArduinoGateway:
    ALLOWED = {"BUZZER_ON", "BUZZER_OFF", "FAN_ON", "FAN_OFF", "LIGHT_ON", "LIGHT_OFF", "BELL_RING"}

    def __init__(self):
        self._lock = threading.Lock()
        self.port = os.environ.get("ARDUINO_PORT", "")
        self.baudrate = int(os.environ.get("ARDUINO_BAUDRATE", "115200"))

    def send(self, command):
        if command not in self.ALLOWED:
            raise ValueError("Arduino command is not allowlisted")
        if not self.port:
            raise RuntimeError("ARDUINO_PORT is not configured")
        import serial  # imported lazily so development does not require hardware
        with self._lock, serial.Serial(self.port, self.baudrate, timeout=2) as device:
            device.write(f"{command}\n".encode("ascii"))
            reply = device.readline().decode("ascii", errors="replace").strip()
        if reply != f"ACK:{command}":
            raise RuntimeError(f"Arduino acknowledgement failed: {reply}")
        return reply
