import os
import time
from datetime import datetime

import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, login_required,
    logout_user, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

# ---------------------------------------------------------------------------
# App + config
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = True  # only send cookie over HTTPS (Render is HTTPS)

# Use a real Postgres database when DATABASE_URL is set (e.g. on Render),
# otherwise fall back to a local SQLite file for local development.
# Render's Postgres URL starts with "postgres://" but SQLAlchemy 2.x needs
# "postgresql://" - this rewrites it automatically.
_db_url = os.getenv("DATABASE_URL", "sqlite:///smart_soil.db")
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = _db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

WEATHER_CITY = os.getenv("WEATHER_CITY", "jaipur")


# ---------------------------------------------------------------------------
# Security: never let any page be cached (by the browser or by a network/
# carrier proxy). Without this, some mobile "data saver" proxies have been
# known to serve one user's logged-in page to a different user.
# ---------------------------------------------------------------------------
@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


DEVICE_API_KEY = os.getenv("DEVICE_API_KEY", "change_this_to_your_own_device_secret")
MOISTURE_THRESHOLD = int(os.getenv("MOISTURE_THRESHOLD", "2500"))
RAIN_SKIP_PROBABILITY = int(os.getenv("RAIN_SKIP_PROBABILITY", "50"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")  # free key(s) from https://ai.google.dev — comma-separate multiple keys to auto-rotate when one hits its limit
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")  # free key from https://console.groq.com/keys — used if all Gemini keys are exhausted/overloaded
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")  # free key from https://openrouter.ai/keys — last-resort fallback
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()  # only this email can view /admin
ADMIN_PASSCODE = os.getenv("ADMIN_PASSCODE", "")  # extra secret passcode required to view /admin

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to view the dashboard."


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class SensorReading(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    moisture = db.Column(db.Float)      # raw analog value, 0-4095
    soil_temp = db.Column(db.Float)     # Celsius
    ph = db.Column(db.Float)
    ec = db.Column(db.Float)            # soil salinity
    npk_n = db.Column(db.Float)
    npk_p = db.Column(db.Float)
    npk_k = db.Column(db.Float)
    air_temp = db.Column(db.Float)      # DHT11/22
    humidity = db.Column(db.Float)      # DHT11/22
    rain_detected = db.Column(db.Boolean, default=False)

    pump_decision = db.Column(db.String(20))  # "ON" or "OFF"
    pump_reason = db.Column(db.String(255))


class FarmProfile(db.Model):
    """One row per user — their field size and current crop, used to size recommendations."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    area_value = db.Column(db.Float, default=1.0)
    area_unit = db.Column(db.String(10), default="acre")   # "acre" or "hectare"
    crop_name = db.Column(db.String(100), default="")


def get_farm_profile():
    profile = FarmProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        profile = FarmProfile(user_id=current_user.id, area_value=1.0, area_unit="acre", crop_name="")
        db.session.add(profile)
        db.session.commit()
    return profile


class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    role = db.Column(db.String(10))          # "user" or "ai"
    content = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


class DeviceControl(db.Model):
    """Single row that tracks whether the pump is in AUTO mode or under MANUAL control."""
    id = db.Column(db.Integer, primary_key=True)
    mode = db.Column(db.String(10), default="AUTO")           # "AUTO" or "MANUAL"
    manual_state = db.Column(db.String(10), default="OFF")    # "ON" or "OFF" (used only in MANUAL mode)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)


class Task(db.Model):
    """Per-user farm task checklist item persisted in the database."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    completed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description or "",
            "completed": bool(self.completed),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def get_control():
    """Fetch the single control row, creating it the first time."""
    control = DeviceControl.query.first()
    if not control:
        control = DeviceControl(mode="AUTO", manual_state="OFF")
        db.session.add(control)
        db.session.commit()
    return control


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ---------------------------------------------------------------------------
# Weather helper
# ---------------------------------------------------------------------------
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
_geocode_cache = {}
_weather_cache = {"data": None, "fetched_at": 0}
WEATHER_CACHE_SECONDS = 600  # 10 minutes

WMO_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}
RAIN_CODES = {51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99}


def geocode_city(city_name):
    """City name -> (lat, lon) using Open-Meteo's free geocoding API. Cached in memory."""
    if city_name in _geocode_cache:
        return _geocode_cache[city_name]

    resp = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city_name, "count": 1},
        timeout=6,
    )
    data = resp.json()
    results = data.get("results")
    if not results:
        return None

    coords = (results[0]["latitude"], results[0]["longitude"])
    _geocode_cache[city_name] = coords
    return coords


