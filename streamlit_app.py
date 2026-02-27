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
        url = "https://api.open-meteo.com/v1/forecast?latitude=50.8172&longitude=3.2047&current=temperature_2m,wind_speed_10m,wind_direction_10m&wind_speed_unit=kn"
        data = requests.get(url, timeout=5).json()['current']
        return {
            "temp": f"{data['temperature_2m']}°C",
            "wind": f"{data['wind_speed_10m']} kts",
            "dir": f"{data['wind_direction_10m']}°"
        }
    except:
        return {"temp": "--°C", "wind": "-- kts", "dir": "--°"}

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

@st.cache_data
def convert_df_to_csv(df):
    return df.to_csv(index=False).encode('utf-8')

# --- Core Logic ---
@st.cache_data(ttl=300)
def fetch_and_merge_data_v2(end_date):
    c_sess = get_authenticated_session("https://toran-camo.flightapp.be", "/admin/login", st.secrets["CAMO_EMAIL"], st.secrets["CAMO_PASS"])
    t_sess = get_authenticated_session("https://admin.toran.be", "/login", st.secrets["TORAN_EMAIL"], st.secrets["TORAN_PASS"])

    if not c_sess or not t_sess: return None, "Auth Failed", {}, pd.DataFrame(), pd.DataFrame()

    maint_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", "upcoming-aircraft-maintenances?perPage=100")
    if not maint_json: return None, "CAMO Data not found", {}, pd.DataFrame(), pd.DataFrame()

    ac_data, aircraft_registry = [], {}
    for r in maint_json.get('resources', []):
        fields = {f['attribute']: f['value'] for f in r.get('fields', [])}
        reg_display = str(fields.get('aircraft') or "Unknown").split(' ')[0].strip().upper()
        reg_merge = normalize_tail(reg_display)

        ac_id = next((f.get('belongsToId') for f in r.get('fields', []) if f.get('attribute') == 'aircraft'), None)
        if ac_id and reg_merge != "UNKNOWN": aircraft_registry[reg_merge] = ac_id

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

        ac_data.append({
            'Registration': reg_display, 'MergeKey': reg_merge, 'Current': curr_val, 
            'Limit': due_val, 'Type': maint_type_str, 'Interval': interval, 'Potential': potential, 'Due Date': due_date
        })
    df_ac = pd.DataFrame(ac_data).sort_values('Limit').drop_duplicates('MergeKey')

    # --- DEFECTS ---
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
            
            desc_clean = "No description provided."
            due_clean = None
            if def_id:
                det_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", f"{endpoint}/{def_id}")
                if det_json and 'resource' in det_json:
                    det_f = {f['attribute']: f['value'] for f in det_json['resource'].get('fields', [])}
                    for k in ['description', 'defect', 'remarks', 'finding', 'klacht']:
                        if det_f.get(k):
                            desc_clean = re.sub(r'<[^>]+>', '', str(det_f.get(k))).strip()
                            break
                    for dk in ['ultimate_repair_date', 'due_date', 'limit']:
                        if det_f.get(dk):
                            try: due_clean = pd.to_datetime(str(det_f[dk])).date()
                            except: pass

            defects_list.append({
                'MergeKey': reg_merge, 'ID': str(r.get('title') or def_id), 'Type': endpoint.split('-')[0].upper(),
                'Status': status.capitalize(), 'Description': desc_clean, 'Due Date': due_clean
            })
    df_defects = pd.DataFrame(defects_list)

    # --- BOOKINGS ---
    xsrf = t_sess.cookies.get('XSRF-TOKEN')
    if xsrf: t_sess.headers.update({'X-XSRF-TOKEN': urllib.parse.unquote(xsrf), 'Referer': 'https://admin.toran.be/planning', 'Accept': 'application/json'})

    pilot_map = {}
    try:
        p_resp = t_sess.get("https://admin.toran.be/api/pilots?page_size=100", timeout=10).json()
        for p in p_resp.get('data', []): pilot_map[str(p.get('id'))] = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    except: pass

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
                    dur = (end - start).total_seconds() / 3600 * 0.85
                    reg = id_map.get(str(f.get('heli_id', '')))
                    if reg:
                        book_list.append({
                            'MergeKey': normalize_tail(reg), 'Registration': reg, 'Start': start, 'End': end, 'Planned': dur,
                            'Type': str(f.get('booking_type', 'Flight')).capitalize(),
                            'Details': f"{f.get('customer_first_name','')} {f.get('customer_last_name','')}".strip(),
                            'Instructor': pilot_map.get(str(f.get('instructor_id')), 'Toran Team')
                        })
            for b in week_data.get('blockings', []):
                start = pd.to_datetime(b['start_datetime']).tz_convert(None)
                if b.get('helis'):
                    for h in b['helis']:
                        reg = id_map.get(str(h.get('id', '')))
                        if reg: book_list.append({'MergeKey': normalize_tail(reg), 'Registration': reg, 'Start': start, 'End': pd.to_datetime(b['end_datetime']).tz_convert(None), 'Planned': 0, 'Type': 'Blocking', 'Details': b.get('description',''), 'Instructor': '-'})
        except: pass

    df_books = pd.DataFrame(book_list)
    if not df_books.empty:
        df_books = df_books.sort_values(by=['MergeKey', 'Start'])
        df_books['Cumulative'] = df_books.groupby('MergeKey')['Planned'].cumsum()
        df = pd.merge(df_ac, df_books.groupby('MergeKey')['Planned'].sum().reset_index(), on='MergeKey', how='left').fillna({'Planned': 0})
    else:
        df = df_ac.assign(Planned=0)

    df['Forecast'] = df['Potential'] - df['Planned']
    df['Life Now %'] = ((df['Potential'] / df['Interval']) * 100).clip(0, 100)
    df['Life Forecast %'] = ((df['Forecast'] / df['Interval']) * 100).clip(0, 100)
    
    return df, df_books, df_defects

