import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import urllib.parse
import re  
from datetime import datetime, timedelta

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
    # Expanded prefixes to handle different Nova API routing
    for prefix in ["/admin/nova-api/", "/nova-api/"]:
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

    if not c_sess or not t_sess: return None, "Auth Failed", {}, pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    # 1. GET AIRCRAFT LIST & IDS
    # We need the viaResourceId (like x-4zbqjrdp) to get documents
    ac_list_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", "aircrafts?perPage=100")
    ac_map = {} # Maps Registration -> Nova ID
    if ac_list_json:
        for r in ac_list_json.get('resources', []):
            rid = r.get('id', {}).get('value') if isinstance(r.get('id'), dict) else r.get('id')
            fields = {f['attribute']: f['value'] for f in r.get('fields', [])}
            reg = str(fields.get('registration', '')).upper()
            if reg and rid: ac_map[reg] = rid

    # 2. UPCOMING MAINTENANCE
    maint_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", "upcoming-aircraft-maintenances?perPage=100")
    ac_data = []
    if maint_json:
        for r in maint_json.get('resources', []):
            fields = {f['attribute']: f['value'] for f in r.get('fields', [])}
            reg_raw = str(fields.get('aircraft') or "Unknown")
            reg_display = reg_raw.split(' ')[0].strip().upper()
            reg_merge = normalize_tail(reg_display)

            try: curr_val = float(str(fields.get('current_hours_ttsn', 0)).replace(',', ''))
            except: curr_val = 0.0
            try: limit_val = float(str(fields.get('max_hours', 0)).replace(',', ''))
            except: limit_val = 0.0
            
            m_type = str(fields.get('aircraftMaintenanceType', "Standard"))
            try: interval = float(re.search(r'(\d+)', m_type).group(1))
            except: interval = 100.0

            raw_date = fields.get('max_valid_until')
            due_date = pd.to_datetime(raw_date).date() if raw_date and str(raw_date) not in ["", "—", "None"] else None

            ac_data.append({
                'Registration': reg_display, 'MergeKey': reg_merge, 'Current': curr_val, 
                'Limit': limit_val, 'Type': m_type, 'Interval': interval, 
                'Potential': max(0.0, limit_val - curr_val), 'Due Date': due_date,
                'NovaID': ac_map.get(reg_display)
            })
    df_ac = pd.DataFrame(ac_data).sort_values('Limit').drop_duplicates('MergeKey')

    # 3. DOCUMENTS (Fetching per Aircraft based on your clues)
    all_docs = []
    for reg, nid in ac_map.items():
        doc_query = f"documents?viaResource=aircraft&viaResourceId={nid}&viaRelationship=documents&relationshipType=hasMany&perPage=50"
        d_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", doc_query)
        if d_json:
            for r in d_json.get('resources', []):
                dfields = {f['attribute']: f['value'] for f in r.get('fields', [])}
                d_name = str(dfields.get('name') or dfields.get('document_type') or "Doc")
                
                exp_date = None
                for k in ['valid_until', 'expiry_date', 'validity']:
                    if dfields.get(k):
                        try: exp_date = pd.to_datetime(dfields[k]).date(); break
                        except: pass
                
                if exp_date:
                    all_docs.append({
                        'MergeKey': normalize_tail(reg),
                        'Document': d_name,
                        'Valid Until': exp_date,
                        'Days Left': (exp_date - datetime.now().date()).days
                    })
    df_docs = pd.DataFrame(all_docs) if all_docs else pd.DataFrame(columns=['MergeKey', 'Document', 'Valid Until', 'Days Left'])

    # 4. DEFECTS & 5. BOOKINGS (Simplified for brevity, same logic as your base)
    # [Rest of logic remains same to ensure functionality]
    defects_list = []
    for endpoint in ['ddl-defects', 'hil-defects']:
        d_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", f"{endpoint}?perPage=50")
        if d_json:
            for r in d_json.get('resources', []):
                idx_f = {f['attribute']: f['value'] for f in r.get('fields', [])}
                if str(idx_f.get('status', '')).lower() in ['closed', 'done']: continue
                reg_merge = normalize_tail(str(idx_f.get('aircraft', '')).split(' ')[0])
                defects_list.append({'MergeKey': reg_merge, 'ID': r.get('id'), 'Type': endpoint.split('-')[0].upper(), 'Description': str(idx_f.get('description', 'No info'))})
    df_defects = pd.DataFrame(defects_list)

    # Bookings logic (Toran Admin)
    xsrf = t_sess.cookies.get('XSRF-TOKEN')
    t_sess.headers.update({'X-XSRF-TOKEN': urllib.parse.unquote(xsrf), 'Referer': 'https://admin.toran.be/planning'})
    book_list = []
    now = pd.Timestamp.now()
    try:
        b_resp = t_sess.get("https://admin.toran.be/api/planning").json()
        id_map = {str(h['id']): h['title'].upper() for h in b_resp.get('helis', [])}
        for f in b_resp.get('entries', []):
            if f.get('status') == 'confirmed':
                start = pd.to_datetime(f.get('reserved_start_datetime')).tz_localize(None)
                if start >= now:
                    reg = id_map.get(str(f.get('heli_id')))
                    if reg: book_list.append({
                        'MergeKey': normalize_tail(reg), 
                        'Planned': (pd.to_datetime(f.get('reserved_end_datetime')).tz_localize(None) - start).total_seconds()/3600 * 0.85
                    })
    except: pass
    
    df_books = pd.DataFrame(book_list)
    usage = df_books.groupby('MergeKey')['Planned'].sum().reset_index() if not df_books.empty else pd.DataFrame(columns=['MergeKey', 'Planned'])
    
    df = pd.merge(df_ac, usage, on='MergeKey', how='left').fillna({'Planned': 0})
    df['Forecast'] = df['Potential'] - df['Planned']
    df['Life Now %'] = (df['Potential'] / df['Interval'] * 100).clip(0, 100)
    df['Life Forecast %'] = (df['Forecast'] / df['Interval'] * 100).clip(0, 100)

    return df, df_books, df_defects, df_docs