def _get_weather_openweathermap():
    """Fetch current weather + rain chance from OpenWeatherMap (needs OPENWEATHER_API_KEY)."""
    resp = requests.get(
        "https://api.openweathermap.org/data/2.5/weather",
        params={"q": WEATHER_CITY, "appid": OPENWEATHER_API_KEY, "units": "metric"},
        timeout=6,
    )
    data = resp.json()

    if str(data.get("cod")) != "200":
        return {"available": False, "reason": data.get("message", "weather lookup failed")}

    description = (data.get("weather") or [{}])[0].get("description", "unknown")
    main = data.get("main", {})
    clouds = data.get("clouds", {}).get("all", 0)
    rain_probability = 80 if "rain" in data else min(clouds, 60)

    return {
        "available": True,
        "city": WEATHER_CITY.title(),
        "condition": description,
        "description": description,
        "temp": main.get("temp"),
        "humidity": main.get("humidity"),
        "rain_probability": rain_probability,
    }


def _get_weather_openmeteo():
    """Fetch current weather + rain chance from Open-Meteo (free forever, no key needed)."""
    coords = geocode_city(WEATHER_CITY)
    if not coords:
        return {"available": False, "reason": f"Could not find city '{WEATHER_CITY}'"}
    lat, lon = coords

    resp = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,weather_code",
            "hourly": "precipitation_probability",
            "timezone": "auto",
            "forecast_days": 1,
        },
        timeout=6,
    )
    data = resp.json()

    if "current" not in data:
        return {"available": False, "reason": data.get("reason", "weather lookup failed")}

    current = data["current"]
    code = current.get("weather_code", 0)
    description = WMO_CODES.get(code, "unknown")

    rain_probability = 10
    try:
        current_time = current["time"]
        hourly_times = data["hourly"]["time"]
        hourly_probs = data["hourly"]["precipitation_probability"]
        if current_time in hourly_times:
            idx = hourly_times.index(current_time)
            rain_probability = hourly_probs[idx]
    except (KeyError, ValueError, IndexError):
        rain_probability = 60 if code in RAIN_CODES else 10

    return {
        "available": True,
        "city": WEATHER_CITY.title(),
        "condition": description,
        "description": description,
        "temp": current.get("temperature_2m"),
        "humidity": current.get("relative_humidity_2m"),
        "rain_probability": rain_probability,
    }


def _get_weather_wttr():
    """Fallback: wttr.in — completely free, no signup, no API key, ever."""
    resp = requests.get(
        f"https://wttr.in/{WEATHER_CITY}",
        params={"format": "j1"},
        timeout=6,
        headers={"User-Agent": "curl"},
    )
    data = resp.json()

    current = data["current_condition"][0]
    description = current["weatherDesc"][0]["value"].lower()
    rain_probability = 10
    try:
        today = data["weather"][0]["hourly"]
        probs = [int(h.get("chanceofrain", 0)) for h in today]
        rain_probability = max(probs) if probs else 10
    except (KeyError, IndexError, ValueError):
        pass

    return {
        "available": True,
        "city": WEATHER_CITY.title(),
        "condition": description,
        "description": description,
        "temp": float(current.get("temp_C", 0)),
        "humidity": float(current.get("humidity", 0)),
        "rain_probability": rain_probability,
    }


