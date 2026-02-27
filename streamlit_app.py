import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import urllib.parse
import re  
from datetime import datetime, timedelta
import base64

# --- Page Config ---
st.set_page_config(page_title="Toran Operations Center", layout="wide", page_icon="🚁")

# --- Weather Setup ---
@st.cache_data(ttl=600) 
def get_ebkt_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=50.8172&longitude=3.2047&current=temperature_2m,cloud_cover,surface_pressure,wind_speed_10m,wind_direction_10m,visibility&wind_speed_unit=kn"
        data = requests.get(url, timeout=5).json()['current']
        clouds = data['cloud_cover']
        c_desc = "Clear" if clouds < 10 else "Few" if clouds < 25 else "Sct" if clouds < 50 else "Bkn" if clouds < 85 else "Ovc"
        return {
            "temp": f"{data['temperature_2m']}°C",
            "wind_spd": f"{data['wind_speed_10m']} KT",
            "wind_deg": f"{data['wind_direction_10m']}°",
            "clouds": f"{c_desc}",
            "vis": f"{round(data['visibility'] / 1000)}KM",
            "qnh": f"{round(data['surface_pressure'])}hPa"
        }
    except: return {"temp": "--", "wind_spd": "--", "wind_deg": "--", "clouds": "--", "vis": "--", "qnh": "--"}

weather = get_ebkt_weather()

# --- Aircraft Image Database ---
AIRCRAFT_DB = {
    "OOHXP": {"image": "raven2.jpg"},
    "OOMOO": {"image": "raven1.jpg"},
    "OOSKH": {"image": "cabri.jpg"}
}

# --- Shared Brand CSS ---
def apply_toran_style():
    st.markdown("""
        <style>
        .stApp { background-color: #FFFFFF; font-family: 'Segoe UI', sans-serif; color: #000000; }
        [data-testid="stSidebar"] { background-color: #F8F8F8; border-right: 1px solid #999999; }
        .stProgress > div > div > div > div { background-color: #E4D18C !important; }
        </style>
    """, unsafe_allow_html=True)

apply_toran_style()

