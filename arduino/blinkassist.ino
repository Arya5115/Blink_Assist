/*
 * BlinkAssist — firmware stub (Objective 5, future hardware integration).
 * Line protocol: BUZZER_ON, BUZZER_OFF, FAN_ON, FAN_OFF, LIGHT_ON,
 * LIGHT_OFF, BELL_RING. Every valid command returns ACK:<COMMAND>.
 */
const int BUZZER = 8;
const int FAN_RELAY = 6;
const int LIGHT_RELAY = 7;
String command;
void acknowledge(String value) { Serial.println("ACK:" + value); }
void setup() {
  pinMode(BUZZER, OUTPUT); pinMode(FAN_RELAY, OUTPUT); pinMode(LIGHT_RELAY, OUTPUT);
  digitalWrite(FAN_RELAY, LOW); digitalWrite(LIGHT_RELAY, LOW); Serial.begin(115200);
}
void loop() {
  if (!Serial.available()) return;
  command = Serial.readStringUntil('\n'); command.trim();
  if (command == "BUZZER_ON") { tone(BUZZER, 880); acknowledge(command); }
  else if (command == "BUZZER_OFF") { noTone(BUZZER); acknowledge(command); }
  else if (command == "FAN_ON") { digitalWrite(FAN_RELAY, HIGH); acknowledge(command); }
  else if (command == "FAN_OFF") { digitalWrite(FAN_RELAY, LOW); acknowledge(command); }
  else if (command == "LIGHT_ON") { digitalWrite(LIGHT_RELAY, HIGH); acknowledge(command); }
  else if (command == "LIGHT_OFF") { digitalWrite(LIGHT_RELAY, LOW); acknowledge(command); }
  else if (command == "BELL_RING") { tone(BUZZER, 1100, 500); acknowledge(command); }
  else Serial.println("ERR:UNKNOWN_COMMAND");
}
