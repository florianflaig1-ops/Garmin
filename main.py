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
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=20)
    except Exception as e:
        print(f"Telegram-Fehler: {e}")

# ==========================================
# ENDPUNKT 1: WORKOUT CHECK (alle 15 Min)
# ==========================================
@app.get("/check-workout")
def check_workout():
    """Prüft, ob in den letzten 35 Minuten ein Workout beendet wurde."""
    auth = ("API_KEY", INTERVALS_API_KEY)
    
    # Zeitfenster: Gestern bis heute abdecken
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
        if act_id in processed_ids:
            continue
            
        # Nur Aktivitäten der letzten 45 Minuten betrachten (verhindert Altlasten-Spam)
        end_date_str = act.get("end_date_local") or act.get("start_date_local")
        if end_date_str:
            try:
                # ISO-Format parsen
                act_time = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                # Wenn naiv, auf UTC setzen
                if act_time.tzinfo is None:
                    act_time = act_time.replace(tzinfo=timezone.utc)
                if (now - act_time).total_seconds() > 3600:
                    # Älter als 1 Stunde -> nur als bekannt markieren, nicht senden
                    save_processed_id(act_id)
                    continue
            except Exception:
                pass

        # Detail-Metriken extrahieren
        name = act.get("name", "Workout")
        sport = act.get("type", "Aktivität")
        dur_min = round(act.get("moving_time", 0) / 60, 1)
        dist_km = round(act.get("distance", 0) / 1000, 2)
        avg_hr = act.get("average_heartrate", "-")
        max_hr = act.get("max_heartrate", "-")
        avg_watts = act.get("icu_average_watts", act.get("average_watts", "-"))
        np_watts = act.get("icu_weighted_avg_watts", "-")
        cadence = act.get("average_cadence", "-")
        load = act.get("icu_training_load", "-")
        decoupling = act.get("icu_efficiency_factor", "-")
        
        # HR-Zonen (Sekunden in Z1, Z2, Z3+)
        hr_zones = act.get("icu_hr_zones", [])
        z1_m = round(hr_zones[0] / 60, 1) if len(hr_zones) > 0 else "-"
        z2_m = round(hr_zones[1] / 60, 1) if len(hr_zones) > 1 else "-"
        z3_plus_m = round(sum(hr_zones[2:]) / 60, 1) if len(hr_zones) > 2 else "-"

        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = f"""
Du bist mein persönlicher Ausdauer-Coach. Ich habe gerade ein Workout beendet.
Mein Profil:
{PROFIL_KONTEXT}

Trainingsdaten:
- Aktivität: {name} ({sport})
- Dauer: {dur_min} min, Distanz: {dist_km} km
- Ø Leistung: {avg_watts} W (Normalized Power NP: {np_watts} W)
- Ø Trittfrequenz: {cadence} rpm
- Ø Puls: {avg_hr} bpm (Max: {max_hr} bpm)
- Pulszonen: Zone 1: {z1_m} min | Zone 2: {z2_m} min | Über Zone 2: {z3_plus_m} min
- Training Load (TL): {load}
- Effizienzfaktor / Entkopplung: {decoupling}

Erstelle eine prägnante, fundierte sportwissenschaftliche Analyse:
1. **Intensität & Zonenbewertung:** Einhaltung Zone 2 bzw. FTP-Bezug (168 W). Wurde die aerobe Basis sauber getroffen?
2. **Metabolischer & kardialer Reiz:** Bewertung von Entkopplung und Puls (unter Beachtung von Duloxetin).
3. **Gelenke & Orthopädie:** Belastung bei 98 kg Körpergewicht.
4. **Hydrations- & Erholungsanweisung:** Konkrete ml-Trinkmenge + Elektrolyte (Salz) und Erholungszeitraum.
"""
        res = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        send_telegram(f"🚴 *Workout-Analyse: {name}*\n\n" + res.text)
        
        save_processed_id(act_id)
        new_found += 1

    return {"status": f"pruefung beendet, {new_found} neue analysiert"}