def get_weather():
    """Return cached weather if fresh, else try OpenWeatherMap (if key set), then
    Open-Meteo, then wttr.in as a last-resort no-key fallback."""
    now = time.time()
    if _weather_cache["data"] and (now - _weather_cache["fetched_at"] < WEATHER_CACHE_SECONDS):
        return _weather_cache["data"]

    result = {"available": False, "reason": "no provider succeeded"}

    providers = []
    if OPENWEATHER_API_KEY:
        providers.append(_get_weather_openweathermap)
    providers.append(_get_weather_openmeteo)
    providers.append(_get_weather_wttr)

    for provider in providers:
        try:
            result = provider()
            if result.get("available"):
                break
        except Exception as exc:
            result = {"available": False, "reason": str(exc)}

    if result.get("available"):
        _weather_cache["data"] = result
        _weather_cache["fetched_at"] = now
    return result


def decide_pump(moisture_value):
    """
    Core logic (matches: check weather first, then moisture, then decide pump).
    Returns (decision, reason) where decision is "ON" or "OFF".
    """
    weather = get_weather()

    if weather.get("available") and weather["rain_probability"] >= RAIN_SKIP_PROBABILITY:
        return "OFF", f"Skipped — rain likely ({weather['description']}, {weather['rain_probability']}% chance)"

    if moisture_value > MOISTURE_THRESHOLD:
        return "ON", f"Soil is dry (value {moisture_value}) and no rain expected"

    return "OFF", f"Soil is wet enough (value {moisture_value})"


def area_to_acres(area_value, area_unit):
    if area_unit == "hectare":
        return area_value * 2.471
    return area_value


def recommend_fertilizer(npk_n, npk_p, npk_k, area_value, area_unit):
    """
    Simplified, rule-of-thumb fertilizer dosage estimate (kg per acre), scaled to field size.
    """
    acres = area_to_acres(area_value, area_unit)
    n, p, k = (npk_n or 0), (npk_p or 0), (npk_k or 0)

    def level(value, low_cut, high_cut):
        if value < low_cut:
            return "low"
        if value < high_cut:
            return "medium"
        return "high"

    n_level = level(n, 40, 80)
    p_level = level(p, 20, 40)
    k_level = level(k, 40, 80)

    dosage_per_acre = {
        "low":    {"Urea (N)": 100, "DAP (P)": 60, "MOP (K)": 40},
        "medium": {"Urea (N)": 50,  "DAP (P)": 30, "MOP (K)": 20},
        "high":   {"Urea (N)": 0,   "DAP (P)": 0,  "MOP (K)": 0},
    }

    return {
        "area_acres": round(acres, 2),
        "n_level": n_level, "p_level": p_level, "k_level": k_level,
        "urea_kg": round(dosage_per_acre[n_level]["Urea (N)"] * acres, 1),
        "dap_kg": round(dosage_per_acre[p_level]["DAP (P)"] * acres, 1),
        "mop_kg": round(dosage_per_acre[k_level]["MOP (K)"] * acres, 1),
    }


def recommend_crops(ph, ec, moisture, air_temp):
    """Simplified crop-suitability heuristic based on soil pH, salinity (EC) and temperature."""
    if ph is None:
        return ["Not enough data yet — take a pH reading first."]

    if ec is not None and ec > 4:
        return ["Barley", "Cotton", "Sugar beet (salt-tolerant crops — your soil salinity is high)"]

    if ph < 5.5:
        return ["Tea", "Potato", "Blueberry (acid-loving crops)"]
    elif ph <= 7.5:
        if air_temp is not None and air_temp >= 25:
            return ["Rice", "Maize", "Sugarcane"]
        return ["Wheat", "Mustard", "Chickpea"]
    else:
        return ["Barley", "Cotton", "Spinach (alkaline-tolerant crops)"]


# ---------------------------------------------------------------------------
# Scoring, Crop Matching & Task Helpers
# ---------------------------------------------------------------------------
def _get_owned_task(task_id):
    """Fetch a Task by ID and verify it belongs to current_user."""
    task = Task.query.filter_by(id=task_id).first()
    if not task:
        return None, (jsonify({"error": "Task not found"}), 404)
    if task.user_id != current_user.id:
        return None, (jsonify({"error": "Forbidden"}), 403)
    return task, None


