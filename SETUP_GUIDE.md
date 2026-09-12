# Smart Soil System — Poora Setup, Step-by-Step

Ye ek hi file hai jisme shuru se lekar end tak sab kuch hai — hardware wiring se lekar website online karne tak.

---

## PART A: Hardware Wiring

### A1. Soil Moisture Sensor
- VCC → ESP32 **3.3V**
- GND → ESP32 **GND**
- AOUT → ESP32 **GPIO 34**

### A2. Relay Module (aapka board active-HIGH hai)
- VCC → ESP32 **5V (VIN)**
- GND → ESP32 **GND** (yehi GND jo moisture sensor aur ESP32 sab shared hai)
- IN (signal) → ESP32 **GPIO 27**

### A3. Battery + Pump (relay ke through, ESP32 se alag circuit)
- Battery **+** → Relay ka **COM** terminal
- Relay ka **NO** terminal → Pump **+**
- Pump **–** → seedha Battery **–**
- Ye poora battery-pump loop ESP32 ke power se bilkul alag hai — sirf relay ke through control hota hai

### A4. ESP32 khud
- USB cable se laptop/adapter se power lo

---

## PART B: ESP32 Code Upload

1. Arduino IDE kholo, board select karo: **ESP32 Dev Module**
2. `esp32_client/esp32_client.ino` file kholo (zip ke andar)
3. Ye 4 cheezein apne hisaab se edit karo:
   ```cpp
   const char* WIFI_SSID = "aapka_wifi_naam";
   const char* WIFI_PASSWORD = "aapka_wifi_password";
   const char* SERVER_URL = "http://192.168.1.100:5000";  // Part C ke baad ye update hoga
   const char* DEVICE_API_KEY = "koi_bhi_random_secret";   // Part C ke .env se match hona chahiye
   ```
4. Upload karo, Serial Monitor kholo (115200 baud) — "Connected! IP: ..." print hona chahiye

---

## PART C: Website (Backend) Local Setup

### C1. Python environment banao
```bash
cd smart-soil-webapp
python -m venv venv

# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate

pip install -r requirements.txt
```

### C2. `.env` file banao
```bash
copy .env.example .env      # Windows
cp .env.example .env        # Mac/Linux
```

`.env` file kholo aur ye values bharo:

| Variable | Kaha se milega |
|---|---|
| `SECRET_KEY` | Koi bhi random lamba text likh do |
| `WEATHER_CITY` | Apna city ka naam (weather bilkul free hai, koi key/signup nahi chahiye) |
| `DEVICE_API_KEY` | Koi bhi random secret — ESP32 code (Part B step 3) mein bhi wahi likho |
| `GEMINI_API_KEY` | (Optional, AI chat ke liye) Free: [ai.google.dev](https://ai.google.dev) |
| `MOISTURE_THRESHOLD` | Default 2500 rehne do, baad mein apni testing se adjust karna |
| `RAIN_SKIP_PROBABILITY` | Default 50 rehne do |

### C3. Server chalao
```bash
python app.py
```
Browser mein kholo: `http://127.0.0.1:5000`

### C4. Account banao
- **Register** pe click karo, email + password daalo
- **Login** karo

### C5. ESP32 ka IP connect karo
- Apne computer ka local IP nikalo: Windows mein `ipconfig`, Mac/Linux mein `ifconfig`
- Part B ke `esp32_client.ino` mein `SERVER_URL` ko is IP se update karo, jaise:
  ```cpp
  const char* SERVER_URL = "http://192.168.1.105:5000";
  ```
- Dobara ESP32 mein upload karo
- ESP32 aur computer **dono same WiFi** par hone chahiye

---

## PART D: Test karo (sab kaam kar raha hai ya nahi)

1. ✅ ESP32 Serial Monitor mein "POST /api/data -> 200" print ho raha hai?
2. ✅ Website dashboard pe sensor values dikh rahe hain (Moisture card update ho raha hai)?
3. ✅ Moisture change karne par pump khud se ON/OFF ho raha hai?
4. ✅ Dashboard pe "Manual ON" dabane se pump turant ON ho jaata hai?
5. ✅ "Auto" pe wapas jaake automatic logic resume hota hai?
6. ✅ Field area bhar ke "Get Recommendation" dabane se fertilizer + crop suggestion aata hai?
7. ✅ (Agar Gemini key daali hai) Chat box mein sawal poochne par jawaab aata hai?

Jahan bhi "nahi" ho, wahi step dobara check karo ya mujhe batao — screenshot ke saath fix kar denge.

---

## PART E: Kisi bhi jagah se access (online hosting)

Abhi tak sab kuch local WiFi tak limited hai. Poore desh se access ke liye:

1. [render.com](https://render.com) pe free account banao
2. `smart-soil-webapp` folder ko GitHub repository mein daalo
3. Render pe "New Web Service" → apni repo select karo
4. Build command: `pip install -r requirements.txt`
5. Start command: `gunicorn app:app`
6. "Environment" tab mein `.env` ki saari values daalo
7. Deploy hone ke baad ek public URL milega (jaise `https://smart-soil-xxxx.onrender.com`)
8. Us URL ko ESP32 code ke `SERVER_URL` mein daal do, dobara upload karo
9. Ab ESP32 sirf internet-connected WiFi se chalega (local network zaroori nahi), aur aap kahin se bhi login karke dashboard/control access kar sakte ho

---

## Quick reference — sab pins ek jagah

| Component | ESP32 Pin |
|---|---|
| Soil Moisture AOUT | GPIO 34 |
| Relay IN | GPIO 27 |
| Soil Moisture VCC | 3.3V |
| Relay VCC | 5V |
| Sab GND | Common GND |

## Agar kuch atak jaaye
Jis bhi step pe problem aaye, wahi ka screenshot ya error message bhej dena — exact wahi jagah dekh ke fix kar denge.
