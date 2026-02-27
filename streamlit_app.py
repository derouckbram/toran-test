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

# --- Enhanced Pilot Weather Setup ---
@st.cache_data(ttl=600) 
def get_ebkt_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=50.8172&longitude=3.2047&current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,cloud_cover,pressure_msl,surface_pressure,wind_speed_10m,wind_direction_10m,visibility&wind_speed_unit=kn"
        data = requests.get(url, timeout=5).json()['current']
        clouds = data['cloud_cover']
        if clouds < 10: c_desc = "Clear"
        elif clouds < 25: c_desc = "Few"
        elif clouds < 50: c_desc = "Scattered"
        elif clouds < 85: c_desc = "Broken"
        else: c_desc = "Overcast"
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

# --- Aircraft Image Database ---
AIRCRAFT_DB = {
    "OOHXP": {"model": "Robinson R44 Raven II", "image": "raven2.jpg"},
    "OOMOO": {"model": "Robinson R44 Raven I", "image": "raven1.jpg"},
    "OOSKH": {"model": "Guimbal Cabri G2", "image": "cabri.jpg"}
}

# --- Style Engine ---
def apply_toran_style():
    st.markdown(
        """
        <style>
        .stApp { background-color: #FFFFFF; font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #000000; }
        .stTabs [data-baseweb="tab"] { background-color: #FFFFFF; border-radius: 4px !important; border: 1px solid #999999; color: #666666; font-weight: 600; }
        .stTabs [aria-selected="true"] { background-color: #E4D18C !important; color: #000000 !important; }
        [data-testid="metric-container"] { background-color: #FFFFFF; border: 1px solid #999999; border-radius: 8px; padding: 20px; border-left: 5px solid #E4D18C; }
        [data-testid="stSidebar"] { background-color: #F8F8F8; border-right: 1px solid #999999; }
        .stProgress > div > div > div > div { background-color: #E4D18C !important; }
        </style>
        """, unsafe_allow_html=True
    )

apply_toran_style()

# --- Helper Functions ---
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

def fetch_resource(session, base_url, resource_name):
    for prefix in ["/admin/nova-api/", "/nova-api/", "/nova-vendor/planning/"]:
        try:
            resp = session.get(f"{base_url.rstrip('/')}{prefix}{resource_name}", timeout=10)
            if resp.status_code == 200: return resp.json()
        except: continue
    return None

def normalize_tail(tail):
    if not tail: return "UNKNOWN"
    return str(tail).upper().replace("-", "").replace(" ", "")

def fetch_and_merge_data_v2(end_date):
    c_sess = get_authenticated_session("https://toran-camo.flightapp.be", "/admin/login", st.secrets["CAMO_EMAIL"], st.secrets["CAMO_PASS"])
    t_sess = get_authenticated_session("https://admin.toran.be", "/login", st.secrets["TORAN_EMAIL"], st.secrets["TORAN_PASS"])
    if not c_sess or not t_sess: return None, "Auth Failed", {}, pd.DataFrame(), pd.DataFrame()
    maint_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", "upcoming-aircraft-maintenances?perPage=100")
    if not maint_json: return None, "CAMO Data not found", {}, pd.DataFrame(), pd.DataFrame()
    ac_data = []
    for r in maint_json.get('resources', []):
        fields = {f['attribute']: f['value'] for f in r.get('fields', [])}
        reg_display = str(fields.get('aircraft') or "Unknown").split(' ')[0].strip().upper()
        reg_merge = normalize_tail(reg_display)
        try: curr_val = float(str(fields.get('current_hours_ttsn', 0)).replace(',', ''))
        except: curr_val = 0.0
        try: due_val = float(str(fields.get('max_hours', 0)).replace(',', ''))
        except: due_val = 0.0
        potential = max(0.0, due_val - curr_val) if due_val > 0 else 0.0
        maint_type_str = str(fields.get('aircraftMaintenanceType', "Standard Inspection"))
        try: interval = float(re.search(r'(\d+)', maint_type_str).group(1))
        except: interval = 100.0
        due_date = None
        raw_date = fields.get('max_valid_until')
        if raw_date and str(raw_date).strip() not in ["", "—", "None", "null"]:
            try: due_date = pd.to_datetime(str(raw_date)).date()
            except: pass
        ac_data.append({'Registration': reg_display, 'MergeKey': reg_merge, 'Current': curr_val, 'Limit': due_val, 'Type': maint_type_str, 'Interval': interval, 'Potential': potential, 'Due Date': due_date})
    df_ac = pd.DataFrame(ac_data).sort_values('Limit').drop_duplicates('MergeKey')
    
    xsrf = t_sess.cookies.get('XSRF-TOKEN')
    if xsrf: t_sess.headers.update({'X-XSRF-TOKEN': urllib.parse.unquote(xsrf), 'Referer': 'https://admin.toran.be/planning', 'Accept': 'application/json'})
    
    book_list, now = [], pd.Timestamp.utcnow().tz_localize(None)
    for i in range(2): 
        target = now + pd.Timedelta(weeks=i)
        try:
            week_data = t_sess.get(f"https://admin.toran.be/api/planning?week={target.isocalendar()[1]}&year={target.isocalendar()[0]}").json()
            id_map = {str(h['id']): h['title'].upper() for h in week_data.get('helis', [])}
            for f in week_data.get('entries', []):
                if f.get('status') == 'confirmed':
                    start = pd.to_datetime(f['reserved_start_datetime']).tz_convert(None)
                    end = pd.to_datetime(f['reserved_end_datetime']).tz_convert(None)
                    reg = id_map.get(str(f.get('heli_id', '')))
                    if reg: book_list.append({
                        'MergeKey': normalize_tail(reg), 'Registration': reg, 'Start': start, 'End': end, 
                        'Planned': (end - start).total_seconds() / 3600 * 0.85,
                        'Type': str(f.get('booking_type', 'Flight')).capitalize(),
                        'Details': f"{f.get('customer_first_name','')} {f.get('customer_last_name','')}".strip(),
                        'Departure': f.get('departure_airport_name', 'EBKT')
                    })
        except: pass
    df_books = pd.DataFrame(book_list)
    if not df_books.empty:
        df = pd.merge(df_ac, df_books.groupby('MergeKey')['Planned'].sum().reset_index(), on='MergeKey', how='left').fillna({'Planned': 0})
    else: df = df_ac.assign(Planned=0)
    df['Forecast'] = df['Potential'] - df['Planned']
    df['Life Now %'] = ((df['Potential'] / df['Interval']) * 100).clip(0, 100)
    return df, df_books, pd.DataFrame()