def _range_score(value, ideal_min, ideal_max, low_span, high_span, max_points=20.0):
    """Score a value as max_points inside [ideal_min, ideal_max], scaling linearly to 0 outside."""
    if value is None:
        return 0.0
    if ideal_min <= value <= ideal_max:
        return float(max_points)
    if value < ideal_min:
        ratio = 1.0 - (ideal_min - value) / low_span
    else:
        ratio = 1.0 - (value - ideal_max) / high_span
    return max(0.0, float(max_points) * ratio)


CROP_PROFILES = [
    {"name": "Tomato", "emoji": "🍅", "ph": (6.0, 7.0), "ec": (1.0, 2.5), "temp": (18.0, 28.0), "moisture": (1800.0, 2800.0)},
    {"name": "Chilli", "emoji": "🌶️", "ph": (6.0, 7.0), "ec": (0.8, 2.0), "temp": (20.0, 32.0), "moisture": (1600.0, 2600.0)},
    {"name": "Peas", "emoji": "🫛", "ph": (6.0, 7.5), "ec": (0.5, 1.5), "temp": (13.0, 22.0), "moisture": (1800.0, 2700.0)},
    {"name": "Beans", "emoji": "🫘", "ph": (6.0, 7.0), "ec": (0.5, 1.2), "temp": (18.0, 27.0), "moisture": (1800.0, 2700.0)},
    {"name": "Brinjal", "emoji": "🍆", "ph": (5.5, 6.8), "ec": (0.8, 2.0), "temp": (20.0, 30.0), "moisture": (1700.0, 2700.0)},
    {"name": "Wheat", "emoji": "🌾", "ph": (6.0, 7.5), "ec": (0.5, 2.5), "temp": (12.0, 24.0), "moisture": (1500.0, 2500.0)},
    {"name": "Rice", "emoji": "🍚", "ph": (5.5, 6.8), "ec": (0.5, 2.0), "temp": (22.0, 34.0), "moisture": (2600.0, 3600.0)},
    {"name": "Maize", "emoji": "🌽", "ph": (5.8, 7.2), "ec": (0.5, 1.8), "temp": (18.0, 30.0), "moisture": (1700.0, 2700.0)},
    {"name": "Potato", "emoji": "🥔", "ph": (5.2, 6.5), "ec": (0.8, 1.8), "temp": (15.0, 24.0), "moisture": (1900.0, 2900.0)},
    {"name": "Sugarcane", "emoji": "🎋", "ph": (6.0, 7.5), "ec": (0.8, 2.0), "temp": (22.0, 35.0), "moisture": (2200.0, 3200.0)},
    {"name": "Mustard", "emoji": "🌼", "ph": (6.0, 7.5), "ec": (0.5, 2.0), "temp": (12.0, 25.0), "moisture": (1300.0, 2300.0)},
    {"name": "Cotton", "emoji": "☁️", "ph": (6.0, 8.0), "ec": (1.0, 3.5), "temp": (21.0, 35.0), "moisture": (1400.0, 2400.0)},
]


def build_context_summary(latest, weather, profile):
    """Plain-text summary of current farm state, given to the AI as context."""
    if not latest:
        sensor_txt = "No sensor readings yet."
    else:
        sensor_txt = (
            f"Moisture={latest.moisture}, Soil Temp={latest.soil_temp}°C, pH={latest.ph}, "
            f"EC={latest.ec}, NPK=({latest.npk_n},{latest.npk_p},{latest.npk_k}), "
            f"Air Temp={latest.air_temp}°C, Humidity={latest.humidity}%, "
            f"Rain detected={latest.rain_detected}, Pump={latest.pump_decision}"
        )
    weather_txt = (
        f"{weather['description']}, {weather['temp']}°C, rain chance {weather['rain_probability']}%"
        if weather.get("available") else "unavailable"
    )
    return (
        f"Sensor data: {sensor_txt}\n"
        f"Weather: {weather_txt}\n"
        f"Field size: {profile.area_value} {profile.area_unit}, current crop: {profile.crop_name or 'not set'}"
    )


