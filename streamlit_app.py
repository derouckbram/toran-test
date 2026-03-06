import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import urllib.parse
import re  
from datetime import datetime, timedelta
import base64
import calendar

# --- Page Config ---
st.set_page_config(page_title="Toran Operations Center", layout="wide", page_icon="🚁")

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
    except:
        return {"temp": "--", "wind_spd": "--", "wind_deg": "--", "clouds": "--", "vis": "--", "qnh": "--"}

weather = get_ebkt_weather()

# --- Aircraft Image & Cardex Database ---
AIRCRAFT_DB = {
    "OOHXP": {"model": "Robinson R44 Raven II", "image": "raven2.jpg", "seats": "4 Seats", "cruise": "109 kts", "overhaul_install": datetime(2018, 1, 26).date(), "overhaul_limit_h": 2200},
    "OOMOO": {"model": "Robinson R44 Raven I", "image": "raven1.jpg", "seats": "4 Seats", "cruise": "109 kts", "overhaul_install": datetime(2019, 10, 2).date(), "overhaul_limit_h": 2200},
    "OOTOA": {"model": "Robinson R44 Raven II", "image": "raven2.jpg", "seats": "4 Seats", "cruise": "109 kts", "overhaul_install": datetime(2013, 12, 3).date(), "overhaul_limit_h": 2200},
    "OOSKH": {"model": "Guimbal Cabri G2", "image": "cabri.jpg", "seats": "2 Seats", "cruise": "90 kts", "overhaul_install": None, "overhaul_limit_h": 2200}
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
        
        .toran-progress-container { width: 100%; background-color: #f0f0f0; border-radius: 4px; height: 20px; display: flex; overflow: hidden; margin-bottom: 5px; border: 1px solid #e0e0e0; }
        .toran-bar-normal { background-color: #E4D18C; height: 100%; transition: width 0.5s ease-in-out; }
        .toran-bar-tol { background-color: #FF8C00; height: 100%; transition: width 0.5s ease-in-out; }
        .oh-bar-container { width: 100%; background-color: #e0e0e0; border-radius: 4px; height: 12px; overflow: hidden; margin-top: 2px; }
        .oh-bar-fill { background-color: #666666; height: 100%; }
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
        session.post(login_url, data={"_token": csrf, "email": email, "password": password, "remember": "on"}, timeout=15)
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

def render_progress_bar(normal_rem, tol_rem, interval):
    if interval <= 0: interval = 100 
    pct_normal = max(0.0, min(100.0, (normal_rem / interval) * 100))
    pct_tol = max(0.0, min(100.0, (tol_rem / interval) * 100))
    st.markdown(f"""<div class="toran-progress-container" title="Normal: {normal_rem:.1f} | Tolerance: {tol_rem:.1f}"><div class="toran-bar-normal" style="width: {pct_normal}%;"></div><div class="toran-bar-tol" style="width: {pct_tol}%;"></div></div>""", unsafe_allow_html=True)

def render_overhaul_bar(current, limit, label):
    if limit <= 0: return
    pct = min(100.0, (current / limit) * 100)
    rem_pct = 100.0 - pct
    st.markdown(f"""<div style="font-size: 12px; margin-top: 8px; color: #666;">{label}: {current:.1f} / {limit:.0f} ({rem_pct:.1f}% left)</div><div class="oh-bar-container"><div class="oh-bar-fill" style="width: {pct}%;"></div></div>""", unsafe_allow_html=True)

# --- VISUAL DOWNLOADER LOGIC ---
def get_historical_rates_interactive():
    if 'seasonal_rates' in st.session_state:
        return st.session_state['seasonal_rates'], st.session_state['global_rates']

    seasonal_rates = {}
    global_rates = {}
    
    with st.status("📥 Downloading Flight History...", expanded=True) as status:
        st.write("Authenticating...")
        f_sess = get_authenticated_session("https://toran.flightapp.be", "/admin/login", st.secrets["CAMO_EMAIL"], st.secrets["CAMO_PASS"])
        
        if not f_sess:
            status.update(label="Authentication Failed", state="error")
            return {}, {}

        raw_hist = []
        page = 1
        prog_bar = st.progress(0)
        
        while True:
            st.write(f"Fetching page {page} (100 flights/page)...")
            try:
                h_flights = fetch_resource(f_sess, "https://toran.flightapp.be", f"flights?perPage=100&page={page}")
                if not h_flights or 'resources' not in h_flights or not h_flights['resources']: break
                for r in h_flights['resources']:
                    fields = {f['attribute']: f['value'] for f in r.get('fields', [])}
                    reg = str(fields.get('aircraft') or "Unk").split(' ')[0]
                    duration = 0.0
                    raw_dur = fields.get('flight_time') or fields.get('total_time') or fields.get('block_time')
                    if raw_dur:
                        if isinstance(raw_dur, str) and ':' in raw_dur:
                            h, m = map(int, raw_dur.split(':'))
                            duration = h + (m/60)
                        else:
                            try: duration = float(raw_dur) / 60
                            except: pass
                    f_date = None
                    if fields.get('date'): 
                        try: f_date = pd.to_datetime(fields.get('date')).date()
                        except: pass
                    if f_date and duration > 0:
                        raw_hist.append({'Reg': normalize_tail(reg), 'Date': f_date, 'Month': f_date.month, 'Hours': duration})
                
                prog_bar.progress(min(page / 50, 1.0))
                page += 1
                if page > 50: break 
            except Exception as e:
                st.write(f"Error: {e}")
                break
        
        st.write("Processing statistics...")
        if raw_hist:
            df_hist = pd.DataFrame(raw_hist)
            for reg, group in df_hist.groupby('Reg'):
                min_date = group['Date'].min()
                max_date = group['Date'].max()
                days = (max_date - min_date).days
                if days < 1: days = 1
                global_rates[reg] = group['Hours'].sum() / days
                seasonal_rates[reg] = {}
                for month_idx, month_group in group.groupby('Month'):
                    total_h = month_group['Hours'].sum()
                    unique_periods = month_group['Date'].apply(lambda x: x.strftime('%Y-%m')).nunique()
                    if unique_periods < 1: unique_periods = 1
                    denom = unique_periods * 30.4
                    seasonal_rates[reg][month_idx] = total_h / denom
        
        status.update(label="✅ History Download Complete", state="complete", expanded=False)
        st.session_state['seasonal_rates'] = seasonal_rates
        st.session_state['global_rates'] = global_rates
        
    return seasonal_rates, global_rates

# --- MASTER LOGIC ---
@st.cache_data(ttl=300)
def fetch_and_merge_data_v12(end_date, seasonal_rates, global_rates):
    c_sess = get_authenticated_session("https://toran-camo.flightapp.be", "/admin/login", st.secrets["CAMO_EMAIL"], st.secrets["CAMO_PASS"])
    t_sess = get_authenticated_session("https://admin.toran.be", "/login", st.secrets["TORAN_EMAIL"], st.secrets["TORAN_PASS"])

    if not c_sess or not t_sess: return None, "Auth Failed", {}, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), 0

    # 1. UPCOMING MAINTENANCE
    maint_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", "upcoming-aircraft-maintenances?perPage=100")
    ac_data = []
    
    if maint_json:
        for r in maint_json.get('resources', []):
            fields = {f['attribute']: f['value'] for f in r.get('fields', [])}
            reg_display = str(fields.get('aircraft') or "Unknown").split(' ')[0].strip().upper()
            reg_merge = normalize_tail(reg_display)
            ac_id_internal = None
            for f_raw in r.get('fields', []):
                if f_raw.get('attribute') == 'aircraft':
                    ac_id_internal = f_raw.get('belongsToId'); break

            try: curr_val = float(str(fields.get('current_hours_ttsn', 0)).replace(',', ''))
            except: curr_val = 0.0
            try: limit_val = float(str(fields.get('max_hours', 0)).replace(',', ''))
            except: limit_val = 0.0
            try: exceedance = float(str(fields.get('max_hours_exceedence', 0)).replace(',', ''))
            except: exceedance = 0.0
            
            base_potential = limit_val - curr_val
            final_potential = max(0.0, base_potential + exceedance)

            maint_type_str = str(fields.get('aircraftMaintenanceType', "Standard"))
            try: interval = float(re.search(r'(\d+)', maint_type_str).group(1))
            except: interval = 100.0
            if interval <= 0: interval = 100.0 

            try: cal_exc_days = float(str(fields.get('max_valid_until_exceedence', 0)).replace(',', ''))
            except: cal_exc_days = 0.0

            due_date = None
            raw_date = fields.get('max_valid_until')
            if raw_date and str(raw_date).strip() not in ["", "—", "None", "null"]:
                try: 
                    d_obj = pd.to_datetime(str(raw_date))
                    if cal_exc_days > 0: d_obj = d_obj + timedelta(days=int(cal_exc_days))
                    due_date = d_obj.date()
                except: pass

            ac_data.append({
                'Registration': reg_display, 'MergeKey': reg_merge, 'Current': curr_val, 'Limit': limit_val, 'Type': maint_type_str, 
                'Interval': interval, 'Potential': final_potential, 'Due Date': due_date, 'AircraftID': ac_id_internal, 'Exceedance': exceedance, 'CalExceedance': cal_exc_days
            })
    df_ac = pd.DataFrame(ac_data).sort_values('Limit').drop_duplicates('MergeKey')

    # 2. LAST PERFORMED
    hist_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", "aircraft-maintenance-histories?perPage=100")
    if hist_json:
        hist_list = []
        for r in hist_json.get('resources', []):
            fields = {f['attribute']: f['value'] for f in r.get('fields', [])}
            reg_merge = normalize_tail(str(fields.get('aircraft') or "").split(' ')[0])
            date_val = None
            for k in ['date', 'completion_date', 'performed_at']:
                if fields.get(k): 
                    try: date_val = pd.to_datetime(fields.get(k)).date(); break
                    except: pass
            hist_hours = None
            for k in ['ttsn', 'hours', 'aircraft_hours', 'total_time', 'tacho', 'current_hours', 'aircraft_ttsn']:
                if fields.get(k): 
                    try: 
                        val = float(str(fields.get(k)).replace(',', ''))
                        if 100 < val < 20000: hist_hours = val; break
                    except: pass
            if hist_hours is None:
                for k, v in fields.items():
                    if isinstance(v, (str, int, float)):
                        try:
                            val = float(str(v).replace(',', ''))
                            if 500 < val < 15000: hist_hours = val; break
                        except: pass
            m_type = str(fields.get('type') or fields.get('name') or "Maintenance")
            if reg_merge != "UNKNOWN" and date_val:
                hist_list.append({'MergeKey': reg_merge, 'LastDate': date_val, 'LastType': m_type, 'LastHours': hist_hours})
        if hist_list:
            df_hist = pd.DataFrame(hist_list).sort_values('LastDate', ascending=False).drop_duplicates('MergeKey')
            df_ac = pd.merge(df_ac, df_hist, on='MergeKey', how='left')

    # 3. DEFECTS
    defects_list = []
    for endpoint in ['ddl-defects', 'hil-defects']:
        d_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", f"{endpoint}?perPage=50")
        if not d_json or 'resources' not in d_json: continue
        for r in d_json.get('resources', []):
            idx_f = {f['attribute']: f['value'] for f in r.get('fields', [])}
            if str(idx_f.get('status', '')).lower() in ['closed', 'gesloten', 'done']: continue
            def_id = r.get('id', {}).get('value') if isinstance(r.get('id'), dict) else r.get('id')
            reg_merge = normalize_tail(str(idx_f.get('aircraft') or "").split(' ')[0])
            desc, d_due = "No description provided.", None
            if def_id:
                det = fetch_resource(c_sess, "https://toran-camo.flightapp.be", f"{endpoint}/{def_id}")
                if det and 'resource' in det:
                    det_f = {f['attribute']: f['value'] for f in det['resource'].get('fields', [])}
                    for k in ['description', 'defect', 'remarks', 'finding']:
                        if det_f.get(k): desc = re.sub(r'<[^>]+>', '', str(det_f.get(k))).strip(); break
                    for dk in ['due_date', 'ultimate_repair_date']:
                        if det_f.get(dk): 
                            try: d_due = pd.to_datetime(det_f[dk]).date(); break
                            except: pass
            defects_list.append({'MergeKey': reg_merge, 'ID': str(r.get('title') or def_id), 'Type': endpoint.split('-')[0].upper(), 'Status': 'Open', 'Description': desc, 'Due Date': d_due})
    df_defects = pd.DataFrame(defects_list)

    # 4. BOOKINGS
    xsrf = t_sess.cookies.get('XSRF-TOKEN')
    t_sess.headers.update({'X-XSRF-TOKEN': urllib.parse.unquote(xsrf), 'Referer': 'https://admin.toran.be/planning', 'Accept': 'application/json'})
    
    cust_map = {}
    try:
        c_resp = t_sess.get("https://admin.toran.be/api/customers", timeout=10).json()
        for c in c_resp.get('data', c_resp): cust_map[str(c.get('id'))] = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
    except: pass
    pilot_map = {}
    try:
        p_resp = t_sess.get("https://admin.toran.be/api/pilots?page_size=100", timeout=10).json()
        for p in p_resp.get('data', []): pilot_map[str(p.get('id'))] = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    except: pass

    now = pd.Timestamp.utcnow().tz_localize(None)
    end_dt = pd.to_datetime(end_date).replace(hour=23, minute=59)
    days_diff = (end_dt - now).days
    weeks_to_fetch = max(1, int(days_diff / 7) + 5)
    book_list = []
    
    for i in range(weeks_to_fetch): 
        target = now + pd.Timedelta(weeks=i)
        try:
            resp = t_sess.get(f"https://admin.toran.be/api/planning?week={target.isocalendar()[1]}&year={target.isocalendar()[0]}").json()
            id_map = {str(h['id']): h['title'].upper() for h in resp.get('helis', [])}
            for f in resp.get('entries', []):
                if f.get('status') == 'confirmed':
                    start = pd.to_datetime(f.get('reserved_start_datetime')).tz_convert(None)
                    end = pd.to_datetime(f.get('reserved_end_datetime')).tz_convert(None)
                    if start < now: continue
                    reg = id_map.get(str(f.get('heli_id', '')))
                    guest = f"{f.get('customer_first_name','')} {f.get('customer_last_name','')}".strip()
                    if not guest and f.get('customer_id'): guest = cust_map.get(str(f.get('customer_id')), '')
                    if not guest: guest = str(f.get('title', 'Guest'))
                    inst = pilot_map.get(str(f.get('instructor_id')), 'Toran Team')
                    if reg: 
                        planned_hours = (end - start).total_seconds() / 3600 * 0.60
                        book_list.append({'MergeKey': normalize_tail(reg), 'Registration': reg, 'Start': start, 'End': end, 'Planned': planned_hours, 'Type': str(f.get('booking_type', 'Flight')).capitalize(), 'Details': guest, 'Instructor': inst, 'Departure': f.get('departure_airport_name', 'EBKT')})
        except: pass

    df_books = pd.DataFrame(book_list)
    if not df_books.empty:
        df_books = df_books.sort_values(['MergeKey', 'Start'])
        df_books = df_books.drop_duplicates(subset=['MergeKey', 'Start'])
        df_books['Cumulative'] = df_books.groupby('MergeKey')['Planned'].cumsum()
        df_books = pd.merge(df_books, df_ac[['MergeKey', 'Potential']], on='MergeKey', how='left')
        df_books['Is_Breach'] = df_books['Cumulative'] > df_books['Potential']
        
        breach_dates = df_books[df_books['Is_Breach']].groupby('MergeKey')['Start'].min().reset_index().rename(columns={'Start': 'Breach Date'})
        
        df_books_display = df_books[df_books['Start'] <= end_dt]
        usage = df_books_display.groupby('MergeKey')['Planned'].sum().reset_index()
        
        df = pd.merge(df_ac, usage, on='MergeKey', how='left').fillna({'Planned': 0})
        df = pd.merge(df, breach_dates, on='MergeKey', how='left')
        
        last_booking = df_books.groupby('MergeKey')['Start'].max().reset_index().rename(columns={'Start': 'LastBookDate'})
        df = pd.merge(df, last_booking, on='MergeKey', how='left')
        
        for idx, row in df.iterrows():
            if pd.isnull(row.get('Breach Date')): 
                reg_key = row['MergeKey']
                remaining = row['Potential'] - row['Planned']
                fallback_rate = global_rates.get(reg_key, 0)
                
                if remaining > 0 and fallback_rate > 0:
                    sim_date = row['LastBookDate'] if pd.notnull(row.get('LastBookDate')) else now
                    days_counter = 0
                    while remaining > 0 and days_counter < (365 * 5):
                        sim_date += timedelta(days=1)
                        days_counter += 1
                        month = sim_date.month
                        daily_consumption = 0
                        if reg_key in seasonal_rates and month in seasonal_rates[reg_key]:
                            daily_consumption = seasonal_rates[reg_key][month]
                        else:
                            daily_consumption = fallback_rate
                        if daily_consumption <= 0: daily_consumption = 0.1
                        remaining -= daily_consumption
                    
                    df.at[idx, 'Breach Date'] = sim_date
                    df.at[idx, 'Is_Projected'] = True
    else:
        df = df_ac.assign(Planned=0, **{'Breach Date': None})
        df_books_display = pd.DataFrame()

    df['IntervalSpan'] = df['Interval']
    if 'LastHours' in df.columns:
        mask = df['LastHours'].notna() & (df['Limit'] > df['LastHours'])
        # Updated Logic: Use accurate interval span for progress bar calculation
        df.loc[mask, 'IntervalSpan'] = df.loc[mask, 'Limit'] - df.loc[mask, 'LastHours']

    df['Forecast'] = df['Potential'] - df['Planned']
    
    df['Life Now %'] = (df['Potential'] / df['IntervalSpan']) * 100
    df['Life Now %'] = df['Life Now %'].fillna(0).clip(0, 100)
    df['Life Forecast %'] = (df['Forecast'] / df['IntervalSpan']) * 100
    df['Life Forecast %'] = df['Life Forecast %'].fillna(0).clip(0, 100)

    # 5. DOCUMENTS
    docs_list = []
    today_date = pd.Timestamp.now().date()
    
    if 'AircraftID' in df.columns:
        ac_map = df[['MergeKey', 'AircraftID']].dropna().drop_duplicates().to_dict('records')
        for ac_record in ac_map:
            ac_key = ac_record['MergeKey']
            ac_id = ac_record['AircraftID']
            page = 1
            while True:
                query = f"documents?search=&filters=W10%3D&orderBy=&perPage=50&trashed=&page={page}&viaResource=aircraft&viaResourceId={ac_id}&viaRelationship=documents&relationshipType=hasMany"
                docs_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", query)
                if not docs_json or 'resources' not in docs_json or not docs_json['resources']: break
                current_batch = docs_json['resources']
                for r in current_batch:
                    fields = {f['attribute']: f['value'] for f in r.get('fields', [])}
                    is_active = True
                    for f in r.get('fields', []):
                        attr = str(f.get('attribute', '')).lower()
                        val = f.get('value')
                        if attr in ['is_active', 'active', 'is_valid']:
                            if val in [False, 0, '0', 'false', 'False', None]: is_active = False; break
                    if not is_active: continue 
                    doc_type_val = None
                    for f in r.get('fields', []):
                        if f.get('attribute') in ['document_type', 'type', 'documentType', 'subtype']:
                            doc_type_val = str(f.get('value') or '').strip(); break
                    doc_final_name = doc_type_val if doc_type_val else str(fields.get('name') or fields.get('filename') or "Document").strip()
                    doc_final_name_lower = doc_final_name.lower()
                    if not any(kw in doc_final_name_lower for kw in ['review', 'arc', 'insur', 'verzekering', 'extension']): continue
                    doc_date = None
                    def parse_date(val):
                        if not val or len(str(val)) < 8: return None
                        val_str = str(val).strip()
                        try: return pd.to_datetime(val_str).date()
                        except: pass
                        try: return datetime.strptime(val_str, '%d/%m/%Y').date()
                        except: pass
                        try: return datetime.strptime(val_str, '%d-%m-%Y').date()
                        except: pass
                        return None
                    target_keys = ['valid_to', 'valid_until', 'expiry_date', 'due_date', 'vervaldatum', 'einddatum', 'date', 'geldig_tot', 'valid_from', 'issue_date']
                    for k in target_keys:
                        val = fields.get(k)
                        d = parse_date(val)
                        if d: doc_date = d; break
                    if not doc_date:
                        for val in fields.values():
                            if isinstance(val, str) and (len(val) == 10 or len(val) == 9):
                                d = parse_date(val)
                                if d: doc_date = d; break
                    if not doc_date:
                        doc_id = r.get('id', {}).get('value') if isinstance(r.get('id'), dict) else r.get('id')
                        if doc_id:
                            detail_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", f"documents/{doc_id}")
                            if detail_json and 'resource' in detail_json:
                                det_fields = {f['attribute']: f['value'] for f in detail_json['resource'].get('fields', [])}
                                for k in target_keys:
                                    d = parse_date(det_fields.get(k))
                                    if d: doc_date = d; break
                                if not doc_date:
                                    for val in det_fields.values():
                                        d = parse_date(val)
                                        if d: doc_date = d; break
                    days_remaining = (doc_date - today_date).days if doc_date else None
                    status_icon = "❓"
                    if days_remaining is not None:
                        if days_remaining < 0: status_icon = "🔴" 
                        elif days_remaining <= 30: status_icon = "🟠" 
                        else: status_icon = "🟢" 
                    docs_list.append({'MergeKey': ac_key, 'Document': doc_final_name, 'Due Date': doc_date, 'Days Left': days_remaining, 'Status': status_icon})
                if len(current_batch) == 0: break
                page += 1
                if page > 20: break
            
    df_docs = pd.DataFrame(docs_list)
    if not df_docs.empty:
        df_docs = df_docs.sort_values(['Document', 'Due Date'], ascending=[True, False])
        df_docs = df_docs.drop_duplicates(subset=['MergeKey', 'Document'], keep='first')
        df_docs = df_docs.sort_values('Due Date', na_position='last')
    
    return df, df_books_display, df_defects, df_docs, weeks_to_fetch

# --- UI EXECUTION ---
with st.sidebar:
    try: st.image("Asset 4@4x.jpg", use_container_width=True)
    except FileNotFoundError: pass 
    st.markdown("### Maintenance Center")
    st.write("Go to **pages/welcome_page** for TV Mode")
    selected_date = st.date_input("🗓️ End Date", value=datetime.today() + timedelta(days=35))
    if st.button('🔄 Refresh'): st.cache_data.clear(); st.session_state.clear(); st.rerun()

seasonal_rates, global_rates = get_historical_rates_interactive()
df, raw_books_df, df_defects, df_docs, weeks_scanned = fetch_and_merge_data_v12(selected_date, seasonal_rates, global_rates)

with st.sidebar:
    st.caption(f"Scanning {weeks_scanned} weeks ahead for flights.")
    if global_rates:
        st.caption("Daily Flight Rates (Hist):")
        for k, v in global_rates.items():
            st.caption(f"{k}: {v:.2f} h/day")

st.title("Operations & Maintenance Forecast")

if df is not None:
    today = pd.Timestamp.now().normalize()
    end_dt_ts = pd.to_datetime(selected_date)
    
    for _, r in df.iterrows():
        if r['Forecast'] < 0:
            msg = f"🛑 **GROUNDING:** {r['Registration']} breach on {r['Breach Date'].strftime('%d %b') if pd.notnull(r.get('Breach Date')) else 'Today'}!"
            st.error(msg, icon="🛑")
        if pd.notnull(r['Due Date']):
            days_left = (r['Due Date'] - today.date()).days
            if days_left < 0:
                st.error(f"🛑 **CALENDAR:** {r['Registration']} limit {r['Due Date'].strftime('%d %b %Y')} (EXPIRED!)", icon="🛑")
            elif days_left <= 30:
                st.warning(f"⚠️ **CALENDAR:** {r['Registration']} limit {r['Due Date'].strftime('%d %b %Y')} (Due in {days_left} days)", icon="⚠️")

    tabs = st.tabs(["Fleet Overview"] + sorted(df['Registration'].tolist()))
    
    with tabs[0]:
        st.subheader("Fleet Summary")
        cols = ['Registration', 'Type', 'Current', 'Potential', 'Life Now %', 'Planned', 'Forecast', 'Life Forecast %', 'Due Date']
        if 'LastDate' in df.columns: cols.extend(['LastDate', 'LastType', 'LastHours'])
        st.dataframe(df[cols], 
                     column_config={
                         "Life Now %": st.column_config.ProgressColumn("Life Remaining", format="%.0f%%", min_value=0, max_value=100),
                         "Life Forecast %": st.column_config.ProgressColumn("Life at Forecast", format="%.0f%%", min_value=0, max_value=100),
                         "Due Date": st.column_config.DateColumn("Due Date", format="DD MMM YYYY"),
                         "LastDate": st.column_config.DateColumn("Last Performed", format="DD MMM YYYY"),
                         "LastHours": st.column_config.NumberColumn("Last TSN", format="%.1f h")
                     }, hide_index=True, use_container_width=True)

    for i, tail in enumerate(sorted(df['Registration'].tolist()), start=1):
        with tabs[i]:
            ac_df = df[df['Registration'] == tail].iloc[0]
            
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Current TSN", f"{ac_df['Current']:.1f}h")
            c2.metric("Potential", f"{ac_df['Potential']:.1f}h")
            c3.metric("Booked", f"{ac_df['Planned']:.1f}h")
            c4.metric("Forecast", f"{ac_df['Forecast']:.1f}h", delta=f"{ac_df['Forecast']-ac_df['Potential']:.1f}h")
            
            st.markdown("---")
            
            col_maint, col_prog = st.columns(2)
            with col_maint:
                st.subheader("🛠️ Maintenance Status")
                st.write(f"**Next Due:** {ac_df['Type']} ({ac_df['Limit']:.1f}h)")
                
                if 'LastDate' in ac_df and pd.notnull(ac_df['LastDate']):
                    last_h = f" (at {ac_df['LastHours']:.1f}h)" if pd.notnull(ac_df.get('LastHours')) else ""
                    st.success(f"**Last Performed:** {ac_df['LastType']} on {ac_df['LastDate'].strftime('%d %b %Y')}{last_h}")
                else:
                    st.warning("History not found.")
                
                if pd.notnull(ac_df['Due Date']):
                    days = (ac_df['Due Date'] - today.date()).days
                    if days < 0:
                        st.error(f"**Calendar Limit:** {ac_df['Due Date'].strftime('%d %b %Y')} (Expired!)")
                    elif days <= 30:
                        st.warning(f"**Calendar Limit:** {ac_df['Due Date'].strftime('%d %b %Y')} ({days} days left)")
                    else:
                        st.success(f"**Calendar Limit:** {ac_df['Due Date'].strftime('%d %b %Y')} ({days} days left)")
                
                if pd.notnull(ac_df.get('Breach Date')):
                    b_date = ac_df['Breach Date']
                    is_proj = ac_df.get('Is_Projected', False)
                    date_str = b_date.strftime('%d %b %Y')
                    if is_proj:
                        st.info(f"**ℹ️ Projected Breach (Based on History):** {date_str}")
                    elif b_date <= end_dt_ts:
                        st.write(f"**🚨 Hour Breach Forecast:** :red[{date_str}] (Within Forecast Period)")
                    else:
                        st.write(f"**✅ Hour Breach Forecast:** :green[{date_str}] (After Forecast Period)")
                else:
                    st.write("**✅ Hour Breach Forecast:** No breach forecasted")

            with col_prog:
                st.subheader("📊 Life Status")
                
                total_potential = ac_df['Potential']
                exceedance = ac_df.get('Exceedance', 0.0)
                interval = ac_df['IntervalSpan'] # Use calculated span, not raw interval
                
                normal_rem = max(0.0, total_potential - exceedance)
                tol_rem = min(exceedance, total_potential)
                st.write(f"**Life Remaining NOW:** (Total: {total_potential:.1f}h)")
                render_progress_bar(normal_rem, tol_rem, interval)
                
                forecast_total = ac_df['Forecast']
                forecast_normal = max(0.0, forecast_total - exceedance)
                forecast_tol = min(exceedance, forecast_total)
                st.write(f"**Life at Forecast ({selected_date.strftime('%d %b')}):** (Total: {forecast_total:.1f}h)")
                render_progress_bar(forecast_normal, forecast_tol, interval)
                
                if pd.notnull(ac_df['Due Date']):
                    cal_due = ac_df['Due Date']
                    cal_tol = ac_df.get('CalExceedance', 0.0)
                    days_rem_total = (cal_due - today.date()).days
                    if days_rem_total > 0:
                        cal_normal_rem = max(0, days_rem_total - cal_tol)
                        cal_tol_rem = min(cal_tol, days_rem_total)
                        span = 365 
                        if 'LastDate' in ac_df and pd.notnull(ac_df['LastDate']):
                            span = (cal_due - ac_df['LastDate']).days
                            if span <= 0: span = 365
                        st.write(f"**Life Remaining NOW (Calendar):** (Total: {int(days_rem_total)} days)")
                        render_progress_bar(cal_normal_rem, cal_tol_rem, span)
                    else:
                        st.error("**Calendar Life Expired**")

                st.markdown("---")
                st.write("**🏗️ Major Overhaul Tracker (2200h / 12Y)**")
                ac_meta = AIRCRAFT_DB.get(normalize_tail(tail))
                if ac_meta:
                    oh_limit_h = ac_meta.get('overhaul_limit_h', 2200)
                    oh_current = ac_df['Current']
                    render_overhaul_bar(oh_current, oh_limit_h, "Airframe Hours")
                    install_date = ac_meta.get('overhaul_install')
                    if install_date:
                        due_12y = install_date + timedelta(days=365*12)
                        total_days_12y = (due_12y - install_date).days
                        used_days = (today.date() - install_date).days
                        render_overhaul_bar(used_days, total_days_12y, f"Calendar 12Y (Due {due_12y.strftime('%d %b %Y')})")
                    else:
                        st.caption("No 12Y Installation Date in Database")
                else:
                    st.caption("Aircraft not in static DB")

            st.markdown("---")
            
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("⚠️ Open Defects")
                if not df_defects.empty and 'MergeKey' in df_defects.columns:
                    ac_def = df_defects[df_defects['MergeKey'] == normalize_tail(tail)]
                    if not ac_def.empty: st.dataframe(ac_def[['ID', 'Type', 'Status', 'Due Date', 'Description']], hide_index=True, use_container_width=True)
                    else: st.info("✅ No open defects.")
                else: st.info("✅ No open defects.")
                
                st.markdown("---")
                st.subheader("📂 Aircraft Documents (ARC & Insurance)")
                if not df_docs.empty:
                    ac_docs = df_docs[df_docs['MergeKey'] == normalize_tail(tail)]
                    if not ac_docs.empty:
                        st.dataframe(ac_docs[['Status', 'Document', 'Due Date', 'Days Left']], hide_index=True, use_container_width=True, column_config={"Due Date": st.column_config.DateColumn("Valid Until", format="DD MMM YYYY"), "Days Left": st.column_config.NumberColumn("Days Left", format="%d")})
                    else:
                        st.info("No active ARC or Insurance documents found.")
                else:
                    st.info("No documents found.")

            with col2:
                st.subheader("📋 Flight Log")
                if not raw_books_df.empty:
                    ac_b = raw_books_df[raw_books_df['MergeKey'] == normalize_tail(tail)]
                    if not ac_b.empty: st.dataframe(ac_b[['Start', 'Type', 'Details', 'Instructor', 'Planned']], hide_index=True, use_container_width=True)
                    else: st.info("No bookings found.")
    
    with st.sidebar:
        st.markdown("---")
        csv_data = convert_df_to_csv(df[['Registration', 'Type', 'Current', 'Limit', 'Potential', 'Planned', 'Forecast', 'Due Date', 'Breach Date']])
        st.download_button("📥 Download Summary (CSV)", csv_data, f"Fleet_Forecast_{selected_date}.csv", "text/csv", use_container_width=True)