# --- UI Mode Setup ---
if "mode" in st.query_params and st.query_params["mode"] == "tv":
    default_idx = 1
else:
    default_idx = 0

with st.sidebar:
    try: st.image("toran_logo.png", use_container_width=True)
    except: pass 
    app_mode = st.radio("🖥️ Mode", ["Maintenance Dashboard", "Guest Welcome Screen"], index=default_idx)
    if app_mode == "Guest Welcome Screen": st.query_params["mode"] = "tv"
    else: st.query_params.clear()
    selected_date = st.date_input("🗓️ Forecast End Date", value=datetime.today() + timedelta(days=35))
    if st.button('🔄 Refresh Data', use_container_width=True): 
        st.cache_data.clear()
        st.rerun()

df, raw_books_df, df_defects = fetch_and_merge_data_v2(selected_date)

if app_mode == "Maintenance Dashboard":
    st.title("Operations & Maintenance Forecast")
    if df is not None:
        today = pd.Timestamp.now().normalize()
        tab_names = ["Fleet Overview"] + sorted(df['Registration'].unique().tolist())
        tabs = st.tabs(tab_names)
        with tabs[0]:
            styled_df = df[['Registration', 'Type', 'Current', 'Limit', 'Potential', 'Life Now %', 'Planned', 'Forecast', 'Due Date']].copy()
            st.dataframe(styled_df, hide_index=True, use_container_width=True)
        for i, tail in enumerate(tab_names[1:], start=1):
            with tabs[i]:
                ac_df = df[df['Registration'] == tail].iloc[0]
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("TSN", f"{ac_df['Current']:.1f}h")
                c2.metric("Potential", f"{ac_df['Potential']:.1f}h")
                c3.metric("Booked", f"{ac_df['Planned']:.1f}h")
                c4.metric("Forecast", f"{ac_df['Forecast']:.1f}h")
                
                st.subheader("🛠️ Open Defects")
                ac_def = df_defects[df_defects['MergeKey'] == normalize_tail(tail)]
                st.dataframe(ac_def[['ID', 'Type', 'Status', 'Due Date', 'Description']], hide_index=True, use_container_width=True)
                
                st.subheader("📋 Schedule")
                detail_df = raw_books_df[raw_books_df['MergeKey'] == normalize_tail(tail)]
                st.dataframe(detail_df[['Start', 'Type', 'Details', 'Instructor', 'Planned']], hide_index=True, use_container_width=True)