# --- UI Setup ---
if "mode" in st.query_params and st.query_params["mode"] == "tv": default_idx = 1
else: default_idx = 0

with st.sidebar:
    try: st.image("toran_logo.png", use_container_width=True)
    except: pass 
    app_mode = st.radio("🖥️ Mode", ["Maintenance Dashboard", "Guest Welcome Screen"], index=default_idx)
    if app_mode == "Guest Welcome Screen": st.query_params["mode"] = "tv"
    else: st.query_params.clear()
    selected_date = st.date_input("🗓️ Forecast End Date", value=datetime.today() + timedelta(days=35))
    if st.button('🔄 Refresh Data', use_container_width=True): st.cache_data.clear(); st.rerun()

df, raw_books_df, _ = fetch_and_merge_data_v2(selected_date)

if app_mode == "Maintenance Dashboard":
    st.title("Operations & Maintenance Forecast")
    if df is not None:
        st.dataframe(df[['Registration', 'Type', 'Current', 'Limit', 'Potential', 'Life Now %', 'Planned', 'Forecast', 'Due Date']], hide_index=True, use_container_width=True)

# ==========================================
# MODE 2: GUEST WELCOME SCREEN
# ==========================================
elif app_mode == "Guest Welcome Screen":
    try:
        with open("Asset 4@4x.jpg", "rb") as f: data = base64.b64encode(f.read()).decode()
        inline_logo = f'<a href="/?mode=admin" target="_self"><img src="data:image/jpeg;base64,{data}" style="height:70px; vertical-align:middle; margin-left:15px; border-radius:8px;"></a>'
    except: inline_logo = '🚁'

    st.markdown("""<meta http-equiv="refresh" content="60">
        <style>
        [data-testid="collapsedControl"], [data-testid="stSidebar"], header { display: none !important; }
        .stApp { margin-top: -95px !important; }
        .welcome-title { font-size: 78px; font-weight: 900; color: #000000; line-height: 1; margin-bottom:5px;}
        .welcome-subtitle { font-size: 34px; font-weight: 600; color: #666666; margin-bottom: 25px; }
        .clock-text { font-size: 45px; font-weight: 800; color: #E4D18C; text-align: right; }
        .info-card { background-color: #F8F8F8; border-left: 10px solid #E4D18C; padding: 20px; border-radius: 12px; box-shadow: 0 10px 15px rgba(0,0,0,0.05); margin-bottom:20px; }
        .weather-card { background-color: #000000; color: #FFFFFF; padding: 15px; border-radius: 12px; }
        .weather-val { font-size: 24px; font-weight: 800; color: #E4D18C; }
        .weather-lbl { font-size: 12px; color: #999999; text-transform: uppercase; font-weight:700; }
        .flight-board { width: 100%; border-collapse: collapse; font-size: 19px; }
        .flight-board th { background-color: #E4D18C; padding: 10px; text-align: left; font-weight: 800; border-radius: 4px 4px 0 0; }
        .flight-board td { padding: 10px; border-bottom: 1px solid #EEE; color: #666; font-weight: 600; }
        .active-row td { background-color: rgba(228, 209, 140, 0.2) !important; color: #000 !important; font-weight: 800; }
        </style>
    """, unsafe_allow_html=True)

    now_be = pd.Timestamp.now('Europe/Brussels')
    
    # MAIN SPLIT: Left (Welcome & Info) | Right (Clock, Logo, Departures)
    col_left, col_right = st.columns([1.8, 1])

    with col_right:
        st.markdown(f'<div class="clock-text">{now_be.strftime("%H:%M")} Local</div>', unsafe_allow_html=True)
        st.markdown(f'<div style="text-align:right; margin: 10px 0 20px 0;">{inline_logo}</div>', unsafe_allow_html=True)
        
        # Today's Departures Board (Narrow)
        if not raw_books_df.empty:
            raw_books_df['LStart'] = raw_books_df['Start'].dt.tz_localize('UTC').dt.tz_convert('Europe/Brussels')
            raw_books_df['LEnd'] = raw_books_df['End'].dt.tz_localize('UTC').dt.tz_convert('Europe/Brussels')
            today_flights = raw_books_df[(raw_books_df['LStart'].dt.date == now_be.date()) & (raw_books_df['Type'] != 'Blocking')].sort_values('LStart')
            
            # Find Active Flight
            active_flight = None
            for _, f in today_flights.iterrows():
                if f['LStart'] > now_be - pd.Timedelta(minutes=15):
                    active_flight = f; break

            st.markdown("<h3 style='font-size:24px; font-weight:800; border-bottom:3px solid #E4D18C; display:inline-block; margin-bottom:10px;'>TODAY'S DEPARTURES</h3>", unsafe_allow_html=True)
            tbl = '<table class="flight-board"><tr><th>Time</th><th>Tail</th><th>Guest</th></tr>'
            for _, f in today_flights.iterrows():
                cls = 'class="active-row"' if active_flight is not None and active_flight.equals(f) else ''
                tbl += f'<tr {cls}><td>{f["LStart"].strftime("%H:%M")}</td><td>{f["Registration"]}</td><td>{f["Details"]}</td></tr>'
            st.markdown(tbl + '</table>', unsafe_allow_html=True)

    with col_left:
        # Welcome Headline
        if active_flight is not None:
            guest = str(active_flight['Details']) if str(active_flight['Details']).strip() else "Guest"
            st.markdown(f'<div class="welcome-title">Welcome, {guest}!</div>', unsafe_allow_html=True)
            st.markdown('<div class="welcome-subtitle">Prepped and ready for departure</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="welcome-title">Welcome to Toran</div>', unsafe_allow_html=True)
            st.markdown('<div class="welcome-subtitle">Aviation Excellence in Kortrijk (EBKT)</div>', unsafe_allow_html=True)

        # Info and Weather Stacked
        if active_flight is not None:
            st.markdown(f"""<div class="info-card"><h3 style="margin:0;">🚁 Flight Info</h3>
                <p style="font-size:24px; margin:5px 0 0 0;"><b>Departs:</b> {active_flight["LStart"].strftime("%H:%M")} Local</p>
                <p style="font-size:24px; margin:0;"><b>Airport:</b> {active_flight.get("Departure", "EBKT")}</p>
                <p style="font-size:24px; margin:0;"><b>Aircraft:</b> {active_flight["Registration"]}</p></div>""", unsafe_allow_html=True)
        
        # Pilot Weather Card
        st.markdown(f"""<div class="weather-card">
            <div style="font-size:14px; color:#E4D18C; font-weight:800; margin-bottom:10px;">EBKT PILOT WEATHER</div>
            <div style="display:grid; grid-template-columns: 1fr 1fr 1fr; gap:15px;">
                <div><div class="weather-lbl">Temp</div><div class="weather-val">{weather['temp']}</div></div>
                <div><div class="weather-lbl">Wind</div><div class="weather-val">{weather['wind_spd']}</div></div>
                <div><div class="weather-lbl">Dir</div><div class="weather-val">{weather['wind_deg']}</div></div>
                <div><div class="weather-lbl">Clouds</div><div class="weather-val" style="font-size:18px;">{weather['clouds']}</div></div>
                <div><div class="weather-lbl">Vis</div><div class="weather-val">{weather['vis']}</div></div>
                <div><div class="weather-lbl">QNH</div><div class="weather-val">{weather['qnh']}</div></div>
            </div></div>""", unsafe_allow_html=True)

        # Picture Below Info
        st.markdown("<br>", unsafe_allow_html=True)
        tail_c = normalize_tail(active_flight['Registration']) if active_flight is not None else "OOHXP"
        img = AIRCRAFT_DB.get(tail_c, {}).get('image', 'raven2.jpg')
        try: st.image(img, use_container_width=True)
        except: pass
