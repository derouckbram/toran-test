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

# --- Aircraft Image Database ---
AIRCRAFT_DB = {
    "OOHXP": {"model": "Robinson R44 Raven II", "image": "raven2.jpg", "seats": "4 Seats", "cruise": "109 kts"},
    "OOMOO": {"model": "Robinson R44 Raven I", "image": "raven1.jpg", "seats": "4 Seats", "cruise": "109 kts"},
    "OOSKH": {"model": "Guimbal Cabri G2", "image": "cabri.jpg", "seats": "2 Seats", "cruise": "90 kts"}
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

# --- MASTER LOGIC ---
@st.cache_data(ttl=300)
def fetch_and_merge_data_master(end_date):
    c_sess = get_authenticated_session("https://toran-camo.flightapp.be", "/admin/login", st.secrets["CAMO_EMAIL"], st.secrets["CAMO_PASS"])
    t_sess = get_authenticated_session("https://admin.toran.be", "/login", st.secrets["TORAN_EMAIL"], st.secrets["TORAN_PASS"])

    if not c_sess or not t_sess: return None, "Auth Failed", {}, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), []

    # 1. UPCOMING MAINTENANCE
    maint_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", "upcoming-aircraft-maintenances?perPage=100")
    ac_data = []
    
    if maint_json:
        for r in maint_json.get('resources', []):
            fields = {f['attribute']: f['value'] for f in r.get('fields', [])}
            
            reg_raw = str(fields.get('aircraft') or "Unknown")
            reg_display = reg_raw.split(' ')[0].strip().upper()
            reg_merge = normalize_tail(reg_display)
            
            ac_id_internal = None
            for f_raw in r.get('fields', []):
                if f_raw.get('attribute') == 'aircraft':
                    ac_id_internal = f_raw.get('belongsToId')
                    break

            try: curr_val = float(str(fields.get('current_hours_ttsn', 0)).replace(',', ''))
            except: curr_val = 0.0
            try: limit_val = float(str(fields.get('max_hours', 0)).replace(',', ''))
            except: limit_val = 0.0
            
            maint_type_str = str(fields.get('aircraftMaintenanceType', "Standard"))
            try: interval = float(re.search(r'(\d+)', maint_type_str).group(1))
            except: interval = 100.0
            if interval <= 0: interval = 100.0 

            due_date = None
            raw_date = fields.get('max_valid_until')
            if raw_date and str(raw_date).strip() not in ["", "—", "None", "null"]:
                try: due_date = pd.to_datetime(str(raw_date)).date()
                except: pass

            ac_data.append({
                'Registration': reg_display, 'MergeKey': reg_merge, 'Current': curr_val, 
                'Limit': limit_val, 'Type': maint_type_str, 'Interval': interval, 
                'Potential': max(0.0, limit_val - curr_val), 'Due Date': due_date,
                'AircraftID': ac_id_internal 
            })
    df_ac = pd.DataFrame(ac_data).sort_values('Limit').drop_duplicates('MergeKey')

    # 2. LAST PERFORMED
    hist_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", "aircraft-maintenance-histories?perPage=100")
    if hist_json:
        hist_list = []
        for r in hist_json.get('resources', []):
            fields = {f['attribute']: f['value'] for f in r.get('fields', [])}
            reg_raw = str(fields.get('aircraft') or "")
            reg_merge = normalize_tail(reg_raw.split(' ')[0])
            
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
                        if 100 < val < 20000: # Sanity check
                            hist_hours = val
                            break
                    except: pass
            
            if hist_hours is None:
                for k, v in fields.items():
                    if isinstance(v, (str, int, float)):
                        try:
                            val_str = str(v).replace(',', '')
                            if re.match(r'^\d+(\.\d{1,2})?$', val_str):
                                val = float(val_str)
                                if 500 < val < 15000: # Broad sanity check
                                    hist_hours = val
                                    break
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
    book_list = []
    
    for i in range(4): # Scan 4 weeks
        target = now + pd.Timedelta(weeks=i)
        try:
            resp = t_sess.get(f"https://admin.toran.be/api/planning?week={target.isocalendar()[1]}&year={target.isocalendar()[0]}").json()
            id_map = {str(h['id']): h['title'].upper() for h in resp.get('helis', [])}
            for f in resp.get('entries', []):
                if f.get('status') == 'confirmed':
                    start = pd.to_datetime(f.get('reserved_start_datetime')).tz_convert(None)
                    end = pd.to_datetime(f.get('reserved_end_datetime')).tz_convert(None)
                    
                    if start < now: continue
                    # Removed the "end_dt" break here so we can find breaches FAR in the future if needed
                    # if start > end_dt: continue 
                    
                    reg = id_map.get(str(f.get('heli_id', '')))
                    guest = f"{f.get('customer_first_name','')} {f.get('customer_last_name','')}".strip()
                    if not guest and f.get('customer_id'): guest = cust_map.get(str(f.get('customer_id')), '')
                    if not guest: guest = str(f.get('title', 'Guest'))
                    inst = pilot_map.get(str(f.get('instructor_id')), 'Toran Team')

                    if reg: book_list.append({
                        'MergeKey': normalize_tail(reg), 'Registration': reg, 'Start': start, 'End': end, 
                        'Planned': (end - start).total_seconds() / 3600 * 0.85, 'Type': str(f.get('booking_type', 'Flight')).capitalize(), 
                        'Details': guest, 'Instructor': inst, 'Departure': f.get('departure_airport_name', 'EBKT')
                    })
        except: pass

    df_books = pd.DataFrame(book_list)
    if not df_books.empty:
        df_books = df_books.sort_values(['MergeKey', 'Start'])
        df_books['Cumulative'] = df_books.groupby('MergeKey')['Planned'].cumsum()
        df_books = pd.merge(df_books, df_ac[['MergeKey', 'Potential']], on='MergeKey', how='left')
        df_books['Is_Breach'] = df_books['Cumulative'] > df_books['Potential']
        breach_dates = df_books[df_books['Is_Breach']].groupby('MergeKey')['Start'].min().reset_index().rename(columns={'Start': 'Breach Date'})
        
        # Filter books for display (only show up to selected date), but keep breach calculation valid
        df_books_display = df_books[df_books['Start'] <= end_dt]
        usage = df_books_display.groupby('MergeKey')['Planned'].sum().reset_index()
        
        df = pd.merge(df_ac, usage, on='MergeKey', how='left').fillna({'Planned': 0})
        df = pd.merge(df, breach_dates, on='MergeKey', how='left')
    else:
        df = df_ac.assign(Planned=0, **{'Breach Date': None})
        df_books_display = pd.DataFrame()

    df['IntervalSpan'] = df['Interval']
    if 'LastHours' in df.columns:
        mask = df['LastHours'].notna() & (df['Limit'] > df['LastHours'])
        df.loc[mask, 'IntervalSpan'] = df.loc[mask, 'Limit'] - df.loc[mask, 'LastHours']

    df['Forecast'] = df['Potential'] - df['Planned']
    df['Life Now %'] = (df['Potential'] / df['IntervalSpan']) * 100
    df['Life Now %'] = df['Life Now %'].fillna(0).clip(0, 100)
    df['Life Forecast %'] = (df['Forecast'] / df['IntervalSpan']) * 100
    df['Life Forecast %'] = df['Life Forecast %'].fillna(0).clip(0, 100)

    # 5. DOCUMENTS (Strict ARC/Insurance Only)
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
                
                if not docs_json or 'resources' not in docs_json or not docs_json['resources']:
                    break
                
                current_batch = docs_json['resources']
                
                for r in current_batch:
                    fields = {f['attribute']: f['value'] for f in r.get('fields', [])}
                    
                    is_active = True
                    for f in r.get('fields', []):
                        attr = str(f.get('attribute', '')).lower()
                        val = f.get('value')
                        if attr in ['is_active', 'active', 'is_valid']:
                            if val in [False, 0, '0', 'false', 'False', None]:
                                is_active = False
                                break
                    if not is_active: continue 

                    doc_type_val = None
                    for f in r.get('fields', []):
                        if f.get('attribute') in ['document_type', 'type', 'documentType', 'subtype']:
                            doc_type_val = str(f.get('value') or '').strip()
                            break
                    
                    doc_final_name = doc_type_val if doc_type_val else str(fields.get('name') or fields.get('filename') or "Document").strip()
                    doc_final_name_lower = doc_final_name.lower()
                    
                    # FILTER: ARC/Insurance ONLY
                    if not any(kw in doc_final_name_lower for kw in ['review', 'arc', 'insur', 'verzekering', 'extension']):
                        continue
                    
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
                        if d: 
                            doc_date = d
                            break
                    
                    if not doc_date:
                        for val in fields.values():
                            if isinstance(val, str) and (len(val) == 10 or len(val) == 9):
                                d = parse_date(val)
                                if d: doc_date = d; break

                    # DEEP DIVE
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
                    
                    docs_list.append({
                        'MergeKey': ac_key, 
                        'Document': doc_final_name, 
                        'Due Date': doc_date,
                        'Days Left': days_remaining,
                        'Status': status_icon
                    })
                
                if len(current_batch) == 0: break
                page += 1
                if page > 20: break
            
    df_docs = pd.DataFrame(docs_list)
    if not df_docs.empty:
        df_docs = df_docs.sort_values(['Document', 'Due Date'], ascending=[True, False])
        df_docs = df_docs.drop_duplicates(subset=['MergeKey', 'Document'], keep='first')
        df_docs = df_docs.sort_values('Due Date', na_position='last')
    
    return df, df_books_display, df_defects, df_docs

