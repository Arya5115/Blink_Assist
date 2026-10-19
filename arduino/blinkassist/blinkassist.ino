const int LIGHT_RELAY = 25;
String command;
void acknowledge(String value) {Serial.println("ACK:" + value); }
void setup() {
  pinMode(LIGHT_RELAY, OUTPUT);
  digitalWrite(LIGHT_RELAY, LOW); 
  Serial.begin(115200);
}
void loop() {
  if (!Serial.available()) return;
  command = Serial.readStringUntil('\n'); command.trim();
  if (command == "LIGHT_ON") { digitalWrite(LIGHT_RELAY, HIGH); acknowledge(command); }
  else if (command == "LIGHT_OFF") { digitalWrite(LIGHT_RELAY, LOW); acknowledge(command); }
  else Serial.println("ERR:UNKNOWN_COMMAND");
}
