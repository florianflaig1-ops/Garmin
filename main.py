import os
import requests
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI
from google import genai

app = FastAPI(title="Garmin Coach Cloud Pro")

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
Medikation: Morgens 90 mg Duloxetin (sympathische Grundspannung, Ruhepuls erhöht, gesteigerte Schweißrate/Diaphorese), abends 50 mg Trazodon.
Ziele: Kardiovaskuläre Gesundheit, nachhaltiger Fettabbau, maximale Gelenkschonung (Fokus Rad/Zwift, Laufen nur vorsichtig).
Sport: Laufen strikt Zone 2 (Puls 130-140 bpm), Zwift Indoor-Cycling (FTP 168 Watt).
Hydration: Erhöhter Elektrolytbedarf (Natrium) aufgrund von Gewicht und SNRI-induziertem Schwitzen.
"""

CACHE_FILE = "processed_activities.txt"

def get_processed_ids() -> set:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return set(line.strip() for line in f if line.strip())
        except Exception:
            return set()
    return set()

def save_processed_id(act_id: str):
    try:
        with open(CACHE_FILE, "a") as f:
            f.write(f"{act_id}\n")
    except Exception as e:
        print(f"Fehler beim Speichern der ID: {e}")

def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID, 
        "text": text
    }
    try:
        res = requests.post(url, json=payload, timeout=20)
        print(f"Telegram-Status: {res.status_code}")
    except Exception as e:
        print(f"Telegram-Fehler: {e}")

# ==========================================
# ENDPUNKT 1: WORKOUT CHECK (alle 15 Min via Cron)
# ==========================================
@app.get("/check-workout")
def check_workout():
    """Prüft auf beendete Workouts und analysiert neue Einheiten."""
    auth = ("API_KEY", INTERVALS_API_KEY)
    
    now = datetime.now(timezone.utc)
    oldest = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    newest = now.strftime("%Y-%m-%d")
    
    url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/activities?oldest={oldest}&newest={newest}"
    r = requests.get(url, auth=auth, timeout=20)
    
    if r.status_code != 200 or not r.json():
        return {"status": "keine aktivitaeten gefunden"}

    activities = r.json()
    processed_ids = get_processed_ids()
    new_found = 0

    for act in activities:
        act_id = str(act.get("id"))
        
        # Bereits versendet?
        if act_id in processed_ids