def _call_llm_api(url, headers, body, extract_fn, retries=3):
    last_error = "unknown error"
    for attempt in range(retries):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=20)
            data = resp.json()

            if resp.status_code == 200:
                return extract_fn(data), None

            last_error = data.get("error", {}).get("message", f"HTTP {resp.status_code}")

            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(1.2 * (attempt + 1))
                continue
            return None, last_error
        except Exception as exc:
            last_error = str(exc)
            time.sleep(1.2 * (attempt + 1))
    return None, last_error


def _ask_gemini(prompt):
    keys = [k.strip() for k in GEMINI_API_KEY.split(",") if k.strip()]
    if not keys:
        return None, "no Gemini key set"

    body = {"contents": [{"parts": [{"text": prompt}]}]}
    extract = lambda data: data["candidates"][0]["content"]["parts"][0]["text"]
    last_error = "unknown error"

    for key in keys:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.5-flash-lite:generateContent?key={key}"
        )
        answer, error = _call_llm_api(url, {}, body, extract, retries=3)
        if answer:
            return answer, None
        last_error = error
    return None, last_error


def _ask_groq(prompt):
    if not GROQ_API_KEY:
        return None, "no Groq key set"
    body = {
        "model": "openai/gpt-oss-120b",
        "messages": [{"role": "user", "content": prompt}],
    }
    extract = lambda data: data["choices"][0]["message"]["content"]
    return _call_llm_api(
        "https://api.groq.com/openai/v1/chat/completions",
        {"Authorization": f"Bearer {GROQ_API_KEY}"},
        body, extract, retries=3,
    )


