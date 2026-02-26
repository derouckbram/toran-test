import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import urllib.parse
from datetime import datetime, timedelta

# --- Page Config for TV ---
st.set_page_config(page_title="Toran Welcome Screen", layout="wide", page_icon="🚁")

# --- Auto-Refresh for Unattended TV (Refreshes every 5 minutes / 300 seconds) ---
st.markdown('<meta http-equiv="refresh" content="300">', unsafe_allow_html=True)

# --- Toran Official Brand Style Engine (TV Version) ---
def apply_tv_style():
    st.markdown(
        """
        <style>
        .stApp {
            background-color: #FFFFFF;
            font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            color: #000000;
        }
        
        /* Hide sidebar completely on the TV screen for a clean look */
        /* To show the sidebar while testing, put /* before the lines below */
        [data-testid="collapsedControl"] { display: none; }
        [data-testid="stSidebar"] { display: none; }
        header { display: none !important; }

        /* Huge Welcome Text */
        .welcome-title {
            font-size: 80px;
            font-weight: 900;
            color: #000000;
            line-height: 1.1;
            margin-bottom: 20px;
        }
        .welcome-subtitle {
            font-size: 40px;
            font-weight: 600;
            color: #666666; /* Dark Gray */
            margin-bottom: 40px;
        }
        
        /* Highlight boxes */
        .info-card {
            background-color: #F8F8F8;
            border-left: 10px solid #E4D18C; /* Toran Beige */
            padding: 30px;
            border-radius: 12px;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
            margin-bottom: 30px;
        }
        .info-card h3 {
            font-size: 35px;
            margin: 0 0 10px 0;
            color: #000000;
        }
        .info-card p {
            font-size: 25px;
            margin: 0;
            color: #666666;
        }
        
        /* Weather Widget */
        .weather-card {
            background-color: #000000;
            color: #FFFFFF;
            padding: 30px;
            border-radius: 12px;
            text-align: center;
        }
        .weather-temp {
            font-size: 60px;
            font-weight: 800;
            color: #E4D18C; /* Toran Beige */
        }
        .weather-desc {
            font-size: 25px;
            color: #999999; /* Light Gray */
        }
        </style>
        """,
        unsafe_allow_html=True
    )

apply_tv_style()

# --- Aircraft Image & Details Database ---
# REPLACE THESE URLs with links to actual photos of your aircraft!
AIRCRAFT_DB = {
    "OOHXP": {
        "model": "Robinson R44 Astro / Raven I",
        "image": "https://images.unsplash.com/photo-1549642646-60840c5f2991?auto=format&fit=crop&q=80&w=1000", 
        "seats": "4 Seats",
        "cruise": "109 kts"
    },
    "OOMOO": {
        "model": "Robinson R44 Raven II",
        "image": "https://images.unsplash.com/photo-1549642646-60840c5f2991?auto=format&fit=crop&q=80&w=1000",
        "seats": "4 Seats",
        "cruise": "109 kts"
    },
    "OOXPY": {
        "model": "Guimbal Cabri G2",
        "image": "https://images.unsplash.com/photo-1596328340158-750d5f242583?auto=format&fit=crop&q=80&w=1000",
        "seats": "2 Seats",
        "cruise": "90 kts"
    }
}

# --- Weather API (Kortrijk EBKT) ---
@st.cache_data(ttl=600) # Cache for 10 mins
def get_ebkt_weather():
    try:
        # Open-Meteo is free and requires no API key
        url = "https://api.open-meteo.com/v1/forecast?latitude=50.8172&longitude=3.2047&current=temperature_2m,wind_speed_10m,wind_direction_10m&wind_speed_unit=kn"
        data = requests.get(url).json()['current']
        return {
            "temp": f"{data['temperature_2m']}°C",
            "wind": f"{data['wind_speed_10m']} kts",
            "dir": f"{data['wind_direction_10m']}°"
        }
    except:
        return {"temp": "--°C", "wind": "-- kts", "dir": "--°"}

