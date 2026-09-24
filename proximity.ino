// Drishti proximity sensor: HC-SR04 ultrasonic -> prints distance in cm over USB serial.
// Wiring: VCC -> 5V, GND -> GND, TRIG -> pin 9, ECHO -> pin 10
const int TRIG = 9;
const int ECHO = 10;

void setup() {
  pinMode(TRIG, OUTPUT);
  pinMode(ECHO, INPUT);
  Serial.begin(9600);
}

void loop() {
  digitalWrite(TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG, LOW);

  long us = pulseIn(ECHO, HIGH, 30000);   // time out after 30 ms (~5 m)
  int cm = us > 0 ? us / 58 : 0;          // 0 means nothing in range
  Serial.println(cm);
  delay(100);
}
