import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import urllib.parse
import re  
from datetime import datetime, timedelta

# --- Page Config ---
st.set_page_config(page_title="Toran Maintenance", layout="wide", page_icon="🚁")

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

# --- DATA ENGINE ---
@st.cache_data(ttl=300)
def fetch_maintenance_data(end_date):
    c_sess = get_authenticated_session("https://toran-camo.flightapp.be", "/admin/login", st.secrets["CAMO_EMAIL"], st.secrets["CAMO_PASS"])
    t_sess = get_authenticated_session("https://admin.toran.be", "/login", st.secrets["TORAN_EMAIL"], st.secrets["TORAN_PASS"])
    if not c_sess or not t_sess: return None, pd.DataFrame(), pd.DataFrame()

    # 1. Maintenance & Calendar
    maint_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", "upcoming-aircraft-maintenances?perPage=100")
    ac_data = []
    if maint_json:
        for r in maint_json.get('resources', []):
            fields = {f['attribute']: f['value'] for f in r.get('fields', [])}
            reg_display = str(fields.get('aircraft') or "Unknown").split(' ')[0].strip().upper()
            reg_merge = normalize_tail(reg_display)
            try: curr = float(str(fields.get('current_hours_ttsn', 0)).replace(',', ''))
            except: curr = 0.0
            try: limit = float(str(fields.get('max_hours', 0)).replace(',', ''))
            except: limit = 0.0
            m_type = str(fields.get('aircraftMaintenanceType', "100h"))
            try: interval = float(re.search(r'(\d+)', m_type).group(1))
            except: interval = 100.0
            raw_date = fields.get('max_valid_until')
            due_date = pd.to_datetime(raw_date).date() if raw_date and str(raw_date).strip() != "—" else None
            
            ac_data.append({
                'Registration': reg_display, 'MergeKey': reg_merge, 'Current': curr, 'Limit': limit, 
                'Type': m_type, 'Interval': interval, 'Potential': max(0.0, limit - curr), 'Due Date': due_date
            })
    df_ac = pd.DataFrame(ac_data).sort_values('Limit').drop_duplicates('MergeKey')

    # 2. Defects (Deep Dive)
    defects_list = []
    for endpoint in ['ddl-defects', 'hil-defects']:
        d_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", f"{endpoint}?perPage=100")
        if not d_json or 'resources' not in d_json: continue
        for r in d_json.get('resources', []):
            idx_f = {f['attribute']: f['value'] for f in r.get('fields', [])}
            if str(idx_f.get('status', '')).lower() in ['closed', 'gesloten', 'done']: continue
            def_id = r.get('id', {}).get('value') if isinstance(r.get('id'), dict) else r.get('id')
            reg_merge = normalize_tail(str(idx_f.get('aircraft') or "").split(' ')[0])
            
            desc, d_due = "No description provided.", None
            det = fetch_resource(c_sess, "https://toran-camo.flightapp.be", f"{endpoint}/{def_id}")
            if det and 'resource' in det:
                det_f = {f['attribute']: f['value'] for f in det['resource'].get('fields', [])}
                for k in ['description', 'defect', 'remarks', 'finding']:
                    if det_f.get(k): desc = re.sub(r'<[^>]+>', '', str(det_f.get(k))).strip(); break
                for dk in ['due_date', 'ultimate_repair_date', 'limit']:
                    if det_f.get(dk): 
                        try: d_due = pd.to_datetime(det_f[dk]).date(); break
                        except: pass
            defects_list.append({'MergeKey': reg_merge, 'ID': str(r.get('title') or def_id), 'Type': endpoint.split('-')[0].upper(), 'Status': 'Open', 'Description': desc, 'Due Date': d_due})
    df_defects = pd.DataFrame(defects_list)

    # 3. Bookings
    xsrf = t_sess.cookies.get('XSRF-TOKEN')
    t_sess.headers.update({'X-XSRF-TOKEN': urllib.parse.unquote(xsrf), 'Referer': 'https://admin.toran.be/planning', 'Accept': 'application/json'})
    
    # Customer Map
    cust_map = {}
    try:
        c_resp = t_sess.get("https://admin.toran.be/api/customers", timeout=10).json()
        for c in c_resp.get('data', c_resp): cust_map[str(c.get('id'))] = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
    except: pass

    # Pilot Map
    pilot_map = {}
    try:
        p_resp = t_sess.get("https://admin.toran.be/api/pilots?page_size=100", timeout=10).json()
        for p in p_resp.get('data', []): pilot_map[str(p.get('id'))] = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    except: pass

    book_list, now = [], pd.Timestamp.utcnow().tz_localize(None)
    end_dt = pd.to_datetime(end_date).replace(hour=23, minute=59)
    for i in range(4): 
        target = now + pd.Timedelta(weeks=i)
        try:
            resp = t_sess.get(f"https://admin.toran.be/api/planning?week={target.isocalendar()[1]}&year={target.isocalendar()[0]}").json()
            id_map = {str(h['id']): h['title'].upper() for h in resp.get('helis', [])}
            for f in resp.get('entries', []):
                if f.get('status') == 'confirmed':
                    start = pd.to_datetime(f['reserved_start_datetime']).tz_convert(None)
                    end = pd.to_datetime(f['reserved_end_datetime']).tz_convert(None)
                    if start > end_dt: continue
                    reg = id_map.get(str(f.get('heli_id', '')))
                    
                    guest = f"{f.get('customer_first_name','')} {f.get('customer_last_name','')}".strip()
                    if not guest and f.get('customer_id'): guest = cust_map.get(str(f.get('customer_id')), '')
                    if not guest: guest = str(f.get('title', 'Guest'))
                    
                    inst = pilot_map.get(str(f.get('instructor_id')), 'Toran Team')

                    if reg: book_list.append({
                        'MergeKey': normalize_tail(reg), 'Registration': reg, 'Start': start, 'End': end, 
                        'Planned': (end - start).total_seconds() / 3600 * 0.85, 'Type': 'Flight', 
                        'Details': guest, 'Instructor': inst, 'Departure': f.get('departure_airport_name', 'EBKT')
                    })
        except: pass

    df_books = pd.DataFrame(book_list)
    if not df_books.empty:
        df_books = df_books.sort_values(['MergeKey', 'Start'])
        df_books['Cumulative'] = df_books.groupby('MergeKey')['Planned'].cumsum()
        merged = pd.merge(df_books, df_ac[['MergeKey', 'Potential']], on='MergeKey', how='left')
        merged['Is_Breach'] = merged['Cumulative'] > merged['Potential']
        breach_dates = merged[merged['Is_Breach']].groupby('MergeKey')['Start'].min().reset_index().rename(columns={'Start': 'Breach Date'})
        df = pd.merge(df_ac, df_books.groupby('MergeKey')['Planned'].sum().reset_index(), on='MergeKey', how='left').fillna({'Planned': 0})
        df = pd.merge(df, breach_dates, on='MergeKey', how='left')
    else:
        df = df_ac.assign(Planned=0, **{'Breach Date': None})

    df['Forecast'] = df['Potential'] - df['Planned']
    df['Life Now %'] = ((df['Potential'] / df['Interval']) * 100).clip(0, 100)
    df['Life Forecast %'] = ((df['Forecast'] / df['Interval']) * 100).clip(0, 100)
    return df, df_books, df_defects

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
    st.markdown("---")
    selected_date = st.date_input("🗓️ End Date", value=datetime.today() + timedelta(days=35))
    if st.button('🔄 Refresh'): st.cache_data.clear(); st.rerun()