def _ask_openrouter(prompt):
    if not OPENROUTER_API_KEY:
        return None, "no OpenRouter key set"
    body = {
        "model": "openrouter/free",
        "messages": [{"role": "user", "content": prompt}],
    }
    extract = lambda data: data["choices"][0]["message"]["content"]
    return _call_llm_api(
        "https://openrouter.ai/api/v1/chat/completions",
        {"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
        body, extract, retries=3,
    )


def ask_ai(question, context_summary):
    if not (GEMINI_API_KEY or GROQ_API_KEY or OPENROUTER_API_KEY):
        return (
            "AI chat abhi off hai kyunki koi bhi AI key set nahi hai (.env mein GEMINI_API_KEY, "
            "GROQ_API_KEY, ya OPENROUTER_API_KEY daal do — sab free hain). Tab tak, upar diya gaya "
            "'Get Recommendation' button rule-based fertilizer aur crop suggestion deta rahega."
        )

    prompt = (
        "You are an agricultural assistant helping an Indian farmer using an IoT soil "
        "monitoring system. Answer briefly and practically, in simple Hindi/Hinglish where natural.\n\n"
        "If a question is not related to farming, still answer it directly and accurately "
        "using your general knowledge, like a normal helpful AI assistant would. However, "
        "never invent or fabricate details about anything you don't actually know, or that "
        "doesn't exist in your sensor data / system context (such as unfamiliar names, terms, "
        "or devices). In such cases, clearly say \"I don't have information about that\" or "
        "ask the user to clarify — never guess confidently.\n\n"
        f"Current farm context:\n{context_summary}\n\n"
        f"Farmer's question: {question}"
    )
    last_error = "unknown error"
    for provider in (_ask_gemini, _ask_groq, _ask_openrouter):
        result, error = provider(prompt)
        if result:
            return result
        if error:
            last_error = error

    return (
        "AI abhi thoda busy hai, thodi der (1-2 minute) baad dobara 'Ask' dabao. "
        "Tab tak upar diya gaya 'Get Recommendation' button turant fertilizer/crop salah de sakta hai."
    )


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Email and password are required.")
            return redirect(url_for("register"))

        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.")
            return redirect(url_for("register"))

        user = User(email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash("Account created — please log in.")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.")
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Dashboard (web UI)
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return redirect(url_for("dashboard")) if current_user.is_authenticated else redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    latest = SensorReading.query.order_by(SensorReading.timestamp.desc()).first()
    history = SensorReading.query.order_by(SensorReading.timestamp.desc()).limit(20).all()
    weather = get_weather()
    control = get_control()
    profile = get_farm_profile()
    chat_history = ChatMessage.query.filter_by(user_id=current_user.id).order_by(ChatMessage.timestamp).limit(20).all()
    tasks = Task.query.filter_by(user_id=current_user.id).order_by(Task.created_at.desc()).limit(20).all()

    pump_decision, pump_reason = (
        (control.manual_state, "Manual override from dashboard")
        if control.mode == "MANUAL"
        else (
            (latest.pump_decision, latest.pump_reason)
            if latest
            else ("OFF", "Waiting for sensor data…")
        )
    )
    fertilizer = (
        recommend_fertilizer(latest.npk_n, latest.npk_p, latest.npk_k, profile.area_value, profile.area_unit)
        if latest
        else {}
    )
    crops = (
        recommend_crops(latest.ph, latest.ec, latest.moisture, latest.air_temp)
        if latest
        else []
    )

    return render_template(
        "dashboard.html",
        latest=latest,
        history=history,
        weather=weather,
        control=control,
        profile=profile,
        chat_history=chat_history,
        tasks=tasks,
        pump_decision=pump_decision,
        pump_reason=pump_reason,
        fertilizer=fertilizer,
        crops=crops,
    )


@app.route("/farm-profile", methods=["POST"])
@login_required
def save_farm_profile():
    profile = get_farm_profile()
    profile.area_value = float(request.form.get("area_value", 1) or 1)
    profile.area_unit = request.form.get("area_unit", "acre")
    profile.crop_name = request.form.get("crop_name", "").strip()
    db.session.commit()
    flash("Farm details saved.")
    return redirect(url_for("dashboard"))


@app.route("/api/recommendation", methods=["GET"])
@login_required
def api_recommendation():
    latest = SensorReading.query.order_by(SensorReading.timestamp.desc()).first()
    profile = get_farm_profile()

    if not latest:
        return jsonify({"error": "No sensor data yet"}), 400

    fert = recommend_fertilizer(latest.npk_n, latest.npk_p, latest.npk_k, profile.area_value, profile.area_unit)
    crops = recommend_crops(latest.ph, latest.ec, latest.moisture, latest.air_temp)

    return jsonify({"fertilizer": fert, "crops": crops})


@app.route("/api/ask-ai", methods=["POST"])
@login_required
def api_ask_ai():
    payload = request.get_json(force=True, silent=True) or {}
    question = (payload.get("question") or "").strip()
    if not question:
        return jsonify({"error": "empty question"}), 400

    latest = SensorReading.query.order_by(SensorReading.timestamp.desc()).first()
    weather = get_weather()
    profile = get_farm_profile()
    context = build_context_summary(latest, weather, profile)

    answer = ask_ai(question, context)

    db.session.add(ChatMessage(user_id=current_user.id, role="user", content=question))
    db.session.add(ChatMessage(user_id=current_user.id, role="ai", content=answer))
    db.session.commit()

    return jsonify({"answer": answer})


@app.route("/control", methods=["POST"])
@login_required
def control_pump():
    """Dashboard buttons call this to switch between AUTO and MANUAL ON/OFF."""
    payload = request.get_json(force=True, silent=True) or {}
    action = payload.get("action")  # "auto" | "manual_on" | "manual_off"

    control = get_control()
    if action == "auto":
        control.mode = "AUTO"
    elif action == "manual_on":
        control.mode = "MANUAL"
        control.manual_state = "ON"
    elif action == "manual_off":
        control.mode = "MANUAL"
        control.manual_state = "OFF"
    else:
        return jsonify({"error": "invalid action"}), 400

    control.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"mode": control.mode, "manual_state": control.manual_state})


# ---------------------------------------------------------------------------
# New Dashboard API Routes (Tasks, Health Score, Crop Match, History)
# ---------------------------------------------------------------------------
@app.route("/api/tasks", methods=["GET"])
@login_required
def get_tasks():
    tasks = (
        Task.query.filter_by(user_id=current_user.id)
        .order_by(Task.created_at.desc())
        .all()
    )
    return jsonify([task.to_dict() for task in tasks])


