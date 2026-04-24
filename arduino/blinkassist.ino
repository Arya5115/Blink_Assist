/*
 * BlinkAssist — firmware stub (Objective 5, future hardware integration).
 * Listens on Serial for a single byte:
 *   '1' -> short beep (single/double blink ack)
 *   'S' -> SOS pattern (long/long/long)
 *   '0' -> silence
 */
const int BUZZER = 8;
void setup() { pinMode(BUZZER, OUTPUT); Serial.begin(115200); }
void loop() {
  if (!Serial.available()) return;
  char c = Serial.read();
  if (c == '1') { tone(BUZZER, 880, 120); }
  else if (c == 'S') {
    for (int i = 0; i < 3; i++) { tone(BUZZER, 660, 500); delay(600); }
  } else if (c == '0') { noTone(BUZZER); }
}
