import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import urllib.parse
import re  
from datetime import datetime
import base64

st.set_page_config(page_title="Welcome to Toran", layout="wide", initial_sidebar_state="collapsed")

# --- Weather Setup ---
@st.cache_data(ttl=900) 
def get_ebkt_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=50.8172&longitude=3.2047&current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,cloud_cover,pressure_msl,surface_pressure,wind_speed_10m,wind_direction_10m,visibility&wind_speed_unit=kn"
        data = requests.get(url, timeout=5).json()['current']
        clouds = data['cloud_cover']
        c_desc = "Clear" if clouds < 10 else "Few" if clouds < 25 else "Sct" if clouds < 50 else "Bkn" if clouds < 85 else "Ovc"
        return {
            "temp": f"{data['temperature_2m']}°C",
            "wind_spd": f"{data['wind_speed_10m']} KT",
            "wind_deg": f"{data['wind_direction_10m']}°",
            "clouds": f"{c_desc} ({clouds}%)",
            "vis": f"{round(data['visibility'] / 1000, 1)} KM",
            "qnh": f"{round(data['surface_pressure'])} hPa"
        }
    except: return {"temp": "--", "wind_spd": "--", "wind_deg": "--", "clouds": "--", "vis": "--", "qnh": "--"}

weather = get_ebkt_weather()

# --- Aircraft Images ---
AIRCRAFT_DB = {
    "OOHXP": {"image": "raven2.jpg"},
    "OOMOO": {"image": "raven1.jpg"},
    "OOSKH": {"image": "cabri.jpg"}
}

