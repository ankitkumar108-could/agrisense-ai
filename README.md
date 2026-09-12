# Smart Soil — Web Dashboard

Real backend hai (Python Flask) jisme:
- Email/password se login/register (real, database-backed)
- Sensor data ka dashboard (moisture, soil temp, pH, NPK, DHT air temp/humidity, rain, EC)
- Weather API check (OpenWeatherMap free tier) — pehle weather dekhta hai, phir moisture, phir pump decide karta hai
- ESP32 se live data lene ke liye API endpoints

## 1. Setup (pehli baar)

```bash
cd smart-soil-webapp
python -m venv venv

# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate

pip install -r requirements.txt
```

## 2. `.env` file banao

`.env.example` ko copy karke `.env` naam do, aur values bharo:

```bash
copy .env.example .env      # Windows
cp .env.example .env        # Mac/Linux
```

- `WEATHER_CITY` — apna city name (weather bilkul free hai, koi key ya signup nahi chahiye)
- `DEVICE_API_KEY` — koi bhi random secret text likh do, yehi ESP32 code mein bhi daalna hai
- `SECRET_KEY` — koi bhi random long text

## 3. Server chalao

```bash
python app.py
```

Browser mein kholo: `http://127.0.0.1:5000` — pehli baar register karo, phir login karo.

## 4. ESP32 ko connect karo

`esp32_client/esp32_client.ino` file open karo Arduino IDE mein:
- `WIFI_SSID`, `WIFI_PASSWORD` apna daalo
- `SERVER_URL` mein apne computer ka local IP daalo (jis par Flask chal raha hai) — `ipconfig` (Windows) se pata karo, jaise `http://192.168.1.100:5000`
- `DEVICE_API_KEY` wahi daalo jo `.env` mein likha hai

ESP32 aur computer **dono same WiFi network** par hone chahiye.

## Kaise kaam karta hai (aapki requirement ke hisaab se)

1. ESP32 moisture sensor padhta hai
2. `/api/data` par bhejta hai
3. Server pehle **weather check** karta hai (agar baarish ho sakti hai to pump skip)
4. Phir **moisture threshold** check karta hai
5. Decision (`ON`/`OFF`) `/api/pump-status` par available hota hai
6. ESP32 usi decision ke hisaab se relay chalata/band karta hai
7. Dashboard pe sab kuch live dikhta hai, har 10 second mein auto-refresh hota hai

## AI Farm Advisor (naya feature)

Dashboard pe ab ek naya section hai:

1. **Field details form** — apna khet ka area (acre/hectare) aur current crop daal do, "Save" karo
2. **Get Recommendation button** — ye turant (bina kisi AI/paid API ke) batayega:
   - Kitna fertilizer (Urea, DAP, MOP) chahiye — aapke field size ke hisaab se kg mein
   - Kaunse crops abhi ki soil condition (pH, EC, temp) ke liye suitable hain
   - *(Ye ek simplified rule-based estimate hai, sirf guidance ke liye — asli dosage ke liye krishi kendra/soil-testing lab se confirm karo)*
3. **AI chat box** — kuch bhi free-text mein pooch sakte ho ("is mahine kya lagau", "pump kab chalega", etc.) — ye aapke live sensor data + weather ko context mein leke jawaab deta hai

### AI chat free mein kaise enable karo
`.env` mein `GEMINI_API_KEY` daalo:
1. [ai.google.dev](https://ai.google.dev) pe jaake free mein sign in karo (Google account se)
2. "Get API key" par click karke free key generate karo (koi credit card nahi chahiye)
3. Wo key `.env` mein `GEMINI_API_KEY=` ke aage paste kar do

Agar key nahi daali, to "Get Recommendation" button phir bhi kaam karega — sirf free-text chat box AI ke bina limited rahega.

## Manual control (kisi bhi jagah se motor ON/OFF)

Dashboard pe ab 3 buttons hain:
- **Auto** — normal logic chalega (weather + moisture ke hisaab se)
- **Manual ON** — pump ko force ON kar dega, jab tak aap "Auto" na dabao
- **Manual OFF** — pump ko force OFF kar dega

Ye setting server ke database mein save hoti hai, isliye jaise hi website online host ho jaayegi, aap **kahin se bhi** (koi bhi city/state) login karke button dabakar pump control kar sakte ho — ESP32 har 10 second mein server se puchta hai aur turant follow karta hai.

## Website ko internet pe host karna (kisi bhi jagah se access ke liye)

Abhi ye sirf aapke computer/WiFi tak hi kaam karta hai. Poore desh se access ke liye ise ek **free hosting service** pe daalna hoga. Sabse aasan free option:

### Render.com (free tier) se deploy karna
1. [render.com](https://render.com) pe free account banao (GitHub se sign in kar sakte ho)
2. Apna `smart-soil-webapp` folder ek GitHub repository mein upload karo
3. Render pe "New Web Service" → apni repo select karo
4. Build command: `pip install -r requirements.txt`
5. Start command: `gunicorn app:app` *(niche `requirements.txt` mein `gunicorn` add karna hoga)*
6. "Environment" tab mein apni `.env` wali saari values daalo (SECRET_KEY, DEVICE_API_KEY, WEATHER_CITY, MOISTURE_THRESHOLD, RAIN_SKIP_PROBABILITY)
7. Deploy hone ke baad Render ek public URL dega jaisa `https://smart-soil-xxxx.onrender.com`

### Uske baad ESP32 code mein sirf 1 line badalni hai
`esp32_client.ino` mein:
```cpp
const char* SERVER_URL = "https://smart-soil-xxxx.onrender.com";
```
(local IP ki jagah ye public URL daal do — ab ESP32 sirf internet se connect hona chahiye, wifi router mein internet hona zaroori hai)

**Note:** Render ka free tier SQLite database ko har naye deploy pe reset kar sakta hai (disk persistent nahi hoti free plan mein). Abhi demo/project ke liye theek hai; production ke liye baad mein Render ka free PostgreSQL add-on use kar sakte ho — bata dena, main uske hisaab se code adjust kar dunga.

## Baad mein add karna

- DHT11, pH, NPK, raindrop sensor ka actual data ESP32 code mein `sendReading()` function ke JSON body mein add kar dena (`app.py` already un fields ko handle karta hai)
- Free hosting ke liye baad mein Render.com / PythonAnywhere jaisi free-tier service use kar sakte ho
