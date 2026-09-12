#include <WiFi.h>
#include <HTTPClient.h>

// ---------- WiFi ----------
const char* WIFI_SSID = "YOUR_WIFI_NAME";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// ---------- Backend server ----------
// Replace with your PC/server's local IP (run `ipconfig` on Windows to find it)
// while both ESP32 and the Flask server are on the same WiFi network.
// Later, once hosted online, replace this with your real domain.
const char* SERVER_URL = "http://192.168.1.100:5000";
const char* DEVICE_API_KEY = "change_this_to_your_own_device_secret"; // must match .env

// ---------- Pins ----------
const int SOIL_PIN = 34;
const int RELAY_PIN = 27;

void setup() {
  Serial.begin(115200);
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, LOW); // active-HIGH relay -> LOW = pump OFF at boot

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nConnected! IP: " + WiFi.localIP().toString());
}

void loop() {
  int soilValue = analogRead(SOIL_PIN);
  Serial.print("Soil Value: ");
  Serial.println(soilValue);

  // Step 1: send this reading to the server (server checks weather + moisture)
  sendReading(soilValue);

  // Step 2: ask the server what to do (it already factored in the weather)
  String decision = fetchPumpDecision();

  if (decision == "ON") {
    Serial.println("Server says: PUMP ON");
    digitalWrite(RELAY_PIN, HIGH);
  } else {
    Serial.println("Server says: PUMP OFF");
    digitalWrite(RELAY_PIN, LOW);
  }

  delay(10000); // check every 10 seconds — adjust as needed
}

void sendReading(int soilValue) {
  if (WiFi.status() != WL_CONNECTED) return;

  HTTPClient http;
  http.begin(String(SERVER_URL) + "/api/data");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Key", DEVICE_API_KEY);

  // Add your other sensors (DHT11, pH, NPK, EC, rain) here as you wire them up.
  String body = "{\"moisture\":" + String(soilValue) + "}";

  int code = http.POST(body);
  Serial.print("POST /api/data -> ");
  Serial.println(code);
  http.end();
}

String fetchPumpDecision() {
  if (WiFi.status() != WL_CONNECTED) return "OFF";

  HTTPClient http;
  http.begin(String(SERVER_URL) + "/api/pump-status");
  int code = http.GET();

  String decision = "OFF";
  if (code == 200) {
    String payload = http.getString();
    if (payload.indexOf("\"pump\":\"ON\"") >= 0) {
      decision = "ON";
    }
  }
  http.end();
  return decision;
}