# --- UI EXECUTION ---
with st.sidebar:
    selected_date = st.date_input("🗓️ End Date", value=datetime.today() + timedelta(days=35))
    if st.button('🔄 Refresh'): st.cache_data.clear(); st.rerun()

df, raw_books_df, df_defects, df_docs = fetch_and_merge_data_master(selected_date)

st.title("Operations & Maintenance Forecast")

if df is not None:
    tabs = st.tabs(["Fleet Overview"] + sorted(df['Registration'].tolist()))
    
    with tabs[0]:
        st.dataframe(df[['Registration', 'Type', 'Current', 'Potential', 'Planned', 'Forecast', 'Life Now %']], use_container_width=True)

    for i, tail in enumerate(sorted(df['Registration'].tolist()), start=1):
        with tabs[i]:
            ac_df = df[df['Registration'] == tail].iloc[0]
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("🛠️ Status")
                st.metric("Potential", f"{ac_df['Potential']:.1f}h")
                st.write(f"**Next Due:** {ac_df['Type']}")
                
                st.subheader("📄 Documents")
                if not df_docs.empty:
                    this_docs = df_docs[df_docs['MergeKey'] == normalize_tail(tail)]
                    if not this_docs.empty:
                        st.table(this_docs[['Document', 'Valid Until', 'Days Left']])
                    else: st.info("No documents linked to this ID.")
                else: st.info("No documents found.")

            with col2:
                st.subheader("📊 Life Status")
                st.progress(int(ac_df['Life Now %']), text=f"Now: {ac_df['Life Now %']:.0f}%")
                st.progress(int(ac_df['Life Forecast %']), text=f"Forecast: {ac_df['Life Forecast %']:.0f}%")