# --- Auth & Fetch Logic ---
def get_authenticated_session(base_url, login_path, email, password):
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
    login_url = f"{base_url.rstrip('/')}{login_path}"
    try:
        r = session.get(login_url, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        csrf = soup.find('meta', {'name': 'csrf-token'}).get('content')
        session.post(login_url, data={"_token": csrf, "email": email, "password": password, "remember": "on"}, timeout=15)
        return session
    except: return None

def fetch_resource(session, base_url, resource_name):
    for prefix in ["/admin/nova-api/", "/nova-api/"]:
        resp = session.get(f"{base_url.rstrip('/')}{prefix}{resource_name}", timeout=10)
        if resp.status_code == 200: return resp.json()
    return None

def normalize_tail(tail):
    return str(tail).upper().replace("-", "").replace(" ", "")

@st.cache_data(ttl=300)
def fetch_data(end_date):
    c_sess = get_authenticated_session("https://toran-camo.flightapp.be", "/admin/login", st.secrets["CAMO_EMAIL"], st.secrets["CAMO_PASS"])
    t_sess = get_authenticated_session("https://admin.toran.be", "/login", st.secrets["TORAN_EMAIL"], st.secrets["TORAN_PASS"])
    
    # Simple CAMO fetch for Registration matching
    maint = fetch_resource(c_sess, "https://toran-camo.flightapp.be", "upcoming-aircraft-maintenances?perPage=50")
    ac_list = []
    if maint:
        for r in maint.get('resources', []):
            fields = {f['attribute']: f['value'] for f in r.get('fields', [])}
            reg = str(fields.get('aircraft', "Unknown")).split(' ')[0].strip().upper()
            ac_list.append({'Registration': reg, 'MergeKey': normalize_tail(reg)})
    df_ac = pd.DataFrame(ac_list).drop_duplicates('MergeKey')

    # Booking Fetch
    xsrf = t_sess.cookies.get('XSRF-TOKEN')
    t_sess.headers.update({'X-XSRF-TOKEN': urllib.parse.unquote(xsrf)})
    
    book_list, now = [], pd.Timestamp.utcnow().tz_localize(None)
    try:
        week_data = t_sess.get(f"https://admin.toran.be/api/planning?week={now.isocalendar()[1]}&year={now.isocalendar()[0]}").json()
        id_map = {str(h['id']): h['title'].upper() for h in week_data.get('helis', [])}
        for f in week_data.get('entries', []):
            if f.get('status') == 'confirmed':
                start = pd.to_datetime(f['reserved_start_datetime']).tz_convert(None)
                reg = id_map.get(str(f.get('heli_id', '')))
                if reg: book_list.append({
                    'MergeKey': normalize_tail(reg), 'Registration': reg, 'Start': start, 
                    'Details': f"{f.get('customer_first_name','')} {f.get('customer_last_name','')}".strip(),
                    'Departure': f.get('departure_airport_name', 'EBKT'),
                    'Type': str(f.get('booking_type', 'Flight'))
                })
    except: pass
    return df_ac, pd.DataFrame(book_list)

# --- UI Setup ---
if "mode" in st.query_params and st.query_params["mode"] == "tv": default_idx = 1
else: default_idx = 0

with st.sidebar:
    app_mode = st.radio("🖥️ Mode", ["Dashboard", "Guest Welcome Screen"], index=default_idx)
    st.query_params["mode"] = "tv" if app_mode == "Guest Welcome Screen" else "admin"
    if st.button('🔄 Refresh'): st.cache_data.clear(); st.rerun()

df_ac, df_books = fetch_data(datetime.today())

if app_mode == "Dashboard":
    st.title("Toran Fleet Manager")
    st.dataframe(df_ac, use_container_width=True)

# ==========================================
# MODE 2: GUEST WELCOME SCREEN
# ==========================================
else:
    # 1. Image Assets
    try:
        with open("Asset 4@4x.jpg", "rb") as f: logo_data = base64.b64encode(f.read()).decode()
        inline_logo = f'<img src="data:image/jpeg;base64,{logo_data}" style="height:70px; vertical-align:middle; margin-left:20px; border-radius:10px;">'
    except: inline_logo = ""

    # 2. TV CSS
    st.markdown(f"""<meta http-equiv="refresh" content="60">
        <style>
        [data-testid="collapsedControl"], [data-testid="stSidebar"], header {{ display: none !important; }}
        .stApp {{ margin-top: -100px !important; padding: 20px !important; }}
        .welcome-title {{ font-size: 85px; font-weight: 900; color: #000; line-height: 1.1; margin:0; }}
        .welcome-subtitle {{ font-size: 38px; font-weight: 600; color: #666; margin-bottom: 20px; }}
        .clock-text {{ font-size: 55px; font-weight: 800; color: #E4D18C; text-align: right; line-height:1; }}
        .info-card {{ background-color: #F8F8F8; border-left: 12px solid #E4D18C; padding: 20px; border-radius: 12px; height: 160px; }}
        .weather-card {{ background-color: #000; color: #FFF; padding: 15px; border-radius: 12px; height: 160px; }}
        .weather-grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-top: 5px; }}
        .w-val {{ font-size: 22px; font-weight: 800; color: #E4D18C; }}
        .w-lbl {{ font-size: 11px; color: #999; text-transform: uppercase; font-weight:700; }}
        .flight-board {{ width: 100%; border-collapse: collapse; font-size: 22px; margin-top:10px; }}
        .flight-board th {{ background-color: #E4D18C; padding: 12px; text-align: left; font-weight: 800; }}
        .flight-board td {{ padding: 12px; border-bottom: 1px solid #EEE; color: #444; font-weight: 600; }}
        .active-row td {{ background-color: rgba(228, 209, 140, 0.25) !important; color: #000 !important; font-weight: 800; }}
        </style>
    """, unsafe_allow_html=True)

    now_be = pd.Timestamp.now('Europe/Brussels')
    
    # --- ROW 1: WELCOME & CLOCK ---
    c_welc, c_clk = st.columns([2.5, 1])
    
    # Process Flights
    today_flights, active_f = pd.DataFrame(), None
    if not df_books.empty:
        df_books['LStart'] = df_books['Start'].dt.tz_localize('UTC').dt.tz_convert('Europe/Brussels')
        today_flights = df_books[(df_books['LStart'].dt.date == now_be.date()) & (df_books['Type'] != 'Blocking')].sort_values('LStart')
        active_f = today_flights[today_flights['LStart'] > now_be - pd.Timedelta(minutes=15)].iloc[0] if not today_flights.empty else None

    with c_welc:
        guest = str(active_f['Details']) if active_f is not None and str(active_f['Details']).strip() else "to Toran"
        st.markdown(f'<div class="welcome-title">Welcome, {guest}!{inline_logo}</div>', unsafe_allow_html=True)
        st.markdown('<div class="welcome-subtitle">Your departure is prepped and ready</div>', unsafe_allow_html=True)

    with c_clk:
        st.markdown(f'<div class="clock-text">{now_be.strftime("%H:%M")} Local</div>', unsafe_allow_html=True)
        st.markdown(f'<div style="text-align:right;"><a href="/?mode=admin" target="_self" style="color:#EEE; text-decoration:none; font-size:12px;">Admin Dashboard</a></div>', unsafe_allow_html=True)

    # --- ROW 2: SPLIT CONTENT ---
    col_left, col_right = st.columns([1.8, 1])

    with col_left:
        # Mini Row for Info and Weather (Horizontal to save space)
        ci1, ci2 = st.columns(2)
        with ci1:
            if active_f is not None:
                st.markdown(f"""<div class="info-card"><h3>🚁 Flight Details</h3><p style="font-size:22px; margin:5px 0 0 0;">
                    <b>Departs:</b> {active_f["LStart"].strftime("%H:%M")}<br><b>Airport:</b> {active_f.get("Departure", "EBKT")}<br><b>Aircraft:</b> {active_f["Registration"]}</p></div>""", unsafe_allow_html=True)
            else:
                st.markdown('<div class="info-card"><h3>Toran Center</h3><p>Aviation Excellence<br>Kortrijk-Wevelgem</p></div>', unsafe_allow_html=True)
        with ci2:
            st.markdown(f"""<div class="weather-card"><div style="font-size:13px; color:#E4D18C; font-weight:800;">PILOT WEATHER EBKT</div>
                <div class="weather-grid">
                    <div><div class="w-lbl">Temp</div><div class="w-val">{weather['temp']}</div></div>
                    <div><div class="w-lbl">Wind</div><div class="w-val">{weather['wind_spd']}</div></div>
                    <div><div class="w-lbl">Dir</div><div class="w-val">{weather['wind_deg']}</div></div>
                    <div><div class="w-lbl">Vis</div><div class="w-val">{weather['vis']}</div></div>
                    <div><div class="w-lbl">Clouds</div><div class="w-val">{weather['clouds']}</div></div>
                    <div><div class="w-lbl">QNH</div><div class="w-val">{weather['qnh']}</div></div>
                </div></div>""", unsafe_allow_html=True)
        
        # Aircraft Picture sits directly below the cards
        st.markdown("<br>", unsafe_allow_html=True)
        tail_key = normalize_tail(active_f['Registration']) if active_f is not None else "OOHXP"
        img_file = AIRCRAFT_DB.get(tail_key, {"image": "raven2.jpg"})['image']
        try: st.image(img_file, use_container_width=True)
        except: st.warning(f"Add {img_file} to GitHub to see the aircraft photo here.")

    with col_right:
        # Today's Departures in a narrow column
        if not today_flights.empty:
            st.markdown("<h3 style='font-size:28px; font-weight:800; border-bottom:4px solid #E4D18C; display:inline-block; margin-bottom:10px;'>DEPARTURES</h3>", unsafe_allow_html=True)
            tbl = '<table class="flight-board"><tr><th>Time</th><th>Tail</th><th>Guest</th></tr>'
            for _, f in today_flights.iterrows():
                active_cls = 'class="active-row"' if active_f is not None and active_f.equals(f) else ''
                tbl += f'<tr {active_cls}><td>{f["LStart"].strftime("%H:%M")}</td><td>{f["Registration"]}</td><td>{f["Details"]}</td></tr>'
            st.markdown(tbl + '</table>', unsafe_allow_html=True)
        else:
            st.info("No more departures scheduled for today.")