df, raw_books_df, df_defects = fetch_maintenance_data(selected_date)

st.title("Operations & Maintenance Forecast")
if df is not None:
    today = pd.Timestamp.now().normalize()
    for _, r in df.iterrows():
        if r['Forecast'] < 0:
            st.error(f"🛑 **GROUNDING:** {r['Registration']} breach on {r['Breach Date'].strftime('%d %b') if pd.notnull(r['Breach Date']) else 'Today'}!", icon="🛑")
        if r['Due Date'] and (r['Due Date'] - today.date()).days <= 14:
            st.warning(f"⚠️ **CALENDAR:** {r['Registration']} limit {r['Due Date'].strftime('%d %b')}", icon="📅")

    tabs = st.tabs(["Fleet Overview"] + sorted(df['Registration'].tolist()))
    
    with tabs[0]:
        st.subheader("Summary Table")
        st.dataframe(df[['Registration', 'Type', 'Current', 'Limit', 'Potential', 'Life Now %', 'Planned', 'Forecast', 'Life Forecast %', 'Due Date']], hide_index=True, use_container_width=True)

    for i, tail in enumerate(sorted(df['Registration'].tolist()), start=1):
        with tabs[i]:
            ac_df = df[df['Registration'] == tail].iloc[0]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("TSN", f"{ac_df['Current']:.1f}h")
            c2.metric("Potential", f"{ac_df['Potential']:.1f}h")
            c3.metric("Booked", f"{ac_df['Planned']:.1f}h")
            c4.metric("Forecast", f"{ac_df['Forecast']:.1f}h", delta=f"{ac_df['Forecast']-ac_df['Potential']:.1f}h")
            
            st.markdown("---")
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("🛠️ Open Defects")
                if not df_defects.empty and 'MergeKey' in df_defects.columns:
                    ac_def = df_defects[df_defects['MergeKey'] == normalize_tail(tail)]
                    if not ac_def.empty: st.dataframe(ac_def[['ID', 'Type', 'Status', 'Due Date', 'Description']], hide_index=True)
                    else: st.info("✅ No open defects.")
                else: st.info("✅ No open defects.")
            with col2:
                st.subheader("📋 Flight Log")
                if not raw_books_df.empty:
                    ac_b = raw_books_df[raw_books_df['MergeKey'] == normalize_tail(tail)]
                    if not ac_b.empty: st.dataframe(ac_b[['Start', 'Type', 'Details', 'Instructor', 'Planned']], hide_index=True)
                    else: st.info("No bookings found.")