# --- STYLE CSS ---
st.markdown("""
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
""", unsafe_allow_html=True)

# --- UI EXECUTION ---
with st.sidebar:
    try: st.image("toran_logo.png", use_container_width=True)
    except FileNotFoundError: pass 
    st.markdown("### Maintenance Center")
    st.write("Go to **pages/welcome_page** for TV Mode")
    selected_date = st.date_input("🗓️ End Date", value=datetime.today() + timedelta(days=35))
    if st.button('🔄 Refresh'): st.cache_data.clear(); st.rerun()

df, raw_books_df, df_defects, df_docs = fetch_and_merge_data_master(selected_date)

st.title("Operations & Maintenance Forecast")

if df is not None:
    today = pd.Timestamp.now().normalize()
    end_dt_ts = pd.to_datetime(selected_date)
    
    for _, r in df.iterrows():
        if r['Forecast'] < 0:
            msg = f"🛑 **GROUNDING:** {r['Registration']} breach on {r['Breach Date'].strftime('%d %b') if pd.notnull(r.get('Breach Date')) else 'Today'}!"
            st.error(msg, icon="🛑")
        if r['Due Date'] and (r['Due Date'] - today.date()).days <= 14:
            st.warning(f"⚠️ **CALENDAR:** {r['Registration']} limit {r['Due Date'].strftime('%d %b')}", icon="📅")

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
                    color = "red" if days < 14 else "green"
                    st.markdown(f"**Calendar Limit:** :{color}[{ac_df['Due Date'].strftime('%d %b %Y')}] ({days} days left)")
                
                # --- UPDATED BREACH DISPLAY ---
                if pd.notnull(ac_df.get('Breach Date')):
                    b_date = ac_df['Breach Date']
                    if b_date <= end_dt_ts:
                        st.write(f"**🚨 Hour Breach Forecast:** :red[{b_date.strftime('%d %b %Y')}] (Within Forecast Period)")
                    else:
                        st.write(f"**✅ Hour Breach Forecast:** :green[{b_date.strftime('%d %b %Y')}] (After Forecast Period)")
                else:
                    st.write("**✅ Hour Breach Forecast:** No breach forecasted")

            with col_prog:
                st.subheader("📊 Life Status")
                baseline_txt = f" (Span: {ac_df['IntervalSpan']:.0f}h)"
                st.write(f"**Life Remaining NOW:**{baseline_txt}")
                st.progress(int(ac_df['Life Now %']), text=f"{ac_df['Life Now %']:.0f}%")
                
                st.write(f"**Life at Forecast ({selected_date.strftime('%d %b')}):**")
                st.progress(int(ac_df['Life Forecast %']), text=f"{ac_df['Life Forecast %']:.0f}%")

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
                        st.dataframe(
                            ac_docs[['Status', 'Document', 'Due Date', 'Days Left']], 
                            hide_index=True, 
                            use_container_width=True, 
                            column_config={
                                "Due Date": st.column_config.DateColumn("Valid Until", format="DD MMM YYYY"),
                                "Days Left": st.column_config.NumberColumn("Days Left", format="%d")
                            }
                        )
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