# ==========================================
# ENDPUNKT 2: MORGEN-REPORT (Täglich 09:00 Uhr)
# ==========================================
@app.get("/morning")
def trigger_morning():
    """Täglicher Morgen-Report mit 7-Tage-Trend und Schlafarchitektur."""
    auth = ("API_KEY", INTERVALS_API_KEY)
    
    # 7 Tage Historie abrufen
    heute_dt = datetime.now(timezone.utc)
    heute = heute_dt.strftime("%Y-%m-%d")
    vor_7_tagen = (heute_dt - timedelta(days=7)).strftime("%Y-%m-%d")
    
    url = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}/wellness?oldest={vor_7_tagen}&newest={heute}"
    r = requests.get(url, auth=auth, timeout=20)
    
    wellness_data = r.json() if (r.status_code == 200 and isinstance(r.json(), list)) else []
    
    today_wellness = wellness_data[-1] if wellness_data else {}
    
    # Heutige Werte
    rhr = today_wellness.get("restingHR", "k.A.")
    hrv = today_wellness.get("hrv", "k.A.")
    sleep_score = today_wellness.get("sleepScore", "k.A.")
    stress = today_wellness.get("stress", "k.A.")
    
    total_sleep_h = round(today_wellness.get("sleepSecs", 0) / 3600, 1) if today_wellness.get("sleepSecs") else "k.A."
    deep_sleep_h = round(today_wellness.get("deepSleepSecs", 0) / 3600, 1) if today_wellness.get("deepSleepSecs") else "k.A."
    rem_sleep_h = round(today_wellness.get("remSleepSecs", 0) / 3600, 1) if today_wellness.get("remSleepSecs") else "k.A."

    # 7-Tage Durchschnitte berechnen
    rhr_list = [d.get("restingHR") for d in wellness_data if d.get("restingHR")]
    hrv_list = [d.get("hrv") for d in wellness_data if d.get("hrv")]
    
    avg_7d_rhr = round(sum(rhr_list) / len(rhr_list), 1) if rhr_list else "k.A."
    avg_7d_hrv = round(sum(hrv_list) / len(hrv_list), 1) if hrv_list else "k.A."

    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""
Du bist mein persönlicher Ausdauer-Coach. Hier ist mein Profil:
{PROFIL_KONTEXT}

Meine heutigen Regenerationswerte vom {heute}:
- Ruhepuls: {rhr} bpm (7-Tage-Schnitt: {avg_7d_rhr} bpm)
- HFV: {hrv} ms (7-Tage-Schnitt: {avg_7d_hrv} ms)
- Schlaf gesamt: {total_sleep_h} h (Score: {sleep_score})
  * Tiefschlaf: {deep_sleep_h} h
  * REM-Schlaf: {rem_sleep_h} h
- Stresslevel: {stress}

Erstelle mein morgendliches Briefing:
1. **Regenerationsanalyse:** Vergleich von Heute vs. 7-Tage-Schnitt (Ruhepuls & HFV unter Berücksichtigung von Duloxetin/Trazodon) sowie Qualität der Schlafarchitektur.
2. **Konkrete Trainingsempfehlung für heute:** 
   - Eindeutige Vorgabe: Entweder Ruhetag ODER Training.
   - Falls Training: Exakte Sportart (Rollentrainer oder Lauf), Dauer in Minuten, Watt- bzw. Pulszielkorridor.
3. **Praxistipp für den Tag:** Fokus auf Flüssigkeitshaushalt oder Alltagsbewegung.
"""
    res = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    send_telegram(f"🌅 *Guten Morgen! Dein Tagesplan ({heute})*\n\n" + res.text)
    return {"status": "morning briefing sent"}

@app.get("/")
def health_check():
    return {"status": "running"}
