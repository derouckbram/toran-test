import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import urllib.parse
from datetime import datetime, timedelta
import base64  # Added for the clickable image trick

# --- Page Config for TV ---
st.set_page_config(page_title="Toran Welcome Screen", layout="wide", page_icon="🚁")

# --- Auto-Refresh for Unattended TV (Refreshes every 5 minutes) ---
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
        
        /* Hide sidebar completely on the TV screen */
        [data-testid="collapsedControl"] { display: none; }
        [data-testid="stSidebar"] { display: none; }
        header { display: none !important; }

        /* Typography */
        .welcome-title {
            font-size: 70px;
            font-weight: 900;
            color: #000000;
            line-height: 1.1;
            margin-bottom: 10px;
        }
        .welcome-subtitle {
            font-size: 35px;
            font-weight: 600;
            color: #666666;
            margin-bottom: 40px;
        }
        .clock-text {
            font-size: 50px;
            font-weight: 800;
            color: #E4D18C; /* Toran Beige */
            text-align: right;
        }
        
        /* Highlight boxes */
        .info-card {
            background-color: #F8F8F8;
            border-left: 10px solid #E4D18C; 
            padding: 30px;
            border-radius: 12px;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
            margin-bottom: 30px;
        }
        .info-card h3 { font-size: 35px; margin: 0 0 10px 0; color: #000000; }
        .info-card p { font-size: 25px; margin: 0; color: #666666; }
        
        /* Weather Widget */
        .weather-card {
            background-color: #000000;
            color: #FFFFFF;
            padding: 30px;
            border-radius: 12px;
            text-align: center;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.2);
        }
        .weather-temp { font-size: 60px; font-weight: 800; color: #E4D18C; }
        .weather-desc { font-size: 25px; color: #999999; }
        
        /* Flight Board Table */
        .flight-board {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
            font-size: 22px;
        }
        .flight-board th {
            background-color: #E4D18C;
            color: #000000;
            padding: 15px;
            text-align: left;
            font-weight: 800;
        }
        .flight-board td {
            padding: 15px;
            border-bottom: 1px solid #E2E8F0;
            color: #666666;
            font-weight: 600;
        }
        .flight-board tr:nth-child(even) { background-color: #F8F8F8; }
        .active-flight td { background-color: rgba(228, 209, 140, 0.2) !important; color: #000000; font-weight: 800; }
        
        /* Clickable Logo Hover Effect */
        .clickable-logo {
            transition: opacity 0.2s ease;
        }
        .clickable-logo:hover {
            opacity: 0.8;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

apply_tv_style()

# --- Time & Weather ---
now_be = pd.Timestamp.now('Europe/Brussels')

@st.cache_data(ttl=600)
def get_ebkt_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=50.8172&longitude=3.2047&current=temperature_2m,wind_speed_10m,wind_direction_10m&wind_speed_unit=kn"
        data = requests.get(url).json()['current']
        return {
            "temp": f"{data['temperature_2m']}°C",
            "wind": f"{data['wind_speed_10m']} kts",
            "dir": f"{data['wind_direction_10m']}°"
        }
    except:
        return {"temp": "--°C", "wind": "-- kts", "dir": "--°"}

weather = get_ebkt_weather()

# --- Aircraft Image & Details Database ---
AIRCRAFT_DB = {
    "OOHXP": {
        "model": "Robinson R44 Raven II",
        "image": "raven2.jpg", 
        "seats": "4 Seats",
        "cruise": "109 kts"
    },
    "OOMOO": {
        "model": "Robinson R44 Raven I",
        "image": "raven1.jpg",
        "seats": "4 Seats",
        "cruise": "109 kts"
    },
    "OOSKH": {
        "model": "Guimbal Cabri G2",
        "image": "cabri.jpg",
        "seats": "2 Seats",
        "cruise": "90 kts"
    }
}

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

@st.cache_data(ttl=180) 
def get_todays_flights():
    t_sess = get_authenticated_session("https://admin.toran.be", "/login", st.secrets["TORAN_EMAIL"], st.secrets["TORAN_PASS"])
    if not t_sess: return []
    
    xsrf = t_sess.cookies.get('XSRF-TOKEN')
    if xsrf: t_sess.headers.update({'X-XSRF-TOKEN': urllib.parse.unquote(xsrf)})

    year, week, _ = now_be.isocalendar()
    
    todays_flights = []
    try:
        url = f"https://admin.toran.be/api/planning?week={week}&year={year}"
        resp = t_sess.get(url, timeout=10).json()
        
        id_map = {str(h['id']): h['title'].upper().split(' ')[0] for h in resp.get('helis', [])}
        
        for e in resp.get('entries', []):
            if e.get('status') == 'confirmed':
                start = pd.to_datetime(e['reserved_start_datetime']).tz_convert('Europe/Brussels')
                
                # Check if flight is TODAY
                if start.date() == now_be.date():
                    tail_raw = id_map.get(str(e['heli_id']), "UNKNOWN")
                    todays_flights.append({
                        "time": start,
                        "time_str": start.strftime('%H:%M'),
                        "customer": f"{e.get('customer_first_name', '')} {e.get('customer_last_name', '')}".strip(),
                        "type": str(e.get('booking_type', 'Flight')).capitalize(),
                        "tail": tail_raw,
                        "instructor": e.get('instructor_name', 'Toran Team') 
                    })
                    
        todays_flights.sort(key=lambda x: x['time'])
    except: pass
    
    return todays_flights

# --- UI BUILDER ---
flights = get_todays_flights()

# Header: Logo on Left (Clickable), Clock on Right
head_col1, head_col2 = st.columns([1, 1])
with head_col1:
    try:
        # Convert local image to base64 so it can be wrapped in a hyperlink
        with open("toran_logo.png", "rb") as image_file:
            encoded_logo = base64.b64encode(image_file.read()).decode()
            
        # The href="/" tells it to return to the main Streamlit page
        logo_html = f'''
            <a href="/" target="_self">
                <img class="clickable-logo" src="data:image/png;base64,{encoded_logo}" width="300" title="Return to Forecast Dashboard">
            </a>
        '''
        st.markdown(logo_html, unsafe_allow_html=True)
    except FileNotFoundError:
        # Fallback text that is also clickable
        st.markdown('<a href="/" target="_self" style="text-decoration:none;"><h1 style="color:#000000;" class="clickable-logo">TORAN HELICOPTERS</h1></a>', unsafe_allow_html=True)

with head_col2:
    st.markdown(f'<div class="clock-text">{now_be.strftime("%H:%M")} Local</div>', unsafe_allow_html=True)

st.markdown("<br><br>", unsafe_allow_html=True)

# Find the flight happening RIGHT NOW or NEXT (within a 90 min window)
active_flight = None
for f in flights:
    time_diff = (f['time'] - now_be).total_seconds() / 60 # difference in minutes
    if -30 <= time_diff <= 90: # 30 mins ago to 90 mins from now
        active_flight = f
        break

if active_flight and active_flight['customer']:
    # 1. HUGE WELCOME TEXT FOR CURRENT CUSTOMER
    st.markdown(f'<div class="welcome-title">Welcome, {active_flight["customer"]}!</div>', unsafe_allow_html=True)
    
    col1, col2 = st.columns([1.5, 1])
    
    with col1:
        st.markdown(f"""
            <div class="info-card">
                <h3>🚁 Your Flight Details</h3>
                <p><b>Time:</b> {active_flight['time_str']} Local</p>
                <p><b>Aircraft:</b> {active_flight['tail']}</p>
                <p><b>Instructor:</b> {active_flight['instructor']}</p>
            </div>
        """, unsafe_allow_html=True)
        
    with col2:
        # Show Aircraft Image dynamically based on the DB
        tail_clean = active_flight['tail'].replace("-", "").replace(" ", "")
        ac_info = AIRCRAFT_DB.get(tail_clean, None)
        if ac_info:
            try:
                st.image(ac_info['image'], use_container_width=True, caption=active_flight['tail'])
            except FileNotFoundError:
                pass # Skip if image isn't uploaded yet
        else:
            st.markdown("<br>", unsafe_allow_html=True)
            
        st.markdown(f"""
            <div class="weather-card" style="margin-top: 20px;">
                <div style="font-size: 20px; font-weight: bold; color: #E4D18C;">EBKT WEATHER CONDITIONS</div>
                <div class="weather-temp">{weather['temp']}</div>
                <div class="weather-desc">Wind: {weather['wind']} at {weather['dir']}</div>
            </div>
        """, unsafe_allow_html=True)

else:
    # 1. GENERAL WELCOME (If no specific flight is happening this exact hour)
    st.markdown('<div class="welcome-title">Welcome to Toran Helicopters</div>', unsafe_allow_html=True)
    st.markdown('<div class="welcome-subtitle">Aviation Excellence in Kortrijk</div>', unsafe_allow_html=True)

# --- DAILY FLIGHT BOARD ---
if flights:
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div style="font-size: 30px; font-weight: 800; color: #000000; border-bottom: 4px solid #E4D18C; display: inline-block; margin-bottom: 10px;">TODAY\'S DEPARTURES</div>', unsafe_allow_html=True)
    
    board_html = '<table class="flight-board"><tr><th>Time</th><th>Aircraft</th><th>Pilot / Student</th><th>Instructor</th></tr>'
    
    for f in flights:
        # Highlight the row if it's the flight happening right now
        row_class = 'class="active-flight"' if active_flight and active_flight == f else ''
        board_html += f'<tr {row_class}><td>{f["time_str"]}</td><td>{f["tail"]}</td><td>{f["customer"]}</td><td>{f["instructor"]}</td></tr>'
        
    board_html += '</table>'
    st.markdown(board_html, unsafe_allow_html=True)

else:
    # --- IF ZERO FLIGHTS SCHEDULED ALL DAY ---
    st.markdown("<br>", unsafe_allow_html=True)
    empty_col1, empty_col2 = st.columns([1.5, 1])
    
    with empty_col1:
        # Try to show a default image here if no flights are running
        try:
            st.image("raven2.jpg", use_container_width=True) # Defaults to the Raven II image
        except FileNotFoundError:
            st.info("Upload your photos ('raven1.jpg', 'raven2.jpg', 'cabri.jpg') to GitHub to see them here!")
        
    with empty_col2:
        st.markdown(f"""
            <div class="weather-card">
                <div style="font-size: 20px; font-weight: bold; color: #E4D18C;">EBKT WEATHER CONDITIONS</div>
                <div class="weather-temp">{weather['temp']}</div>
                <div class="weather-desc">Wind: {weather['wind']} at {weather['dir']}</div>
            </div>
        """, unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(f"""
            <div class="info-card" style="text-align: center;">
                <h3>Our Fleet</h3>
                <p>Robinson R44 Raven I</p>
                <p>Robinson R44 Raven II</p>
                <p>Guimbal Cabri G2</p>
            </div>
        """, unsafe_allow_html=True)