# --- Auth Helper ---
def get_authenticated_session(base_url, login_path, email, password):
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
    login_url = f"{base_url.rstrip('/')}{login_path}"
    try:
        r = session.get(login_url, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        token = soup.find('meta', {'name': 'csrf-token'})
        csrf = token.get('content') if token else soup.find('input', {'name': '_token'}).get('value')
        session.post(login_url, data={"_token": csrf, "email": email, "password": password, "remember": "on"}, timeout=15)
        return session
    except: return None

def normalize_tail(tail):
    if not tail: return "UNKNOWN"
    return str(tail).upper().replace("-", "").replace(" ", "")

# --- DATA FETCH (TV Version - Optimized for Speed) ---
@st.cache_data(ttl=900)
def fetch_tv_data():
    t_sess = get_authenticated_session("https://admin.toran.be", "/login", st.secrets["TORAN_EMAIL"], st.secrets["TORAN_PASS"])
    if not t_sess: return pd.DataFrame()

    # Address Book
    cust_map = {}
    try:
        c_resp = t_sess.get("https://admin.toran.be/api/customers", timeout=10).json()
        for c in c_resp.get('data', c_resp): cust_map[str(c.get('id'))] = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
    except: pass

    book_list, now = [], pd.Timestamp.utcnow().tz_localize(None)
    try:
        resp = t_sess.get(f"https://admin.toran.be/api/planning?week={now.isocalendar()[1]}&year={now.isocalendar()[0]}").json()
        id_map = {str(h['id']): h['title'].upper() for h in resp.get('helis', [])}
        for f in resp.get('entries', []):
            if f.get('status') == 'confirmed':
                start = pd.to_datetime(f['reserved_start_datetime']).tz_convert(None)
                reg = id_map.get(str(f.get('heli_id', '')))
                
                guest = f"{f.get('customer_first_name','')} {f.get('customer_last_name','')}".strip()
                if not guest and f.get('customer_id'): guest = cust_map.get(str(f.get('customer_id')), '')
                if not guest: guest = str(f.get('title', 'Guest'))

                if reg: book_list.append({
                    'MergeKey': normalize_tail(reg), 'Registration': reg, 'Start': start, 
                    'End': pd.to_datetime(f['reserved_end_datetime']).tz_convert(None),
                    'Type': str(f.get('booking_type', 'Flight')).capitalize(),
                    'Details': guest, 'Departure': f.get('departure_airport_name', 'EBKT')
                })
    except: pass
    
    return pd.DataFrame(book_list)

raw_books_df = fetch_tv_data()

# --- TV LAYOUT ---
try:
    with open("Asset 4@4x.jpg", "rb") as f: logo_data = base64.b64encode(f.read()).decode()
    inline_logo = f'<a href="/" target="_self"><img src="data:image/jpeg;base64,{logo_data}" style="height:70px; vertical-align:middle; margin-left:15px; border-radius:8px; cursor: pointer;"></a>'
except: inline_logo = '<a href="/" target="_self" style="text-decoration:none;">🚁</a>'

st.markdown("""<meta http-equiv="refresh" content="900">
    <style>
    [data-testid="collapsedControl"], [data-testid="stSidebar"], header { display: none !important; }
    .stApp { margin-top: -95px !important; }
    .welcome-title { font-size: 78px; font-weight: 900; color: #000; line-height: 1; margin-bottom: 5px; }
    .welcome-subtitle { font-size: 34px; font-weight: 600; color: #666; margin-bottom: 25px; }
    .clock-text { font-size: 45px; font-weight: 800; color: #E4D18C; text-align: right; }
    .info-card { background-color: #F8F8F8; border-left: 10px solid #E4D18C; padding: 20px; border-radius: 12px; box-shadow: 0 10px 15px rgba(0,0,0,0.05); margin-bottom:20px; }
    .weather-card { background-color: #000; color: #FFF; padding: 20px; border-radius: 12px; }
    .weather-val { font-size: 26px; font-weight: 800; color: #E4D18C; }
    .weather-lbl { font-size: 13px; color: #999; text-transform: uppercase; font-weight:700; }
    .flight-board { width: 100%; border-collapse: collapse; font-size: 20px; }
    .flight-board th { background-color: #E4D18C; padding: 12px; text-align: left; font-weight: 800; }
    .flight-board td { padding: 12px; border-bottom: 1px solid #EEE; color: #666; font-weight: 600; }
    .active-row td { background-color: rgba(228, 209, 140, 0.2) !important; color: #000 !important; font-weight: 800; }
    </style>
    <script>
    function updateClock() {
        const now = new Date();
        const timeStr = now.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) + ' Local';
        document.getElementById('live-clock').innerText = timeStr;
    }
    setInterval(updateClock, 1000);
    </script>
""", unsafe_allow_html=True)

now_be = pd.Timestamp.now('Europe/Brussels')
col_left, col_right = st.columns([1.8, 1])

with col_right:
    st.markdown(f'<div id="live-clock" class="clock-text">{now_be.strftime("%H:%M")} Local</div>', unsafe_allow_html=True)
    st.markdown(f'<div style="text-align:right; margin: 10px 0 20px 0;">{inline_logo}</div>', unsafe_allow_html=True)
    if not raw_books_df.empty:
        raw_books_df['LStart'] = raw_books_df['Start'].dt.tz_localize('UTC').dt.tz_convert('Europe/Brussels')
        today_flights = raw_books_df[(raw_books_df['LStart'].dt.date == now_be.date())].sort_values('LStart')
        active_f = None
        for _, f in today_flights.iterrows():
            if f['LStart'] > now_be - pd.Timedelta(minutes=15): active_f = f; break
        st.markdown("<h3 style='font-size:24px; font-weight:800; border-bottom:3px solid #E4D18C; display:inline-block; margin-bottom:10px;'>TODAY'S DEPARTURES</h3>", unsafe_allow_html=True)
        tbl = '<table class="flight-board"><tr><th>Time</th><th>Tail</th><th>Guest</th></tr>'
        for _, f in today_flights.iterrows():
            cls = 'class="active-row"' if active_f is not None and active_f.equals(f) else ''
            tbl += f'<tr {cls}><td>{f["LStart"].strftime("%H:%M")}</td><td>{f["Registration"]}</td><td>{f["Details"]}</td></tr>'
        st.markdown(tbl + '</table>', unsafe_allow_html=True)

with col_left:
    if active_f is not None:
        guest = str(active_f['Details']) if str(active_f['Details']).strip() else "Guest"
        st.markdown(f'<div class="welcome-title">Welcome, {guest}!</div>', unsafe_allow_html=True)
        st.markdown('<div class="welcome-subtitle">Prepped and ready for departure</div>', unsafe_allow_html=True)
        st.markdown(f"""<div class="info-card"><h3 style="margin:0;">🚁 Flight Details</h3><br>
            <p style="font-size:24px; margin:0;"><b>Departs:</b> {active_f["LStart"].strftime("%H:%M")} Local</p>
            <p style="font-size:24px; margin:0;"><b>Airport:</b> {active_f.get("Departure", "EBKT")}</p>
            <p style="font-size:24px; margin:0;"><b>Aircraft:</b> {active_f["Registration"]}</p></div>""", unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="welcome-title">Welcome to Toran</div>', unsafe_allow_html=True)
        st.markdown('<div class="welcome-subtitle">Aviation Excellence in Kortrijk (EBKT)</div>', unsafe_allow_html=True)

    st.markdown(f"""<div class="weather-card"><div style="font-size:14px; color:#E4D18C; font-weight:800; margin-bottom:10px;">EBKT PILOT WEATHER</div>
        <div style="display:grid; grid-template-columns: 1fr 1fr 1fr; gap:15px;">
            <div><div class="weather-lbl">Temp</div><div class="weather-val">{weather['temp']}</div></div>
            <div><div class="weather-lbl">Wind</div><div class="weather-val">{weather['wind_spd']}</div></div>
            <div><div class="weather-lbl">Dir</div><div class="weather-val">{weather['wind_deg']}</div></div>
            <div><div class="weather-lbl">Clouds</div><div class="weather-val" style="font-size:18px;">{weather['clouds']}</div></div>
            <div><div class="weather-lbl">Vis</div><div class="weather-val">{weather['vis']}</div></div>
            <div><div class="weather-lbl">QNH</div><div class="weather-val">{weather['qnh']}</div></div>
        </div></div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    tail_c = normalize_tail(active_f['Registration']) if active_f is not None else "OOHXP"
    img = AIRCRAFT_DB.get(tail_c, {}).get('image', 'raven2.jpg')
    try: st.image(img, use_container_width=True)
    except: pass