# --- Toran API Logic ---
def get_authenticated_session(base_url, login_path, email, password):
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
    login_url = f"{base_url.rstrip('/')}{login_path}"
    try:
        r = session.get(login_url, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        token = soup.find('meta', {'name': 'csrf-token'})
        csrf = token.get('content') if token else soup.find('input', {'name': '_token'}).get('value')
        payload = {"_token": csrf, "email": email, "password": password, "remember": "on"}
        session.post(login_url, data=payload, headers={'Referer': login_url}, timeout=15)
        return session
    except: return None

@st.cache_data(ttl=180) # Cache for 3 minutes to avoid hammering the Toran API
def get_next_flight():
    t_sess = get_authenticated_session("https://admin.toran.be", "/login", st.secrets["TORAN_EMAIL"], st.secrets["TORAN_PASS"])
    if not t_sess: return None
    
    xsrf = t_sess.cookies.get('XSRF-TOKEN')
    if xsrf: t_sess.headers.update({'X-XSRF-TOKEN': urllib.parse.unquote(xsrf)})

    now = datetime.utcnow()
    year, week, _ = now.isocalendar()
    
    try:
        url = f"https://admin.toran.be/api/planning?week={week}&year={year}"
        resp = t_sess.get(url, timeout=10).json()
        
        id_map = {str(h['id']): h['title'].upper().split(' ')[0] for h in resp.get('helis', [])}
        
        upcoming_flights = []
        for e in resp.get('entries', []):
            if e.get('status') == 'confirmed':
                start = pd.to_datetime(e['reserved_start_datetime']).tz_localize(None)
                
                # Find flights happening TODAY, starting anywhere from 1 hour ago up to the end of the day
                if start.date() == now.date() and start > (now - timedelta(hours=1)):
                    tail_raw = id_map.get(str(e['heli_id']), "UNKNOWN")
                    tail_clean = tail_raw.replace("-", "").replace(" ", "")
                    
                    upcoming_flights.append({
                        "time": start,
                        "first_name": e.get('customer_first_name', '').strip(),
                        "last_name": e.get('customer_last_name', '').strip(),
                        "type": str(e.get('booking_type', 'Flight')).capitalize(),
                        "tail": tail_raw,
                        "tail_clean": tail_clean,
                        "instructor": e.get('instructor_name', 'your instructor') 
                    })
                    
        if upcoming_flights:
            # Sort by time and return the absolute next one
            upcoming_flights.sort(key=lambda x: x['time'])
            return upcoming_flights[0]
            
    except: pass
    return None

# --- UI BUILDER ---
weather = get_ebkt_weather()
next_flight = get_next_flight()

# Toran Logo
try:
    st.image("toran_logo.png", width=250)
except FileNotFoundError:
    pass # Will gracefully skip if the logo isn't uploaded yet
st.markdown("<br><br>", unsafe_allow_html=True)

if next_flight and next_flight['last_name']:
    
    # 1. HUGE WELCOME TEXT
    st.markdown(f'<div class="welcome-title">Welcome, Mr/Ms. {next_flight["last_name"]}!</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="welcome-subtitle">We are thrilled to have you here at Toran Helicopters today.</div>', unsafe_allow_html=True)
    
    col1, col2 = st.columns([1.5, 1])
    
    with col1:
        # 2. FLIGHT PURPOSE BOX
        type_str = "Schooling flight" if "Schooling" in next_flight['type'] else "Helicopter flight"
        instr_str = f"with {next_flight['instructor']}" if next_flight['instructor'] != 'your instructor' else "with our flight team"
        
        st.markdown(f"""
            <div class="info-card">
                <h3>🗓️ Your Schedule</h3>
                <p>Today you are here for your <b>{type_str}</b> {instr_str}.</p>
                <p>Scheduled Start: <b>{next_flight['time'].strftime('%H:%M')} Local</b></p>
            </div>
        """, unsafe_allow_html=True)
        
        # 3. AIRCRAFT INFO BOX
        tail_clean = next_flight['tail_clean']
        ac_info = AIRCRAFT_DB.get(tail_clean, {"model": "Toran Helicopter", "image": "https://images.unsplash.com/photo-1549642646-60840c5f2991", "seats": "N/A", "cruise": "N/A"})
        
        st.markdown(f"""
            <div class="info-card">
                <h3>🚁 Your Aircraft: {next_flight['tail']}</h3>
                <p>Model: {ac_info['model']}</p>
                <p>Configuration: {ac_info['seats']} | Cruise: {ac_info['cruise']}</p>
            </div>
        """, unsafe_allow_html=True)

    with col2:
        # 4. AIRCRAFT IMAGE
        st.image(ac_info['image'], use_container_width=True, caption=next_flight['tail'])
        
        # 5. WEATHER WIDGET
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(f"""
            <div class="weather-card">
                <div style="font-size: 20px; font-weight: bold; color: #E4D18C;">EBKT WEATHER CONDITIONS</div>
                <div class="weather-temp">{weather['temp']}</div>
                <div class="weather-desc">Wind: {weather['wind']} at {weather['dir']}</div>
            </div>
        """, unsafe_allow_html=True)

else:
    # --- FALLBACK IF NO FLIGHTS ARE UPCOMING TODAY ---
    st.markdown('<div class="welcome-title">Welcome to Toran Helicopters</div>', unsafe_allow_html=True)
    st.markdown('<div class="welcome-subtitle">Aviation Excellence in Kortrijk</div>', unsafe_allow_html=True)
    
    st.image("https://images.unsplash.com/photo-1549642646-60840c5f2991", use_container_width=True)
    
    st.markdown(f"""
        <div class="weather-card" style="margin-top: 30px;">
            <div style="font-size: 20px; font-weight: bold; color: #E4D18C;">EBKT CURRENT CONDITIONS</div>
            <div class="weather-temp">{weather['temp']}</div>
            <div class="weather-desc">Wind: {weather['wind']} at {weather['dir']}</div>
        </div>
    """, unsafe_allow_html=True)