@app.route("/api/tasks", methods=["POST"])
@login_required
def create_task():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Task title is required"}), 400
    description = (data.get("description") or "").strip()

    task = Task(
        user_id=current_user.id,
        title=title[:200],
        description=description,
    )
    db.session.add(task)
    db.session.commit()
    return jsonify(task.to_dict()), 201


@app.route("/api/tasks/<int:task_id>/toggle", methods=["POST"])
@login_required
def toggle_task(task_id):
    task, error_response = _get_owned_task(task_id)
    if error_response:
        return error_response

    task.completed = not bool(task.completed)
    db.session.commit()
    return jsonify(task.to_dict())


@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
@login_required
def delete_task(task_id):
    task, error_response = _get_owned_task(task_id)
    if error_response:
        return error_response

    db.session.delete(task)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/health-score", methods=["GET"])
@login_required
def get_health_score():
    latest = SensorReading.query.order_by(SensorReading.timestamp.desc()).first()
    if not latest:
        return jsonify({"score": 0, "label": "No Data"})

    moisture_score = _range_score(latest.moisture, 1500.0, 3000.0, 1500.0, 1095.0, 20.0)
    ph_score = _range_score(latest.ph, 6.0, 7.5, 3.0, 3.0, 20.0)
    ec_score = _range_score(latest.ec, 0.5, 2.0, 0.5, 3.0, 20.0)
    temp_score = _range_score(latest.soil_temp, 18.0, 30.0, 18.0, 15.0, 20.0)

    adequate_count = sum([
        latest.npk_n is not None and latest.npk_n > 40,
        latest.npk_p is not None and latest.npk_p > 20,
        latest.npk_k is not None and latest.npk_k > 40,
    ])
    npk_score = 20.0 * (adequate_count / 3.0)

    breakdown = {
        "moisture": round(moisture_score, 1),
        "ph": round(ph_score, 1),
        "npk": round(npk_score, 1),
        "ec": round(ec_score, 1),
        "temperature": round(temp_score, 1),
    }
    total_score = int(round(sum(breakdown.values())))

    if total_score >= 75:
        label = "Good"
    elif total_score >= 50:
        label = "Fair"
    elif total_score >= 25:
        label = "Poor"
    else:
        label = "Critical"

    return jsonify({
        "score": total_score,
        "label": label,
        "breakdown": breakdown,
    })


@app.route("/api/crop-match", methods=["GET"])
@login_required
def get_crop_match():
    latest = SensorReading.query.order_by(SensorReading.timestamp.desc()).first()
    if not latest:
        return jsonify([])

    temp_val = latest.soil_temp if latest.soil_temp is not None else latest.air_temp
    param_configs = [
        ("ph", latest.ph, 3.0, 3.0),
        ("ec", latest.ec, 1.5, 2.5),
        ("temp", temp_val, 15.0, 15.0),
        ("moisture", latest.moisture, 1500.0, 1500.0),
    ]

    matches = []
    for crop in CROP_PROFILES:
        scores = [
            _range_score(val, crop[key][0], crop[key][1], low_span, high_span, 100.0)
            for key, val, low_span, high_span in param_configs
            if val is not None
        ]
        match_pct = int(round(sum(scores) / len(scores))) if scores else 0
        matches.append({
            "name": crop["name"],
            "match_pct": match_pct,
            "emoji": crop["emoji"],
        })

    matches.sort(key=lambda item: item["match_pct"], reverse=True)
    return jsonify(matches[:5])


@app.route("/api/history", methods=["GET"])
@login_required
def get_history():
    try:
        limit = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))

    readings = (
        SensorReading.query.order_by(SensorReading.timestamp.desc())
        .limit(limit)
        .all()
    )
    return jsonify([
        {
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "moisture": r.moisture,
            "soil_temp": r.soil_temp,
            "ph": r.ph,
            "ec": r.ec,
            "npk_n": r.npk_n,
            "npk_p": r.npk_p,
            "npk_k": r.npk_k,
            "air_temp": r.air_temp,
            "humidity": r.humidity,
            "rain_detected": bool(r.rain_detected),
            "pump_decision": r.pump_decision,
        }
        for r in readings
    ])