# ==========================================
# MODE 2: GUEST WELCOME SCREEN
# ==========================================
elif app_mode == "Guest Welcome Screen":
    st.markdown("""
        <meta http-equiv="refresh" content="600">
        <style>
        [data-testid="collapsedControl"], [data-testid="stSidebar"], header { display: none !important; }
        .welcome-title { font-size: 80px; font-weight: 900; color: #000000; margin-bottom: 5px; }
        .welcome-subtitle { font-size: 35px; font-weight: 600; color: #666666; margin-bottom: 40px; }
        .clock-text { font-size: 55px; font-weight: 800; color: #E4D18C; text-align: right; }
        .info-card { background-color: #F8F8F8; border-left: 10px solid #E4D18C; padding: 30px; border-radius: 12px; box-shadow: 0 10px 15px rgba(0,0,0,0.1); }
        .info-card h3 { font-size: 40px; margin: 0; }
        .weather-card { background-color: #000000; color: #FFFFFF; padding: 30px; border-radius: 12px; text-align: center; }
        .weather-temp { font-size: 65px; font-weight: 800; color: #E4D18C; }
        .flight-board { width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 24px; }
        .flight-board th { background-color: #E4D18C; padding: 15px; text-align: left; }
        .flight-board td { padding: 15px; border-bottom: 1px solid #EEE; color: #666; }
        .active-row td { background-color: rgba(228, 209, 140, 0.15) !important; color: #000 !important; font-weight: 800; }
        </style>
        
        <script>
        function updateClock() {
            const now = new Date();
            const timeStr = now.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
            document.getElementById('live-clock').innerText = timeStr + ' Local';
        }
        setInterval(updateClock, 1000);
        </script>
    """, unsafe_allow_html=True)

    now_be = pd.Timestamp.now('Europe/Brussels')
    
    col_logo, col_clock = st.columns([1, 1])
    with col_logo:
        try:
            with open("toran_logo.png", "rb") as f:
                data = base64.b64encode(f.read()).decode()
            st.markdown(f'<a href="/?mode=admin" target="_self"><img src="data:image/png;base64,{data}" width="350"></a>', unsafe_allow_html=True)
        except: st.markdown('<a href="/?mode=admin" target="_self" style="text-decoration:none;"><h1 style="color:#000;">TORAN</h1></a>', unsafe_allow_html=True)
    with col_clock:
        st.markdown(f'<div id="live-clock" class="clock-text">{now_be.strftime("%H:%M:%S")} Local</div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    today_flights = pd.DataFrame()
    active_flight = None
    if not raw_books_df.empty:
        raw_books_df['LStart'] = raw_books_df['Start'].dt.tz_localize('UTC').dt.tz_convert('Europe/Brussels')
        raw_books_df['LEnd'] = raw_books_df['End'].dt.tz_localize('UTC').dt.tz_convert('Europe/Brussels')
        today_flights = raw_books_df[(raw_books_df['LStart'].dt.date == now_be.date()) & (raw_books_df['Type'] != 'Blocking')].sort_values('LStart')
        for _, f in today_flights.iterrows():
            if (f['LEnd'] - now_be).total_seconds() >= -1800:
                active_flight = f
                break

    if active_flight is not None:
        guest = str(active_flight['Details']) if str(active_flight['Details']).strip() else "Guest"
        st.markdown(f'<div class="welcome-title">Welcome, {guest}!</div>', unsafe_allow_html=True)
        st.markdown('<div class="welcome-subtitle">We are ready for your departure</div>', unsafe_allow_html=True)
        
        c_info, c_img = st.columns([1.2, 1])
        with c_info:
            st.markdown(f"""
                <div class="info-card">
                    <h3>🚁 Flight Details</h3><br>
                    <p style="font-size:28px;"><b>Scheduled:</b> {active_flight['LStart'].strftime('%H:%M')} Local</p>
                    <p style="font-size:28px;"><b>Aircraft:</b> {active_flight['Registration']}</p>
                </div>
            """, unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown(f"""
                <div class="weather-card">
                    <div style="font-size:20px; color:#E4D18C; font-weight:800;">EBKT KORTRIJK WEATHER</div>
                    <div class="weather-temp">{weather['temp']}</div>
                    <div style="font-size:24px;">Wind: {weather['wind']} at {weather['dir']}</div>
                </div>
            """, unsafe_allow_html=True)
        with c_img:
            tail_clean = normalize_tail(active_flight['Registration'])
            img_file = AIRCRAFT_DB.get(tail_clean, {}).get('image', 'raven2.jpg')
            try: st.image(img_file, use_container_width=True)
            except: pass
    else:
        st.markdown('<div class="welcome-title">Welcome to Toran Helicopters</div>', unsafe_allow_html=True)
        st.markdown('<div class="welcome-subtitle">Aviation Excellence in Kortrijk</div>', unsafe_allow_html=True)

    if not today_flights.empty:
        st.markdown("<br><h2 style='border-bottom:4px solid #E4D18C; display:inline-block;'>TODAY'S DEPARTURES</h2>", unsafe_allow_html=True)
        tbl = '<table class="flight-board"><tr><th>Time</th><th>Aircraft</th><th>Pilot / Student</th></tr>'
        for _, f in today_flights.iterrows():
            cls = 'class="active-row"' if active_flight is not None and active_flight.equals(f) else ''
            tbl += f'<tr {cls}><td>{f["LStart"].strftime("%H:%M")}</td><td>{f["Registration"]}</td><td>{f["Details"]}</td></tr>'
        st.markdown(tbl + '</table>', unsafe_allow_html=True)
