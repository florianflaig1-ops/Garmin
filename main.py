import os
import requests
from datetime import date, timedelta
from fastapi import FastAPI
from google import genai

app = FastAPI(title="Garmin Coach Cloud")

# ==========================================
# KONFIGURATION (aus Render Environment Variables)
# ==========================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ATHLETE_ID = os.getenv("ATHLETE_ID")
INTERVALS_API_KEY = os.getenv("INTERVALS_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PROFIL_KONTEXT = """
Athlet: 41 Jahre alt, männlich, 185 cm, 98 kg.
Medikation: Morgens 90 mg Duloxetin (sympathische Grundspannung / Ruhepuls erhöht), abends 50 mg Trazodon.
Ziele: Kardiovaskuläre Gesundheit, nachhaltiger Gewichtsverlust, Gelenkschonung.
Sport: Laufen in Zone 2 (Puls 130-140 bpm), Zwift-Cycling (FTP 168 Watt).
"""

# Letzte Aktivitäts-ID im Speicher halten
LAST_ACTIVITY_ID = None

def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        requests.post(url, json=payload, timeout=20)
    except Exception as e:
        print(f"Telegram-Fehler: {e}")

@app.get("/check-workout")
def check_workout():
    """Prüft alle 15 Minuten, ob ein neues Workout vorliegt."""
    global LAST_ACTIVITY_ID
    auth = ("API_KEY", INTERVALS_API_KEY)
    heute = date.today().isoformat()
    gestern = (date.today() - timedelta(days=1)).isoformat()
    
    url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/activities?oldest={gestern}&newest={heute}"
    r = requests.get(url, auth=auth, timeout=20)
    
    if r.status_code != 200 or not r.json():
        return {"status": "keine aktivitaeten gefunden"}

    latest = r.json()[-1]
    act_id = str(latest.get("id"))
    
    # Beim allerersten Start die aktuelle ID nur merken, um alte Workouts nicht doppelt zu melden
    if LAST_ACTIVITY_ID is None:
        LAST_ACTIVITY_ID = act_id
        return {"status": f"initialisiert mit aktivitaet {act_id}"}
        
    if act_id == LAST_ACTIVITY_ID:
        return {"status": "kein neues workout"}

    # Neues Workout erkannt -> Analysieren!
    name = latest.get("name", "Workout")
    sport = latest.get("type", "Aktivität")
    dur_min = round(latest.get("moving_time", 0) / 60, 1)
    dist_km = round(latest.get("distance", 0) / 1000, 2)
    avg_hr = latest.get("average_heartrate", "-")
    max_hr = latest.get("max_heartrate", "-")
    watts = latest.get("icu_average_watts", latest.get("average_watts", "-"))
    load = latest.get("icu_training_load", "-")

    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""
Du bist mein persönlicher Ausdauer-Coach. Ich habe gerade ein Workout beendet.
Mein Profil: {PROFIL_KONTEXT}

Trainingsdaten:
- Aktivität: {name} ({sport})
- Dauer: {dur_min} min, Distanz: {dist_km} km
- Ø Herzfrequenz: {avg_hr} bpm (Max: {max_hr} bpm)
- Ø Leistung: {watts} Watt
- Training Load (TL): {load}

Analysiere dieses Workout prägnant:
1. Zonen- und Intensitätsbewertung (Einhaltung Zone 2 / 130-140 bpm bzw. FTP).
2. Physiologischer Reiz & Gelenkbelastung (bei 98 kg).
3. Konkrete Erholungsempfehlung bis morgen.
"""
    res = client.models.generate_content(model="gemini-3.8-flash", contents=prompt)
    send_telegram(f"🏃 Workout-Analyse: {name}\n\n" + res.text)
    
    LAST_ACTIVITY_ID = act_id
    return {"status": f"neues workout {act_id} analysiert"}

@app.get("/morning")
def trigger_morning():
    """Täglicher Morgen-Report um 09:00 Uhr."""
    heute = date.today().isoformat()
    auth = ("API_KEY", INTERVALS_API_KEY)
    url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/wellness?oldest={heute}&newest={heute}"
    r = requests.get(url, auth=auth, timeout=20)
    
    rhr, hrv, sleep_h, score, stress = "Keine Daten", "Keine Daten", "Keine Daten", "Keine Daten", "Keine Daten"
    if r.status_code == 200 and r.json():
        day = r.json()[0]
        rhr = day.get("restingHR", "Keine Daten")
        hrv = day.get("hrv", "Keine Daten")
        if day.get("sleepSecs"):
            sleep_h = round(day["sleepSecs"] / 3600, 1)
        score = day.get("sleepScore", "Keine Daten")
        stress = day.get("stress", "Keine Daten")

    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""
Du bist mein persönlicher Ausdauer-Coach. Hier ist mein Profil:
{PROFIL_KONTEXT}

Meine heutigen Werte vom {heute}:
- Ruhepuls: {rhr} bpm
- HFV: {hrv} ms
- Schlaf: {sleep_h} h (Score: {score})
- Stresslevel: {stress}

Erstelle meine morgendliche Analyse:
1. Regenerationsstatus (unter Berücksichtigung von Duloxetin auf Puls/HFV).
2. Konkrete Trainingsempfehlung für heute (Pausentag ODER Training mit Vorgabe: Sportart, Dauer, Watt- bzw. Pulsziel).
3. Praxistipp für den Tag.
"""
    res = client.models.generate_content(model="gemini-3.8-flash", contents=prompt)
    send_telegram(f"🌅 Guten Morgen! Dein Tagesplan ({heute})\n\n" + res.text)
    return {"status": "morning briefing sent"}

@app.get("/")
def health_check():
    return {"status": "running"}