# ---------------------------------------------------------------------------
# API routes (used by the ESP32 device + dashboard auto-refresh)
# ---------------------------------------------------------------------------
@app.route("/api/data", methods=["POST"])
def api_receive_data():
    """ESP32 posts a JSON reading here. Requires DEVICE_API_KEY header."""
    if request.headers.get("X-Device-Key") != DEVICE_API_KEY:
        return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(force=True, silent=True) or {}
    moisture = payload.get("moisture", 0)

    decision, reason = decide_pump(moisture)

    reading = SensorReading(
        moisture=moisture,
        soil_temp=payload.get("soil_temp"),
        ph=payload.get("ph"),
        ec=payload.get("ec"),
        npk_n=payload.get("npk_n"),
        npk_p=payload.get("npk_p"),
        npk_k=payload.get("npk_k"),
        air_temp=payload.get("air_temp"),
        humidity=payload.get("humidity"),
        rain_detected=payload.get("rain_detected", False),
        pump_decision=decision,
        pump_reason=reason,
    )
    db.session.add(reading)
    db.session.commit()

    return jsonify({"pump": decision, "reason": reason})


@app.route("/api/pump-status", methods=["GET"])
def api_pump_status():
    """ESP32 polls this to find out what to do right now. Manual override always wins."""
    control = get_control()
    if control.mode == "MANUAL":
        return jsonify({"pump": control.manual_state, "reason": "Manual override from dashboard"})

    latest = SensorReading.query.order_by(SensorReading.timestamp.desc()).first()
    if not latest:
        return jsonify({"pump": "OFF", "reason": "No data yet"})
    return jsonify({"pump": latest.pump_decision, "reason": latest.pump_reason})


@app.route("/api/latest", methods=["GET"])
@login_required
def api_latest():
    """Used by the dashboard page to auto-refresh without a full reload."""
    latest = SensorReading.query.order_by(SensorReading.timestamp.desc()).first()
    control = get_control()
    if not latest:
        return jsonify({"mode": control.mode, "manual_state": control.manual_state})
    return jsonify({
        "mode": control.mode,
        "manual_state": control.manual_state,
        "timestamp": latest.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "moisture": latest.moisture,
        "soil_temp": latest.soil_temp,
        "ph": latest.ph,
        "ec": latest.ec,
        "npk_n": latest.npk_n,
        "npk_p": latest.npk_p,
        "npk_k": latest.npk_k,
        "air_temp": latest.air_temp,
        "humidity": latest.humidity,
        "rain_detected": latest.rain_detected,
        "pump_decision": control.manual_state if control.mode == "MANUAL" else latest.pump_decision,
        "pump_reason": "Manual override from dashboard" if control.mode == "MANUAL" else latest.pump_reason,
    })


# ---------------------------------------------------------------------------
# Admin (read-only view of the database).
# Two locks: (1) must be logged in as ADMIN_EMAIL, (2) must enter ADMIN_PASSCODE.
# ---------------------------------------------------------------------------
@app.route("/admin", methods=["GET", "POST"])
@login_required
def admin():
    if not ADMIN_EMAIL or current_user.email.strip().lower() != ADMIN_EMAIL:
        return "Access denied — this page is admin-only.", 403

    if not ADMIN_PASSCODE:
        return "Admin panel is locked: set ADMIN_PASSCODE in environment variables first.", 403

    unlocked = request.form.get("passcode") == ADMIN_PASSCODE
    if not unlocked:
        return render_template("admin_lock.html")

    users = User.query.order_by(User.created_at.desc()).all()
    readings = SensorReading.query.order_by(SensorReading.timestamp.desc()).limit(50).all()
    messages = ChatMessage.query.order_by(ChatMessage.timestamp.desc()).limit(50).all()
    profiles = FarmProfile.query.all()
    control = get_control()

    return render_template(
        "admin.html",
        users=users, readings=readings, messages=messages,
        profiles=profiles, control=control,
    )


# ---------------------------------------------------------------------------
# Create tables at import time too, so this works whether the app is started
# with `python app.py` OR with a WSGI server like `gunicorn app:app`.
# ---------------------------------------------------------------------------
with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
