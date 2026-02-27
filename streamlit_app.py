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
        .stTabs [data-baseweb="tab-list"] { gap: 8px; background-color: transparent; }
        .stTabs [data-baseweb="tab"] { background-color: #FFFFFF; border-radius: 4px !important; padding: 10px 20px !important; border: 1px solid #999999; color: #666666; font-weight: 600; transition: all 0.2s ease; }
        .stTabs [aria-selected="true"] { background-color: #E4D18C !important; color: #000000 !important; border: 1px solid #E4D18C !important; }
        [data-testid="metric-container"] { background-color: #FFFFFF; border: 1px solid #999999; border-radius: 8px; padding: 20px; border-left: 5px solid #E4D18C; }
        [data-testid="stMetricValue"] { font-size: 34px !important; font-weight: 800 !important; color: #000000 !important; }
        [data-testid="stDataFrame"] { background-color: #FFFFFF; border-radius: 8px; border: 1px solid #999999; }
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
    
    defects_list = []
    for endpoint in ['ddl-defects', 'hil-defects']:
        d_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", f"{endpoint}?perPage=50")
        if not d_json or 'resources' not in d_json: continue
        for r in d_json['resources']:
            idx_f = {f['attribute']: f['value'] for f in r.get('fields', [])}
            status = str(idx_f.get('status', 'Open')).strip().lower()
            if status in ['closed', 'gesloten', 'resolved', 'done']: continue
            reg_raw = str(idx_f.get('aircraft') or "")
            reg_merge = normalize_tail(reg_raw.split(' ')[0])
            def_id = r.get('id', {}).get('value') if isinstance(r.get('id'), dict) else r.get('id')
            desc_clean, due_clean = "No description provided.", None
            if def_id:
                det_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", f"{endpoint}/{def_id}")
                if det_json and 'resource' in det_json:
                    det_f = {f['attribute']: f['value'] for f in det_json['resource'].get('fields', [])}
                    for k in ['description', 'defect', 'remarks', 'finding']:
                        if det_f.get(k): desc_clean = re.sub(r'<[^>]+>', '', str(det_f.get(k))).strip(); break
                    for dk in ['ultimate_repair_date', 'due_date', 'limit']:
                        if det_f.get(dk):
                            try: due_clean = pd.to_datetime(str(det_f[dk])).date(); break
                            except: pass
            defects_list.append({'MergeKey': reg_merge, 'ID': str(r.get('title') or def_id), 'Type': endpoint.split('-')[0].upper(), 'Status': status.capitalize(), 'Description': desc_clean, 'Due Date': due_clean})
    df_defects = pd.DataFrame(defects_list)
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
    return df, df_books, df_defects

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

df, raw_books_df, df_defects = fetch_and_merge_data_v2(selected_date)

# ==========================================
# MODE 1: MAINTENANCE DASHBOARD (RESTORED)
# ==========================================
if app_mode == "Maintenance Dashboard":
    st.title("Operations & Maintenance Forecast")
    if df is not None:
        tab_names = ["Fleet Overview"] + sorted(df['Registration'].unique().tolist())
        tabs = st.tabs(tab_names)
        
        with tabs[0]:
            st.subheader("Fleet Summary")
            st.dataframe(df[['Registration', 'Type', 'Current', 'Limit', 'Potential', 'Life Now %', 'Planned', 'Forecast', 'Due Date']], hide_index=True, use_container_width=True)
            
        for i, tail in enumerate(tab_names[1:], start=1):
            with tabs[i]:
                ac_df = df[df['Registration'] == tail].iloc[0]
                
                # Metrics Row
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Current TSN", f"{ac_df['Current']:.1f}h")
                c2.metric("Life Potential", f"{ac_df['Potential']:.1f}h")
                c3.metric("Booked Flights", f"{ac_df['Planned']:.1f}h")
                c4.metric("Forecasted End", f"{ac_df['Forecast']:.1f}h")
                
                # Defects Section
                st.markdown("---")
                st.subheader("🛠️ Open Defects (HIL / DDL)")
                if not df_defects.empty and 'MergeKey' in df_defects.columns:
                    ac_def = df_defects[df_defects['MergeKey'] == normalize_tail(tail)]
                    if not ac_def.empty:
                        st.dataframe(ac_def[['ID', 'Type', 'Status', 'Due Date', 'Description']], hide_index=True, use_container_width=True)
                    else: st.info("✅ No open defects for this aircraft.")
                else: st.info("✅ No open defects.")
                
                # Schedule Section
                st.markdown("---")
                st.subheader("📋 Scheduled Log")
                if not raw_books_df.empty:
                    ac_books = raw_books_df[raw_books_df['MergeKey'] == normalize_tail(tail)]
                    if not ac_books.empty:
                        st.dataframe(ac_books[['Start', 'Type', 'Details', 'Departure', 'Planned']], hide_index=True, use_container_width=True)
                    else: st.info("No bookings found for this period.")

# ==========================================
# MODE 2: GUEST WELCOME SCREEN
# ==========================================
elif app_mode == "Guest Welcome Screen":
    try:
        with open("Asset 4@4x.jpg", "rb") as f: data = base64.b64encode(f.read()).decode()
        inline_logo = f'<a href="/?mode=admin" target="_self"><img src="data:image/jpeg;base64,{data}" style="height:70px; vertical-align:middle; margin-left:15px; border-radius:8px; cursor: pointer;"></a>'
    except: inline_logo = '<a href="/?mode=admin" target="_self" style="text-decoration:none;">🚁</a>'

    st.markdown("""<meta http-equiv="refresh" content="60">
        <style>
        [data-testid="collapsedControl"], [data-testid="stSidebar"], header { display: none !important; }
        .stApp { margin-top: -100px !important; }
        .welcome-title { font-size: 78px; font-weight: 900; color: #000; line-height: 1; margin-bottom: 5px; }
        .welcome-subtitle { font-size: 34px; font-weight: 600; color: #666; margin-bottom: 25px; }
        .clock-text { font-size: 50px; font-weight: 800; color: #E4D18C; text-align: right; }
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
            const timeStr = now.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ' Local';
            const clockEl = document.getElementById('live-clock');
            if (clockEl) clockEl.innerText = timeStr;
        }
        setInterval(updateClock, 1000);
        </script>
    """, unsafe_allow_html=True)

    now_be = pd.Timestamp.now('Europe/Brussels')
    col_left, col_right = st.columns([1.8, 1])

    with col_right:
        st.markdown(f'<div id="live-clock" class="clock-text">{now_be.strftime("%H:%M:%S")} Local</div>', unsafe_allow_html=True)
        st.markdown(f'<div style="text-align:right; margin: 10px 0 20px 0;">{inline_logo}</div>', unsafe_allow_html=True)
        if not raw_books_df.empty:
            raw_books_df['LStart'] = raw_books_df['Start'].dt.tz_localize('UTC').dt.tz_convert('Europe/Brussels')
            today_flights = raw_books_df[(raw_books_df['LStart'].dt.date == now_be.date()) & (raw_books_df['Type'] != 'Blocking')].sort_values('LStart')
            active_flight = None
            for _, f in today_flights.iterrows():
                if f['LStart'] > now_be - pd.Timedelta(minutes=15): active_flight = f; break
            st.markdown("<h3 style='font-size:24px; font-weight:800; border-bottom:3px solid #E4D18C; display:inline-block; margin-bottom:10px;'>TODAY'S DEPARTURES</h3>", unsafe_allow_html=True)
            tbl = '<table class="flight-board"><tr><th>Time</th><th>Tail</th><th>Guest</th></tr>'
            for _, f in today_flights.iterrows():
                cls = 'class="active-row"' if active_flight is not None and active_flight.equals(f) else ''
                tbl += f'<tr {cls}><td>{f["LStart"].strftime("%H:%M")}</td><td>{f["Registration"]}</td><td>{f["Details"]}</td></tr>'
            st.markdown(tbl + '</table>', unsafe_allow_html=True)

    with col_left:
        if active_flight is not None:
            guest = str(active_flight['Details']) if str(active_flight['Details']).strip() else "Guest"
            st.markdown(f'<div class="welcome-title">Welcome, {guest}!</div>', unsafe_allow_html=True)
            st.markdown('<div class="welcome-subtitle">Prepped and ready for departure</div>', unsafe_allow_html=True)
            st.markdown(f"""<div class="info-card"><h3 style="margin:0;">🚁 Flight Details</h3><br>
                <p style="font-size:24px; margin:0;"><b>Departs:</b> {active_flight["LStart"].strftime("%H:%M")} Local</p>
                <p style="font-size:24px; margin:0;"><b>Airport:</b> {active_flight.get("Departure", "EBKT")}</p>
                <p style="font-size:24px; margin:0;"><b>Tail:</b> {active_flight["Registration"]}</p></div>""", unsafe_allow_html=True)
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
        tail_c = normalize_tail(active_flight['Registration']) if active_flight is not None else "OOHXP"
        img = AIRCRAFT_DB.get(tail_c, {}).get('image', 'raven2.jpg')
        try: st.image(img, use_container_width=True)
        except: pass
