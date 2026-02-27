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

# --- Weather Setup for Welcome Screen ---
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
    "OOHXP": {"model": "Robinson R44 Raven II", "image": "raven2.jpg", "seats": "4 Seats", "cruise": "109 kts"},
    "OOMOO": {"model": "Robinson R44 Raven I", "image": "raven1.jpg", "seats": "4 Seats", "cruise": "109 kts"},
    "OOSKH": {"model": "Guimbal Cabri G2", "image": "cabri.jpg", "seats": "2 Seats", "cruise": "90 kts"}
}

# --- Toran Official Brand Style Engine ---
def apply_toran_style():
    st.markdown(
        """
        <style>
        .stApp { background-color: #FFFFFF; font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #000000; }
        .stTabs [data-baseweb="tab-list"] { gap: 8px; background-color: transparent; }
        .stTabs [data-baseweb="tab"] { background-color: #FFFFFF; border-radius: 4px !important; padding: 10px 20px !important; border: 1px solid #999999; color: #666666; font-weight: 600; transition: all 0.2s ease; }
        .stTabs [data-baseweb="tab"]:hover { border-color: #D8BF95; color: #000000; }
        .stTabs [aria-selected="true"] { background-color: #E4D18C !important; color: #000000 !important; border: 1px solid #E4D18C !important; }
        [data-testid="metric-container"] { background-color: #FFFFFF; border: 1px solid #999999; border-radius: 8px; padding: 20px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); border-left: 5px solid #E4D18C; }
        [data-testid="stMetricValue"] { font-size: 34px !important; font-weight: 800 !important; color: #000000 !important; }
        [data-testid="stMetricLabel"] { font-size: 15px !important; font-weight: 600 !important; color: #666666 !important; text-transform: uppercase; }
        [data-testid="stDataFrame"] { background-color: #FFFFFF; border-radius: 8px; padding: 15px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); border: 1px solid #999999; }
        h1, h2, h3 { color: #000000 !important; font-weight: 800 !important; }
        [data-testid="stSidebar"] { background-color: #F8F8F8; border-right: 1px solid #999999; }
        .stProgress > div > div > div > div { background-color: #E4D18C !important; border-radius: 4px; }
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

    ac_data = []
    aircraft_registry = {}
    
    for r in maint_json.get('resources', []):
        fields = {f['attribute']: f['value'] for f in r.get('fields', [])}
        reg_raw = str(fields.get('aircraft') or "Unknown")
        reg_display = reg_raw.split(' ')[0].strip().upper()
        reg_merge = normalize_tail(reg_display)

        ac_id = None
        for f in r.get('fields', []):
            if f.get('attribute') == 'aircraft': ac_id = f.get('belongsToId')
        if ac_id and reg_merge != "UNKNOWN": aircraft_registry[reg_merge] = ac_id

        try: curr_val = float(str(fields.get('current_hours_ttsn', 0)).replace(',', ''))
        except: curr_val = 0.0
        try: due_val = float(str(fields.get('max_hours', 0)).replace(',', ''))
        except: due_val = 0.0
        potential = max(0.0, due_val - curr_val) if due_val > 0 else 0.0

        maint_type_str = str(fields.get('aircraftMaintenanceType', "Standard Inspection"))
        try: interval = float(re.search(r'(\d+)', maint_type_str).group(1))
        except: interval = 100.0
        if interval <= 0: interval = 100.0 

        due_date = None
        raw_date = fields.get('max_valid_until')
        if raw_date and str(raw_date).strip() not in ["", "—", "None", "null"]:
            try: 
                parsed_date = pd.to_datetime(str(raw_date)).date()
                if parsed_date.year > 2000: due_date = parsed_date
            except: pass

        ac_data.append({
            'Registration': reg_display, 'MergeKey': reg_merge, 'Current': curr_val, 
            'Limit': due_val, 'Type': maint_type_str, 'Interval': interval, 'Potential': potential, 'Due Date': due_date
        })
    df_ac = pd.DataFrame(ac_data).sort_values('Limit').drop_duplicates('MergeKey')

    # --- DEFECT VACUUM ---
    defects_list = []
    for endpoint in ['ddl-defects', 'hil-defects']:
        page = 1
        while True:
            d_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", f"{endpoint}?perPage=100&page={page}")
            if not d_json or 'resources' not in d_json or not d_json['resources']: break
                
            for r in d_json.get('resources', []):
                index_fields = {f['attribute']: f['value'] for f in r.get('fields', [])}
                defect_id = r.get('id', {}).get('value') if isinstance(r.get('id'), dict) else r.get('id')
                
                reg_raw = str(index_fields.get('aircraft') or index_fields.get('helicopter') or index_fields.get('registration') or "")
                if isinstance(index_fields.get('aircraft'), dict): reg_raw = index_fields.get('aircraft').get('display', reg_raw)
                if not reg_raw or reg_raw == "None": continue
                    
                reg_display = reg_raw.split(' ')[0].strip().upper()
                reg_merge = normalize_tail(reg_display)
                
                status_val = str(index_fields.get('status', 'Open')).strip().lower()
                active_val = index_fields.get('active', index_fields.get('is_active', True))
                if status_val in ['closed', 'gesloten', 'resolved', 'done', 'fixed', 'inactive'] or str(active_val).lower() in ['false', '0', 'no', 'none']: continue

                defect_name = str(r.get('title') or index_fields.get('name') or defect_id)
                desc_clean = "No description provided."
                due_clean = None
                
                if defect_id:
                    det_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", f"{endpoint}/{defect_id}")
                    if det_json and 'resource' in det_json:
                        det_fields = {f['attribute']: f['value'] for f in det_json['resource'].get('fields', [])}
                        for k in ['description', 'defect', 'discrepancy', 'squawk', 'remarks', 'finding', 'fault', 'details', 'text', 'symptom', 'beschrijving', 'klacht', 'probleem']:
                            if det_fields.get(k) and str(det_fields.get(k)).strip() not in ["", "None", "null"]:
                                desc_clean = re.sub(r'<[^>]+>', '', str(det_fields.get(k))).strip() 
                                break
                        for d_key in ['ultimate_repair_date', 'due_date', 'limit_date', 'limit', 'expiration_date', 'target_date']:
                            val = det_fields.get(d_key)
                            if val and str(val).strip() not in ["", "—", "None", "null"]:
                                try:
                                    d = pd.to_datetime(str(val)).date()
                                    if d.year > 2000:
                                        due_clean = d
                                        break
                                except: pass

                    if not due_clean:
                        l_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", f"defect-limitations?viaResource={endpoint}&viaResourceId={defect_id}&viaRelationship=defectLimitations&relationshipType=hasMany")
                        if l_json and 'resources' in l_json:
                            for lr in l_json.get('resources', []):
                                l_fields = {f['attribute']: f['value'] for f in lr.get('fields', [])}
                                for d_key in ['ultimate_repair_date', 'due_date', 'date', 'limit_date', 'limit', 'valid_until']:
                                    val = l_fields.get(d_key)
                                    if val and str(val).strip() not in ["", "—", "None", "null"]:
                                        try:
                                            d = pd.to_datetime(str(val)).date()
                                            if d.year > 2000:
                                                due_clean = d
                                                break
                                        except: pass
                                if due_clean: break
                
                defects_list.append({
                    'MergeKey': reg_merge, 'ID': defect_name, 'Type': "DDL" if 'ddl' in endpoint else "HIL",
                    'Status': str(index_fields.get('status', 'Open')).capitalize(), 'Description': desc_clean, 'Due Date': due_clean
                })
            if not d_json.get('next_page_url'): break
            page += 1
    df_defects = pd.DataFrame(defects_list)

    # 3. Fetch Toran Bookings 
    xsrf_cookie = t_sess.cookies.get('XSRF-TOKEN')
    if xsrf_cookie: t_sess.headers.update({'X-XSRF-TOKEN': urllib.parse.unquote(xsrf_cookie), 'Referer': 'https://admin.toran.be/planning', 'Accept': 'application/json'})

    now = pd.Timestamp.utcnow().tz_localize(None)
    end_dt = pd.to_datetime(end_date).replace(hour=23, minute=59, second=59)
    days_diff = (end_dt - now).days
    weeks_to_fetch = max(1, (days_diff // 7) + 2) if days_diff >= 0 else 0

    book_list = []
    id_map = {}
    
    for i in range(weeks_to_fetch): 
        target_date = now + pd.Timedelta(weeks=i)
        api_url = f"https://admin.toran.be/api/planning?week={target_date.isocalendar()[1]}&year={target_date.isocalendar()[0]}"
        try:
            resp = t_sess.get(api_url, timeout=15)
            if resp.status_code == 200:
                week_data = resp.json()
                for h in week_data.get('helis', []): id_map[str(h.get('id', ''))] = h.get('title', '').upper()
                
                for f in week_data.get('entries', []):
                    if f.get('status') == 'confirmed':
                        start = pd.to_datetime(f.get('reserved_start_datetime')).tz_convert(None)
                        if now < start <= end_dt:
                            dur = (pd.to_datetime(f.get('reserved_end_datetime')).tz_convert(None) - start).total_seconds() / 3600 * 0.85
                            reg = id_map.get(str(f.get('heli_id', '')))
                            if reg: 
                                book_list.append({
                                    'MergeKey': normalize_tail(reg), 
                                    'Registration': reg, 
                                    'Start': start, 
                                    'End': pd.to_datetime(f.get('reserved_end_datetime')).tz_convert(None), 
                                    'Planned': dur, 
                                    'Type': str(f.get('booking_type', 'Flight')).capitalize(), 
                                    'Details': f"{f.get('customer_first_name','')} {f.get('customer_last_name','')}".strip()
                                })
                
                for b in week_data.get('blockings', []):
                    start = pd.to_datetime(b.get('start_datetime')).tz_convert(None)
                    if now < start <= end_dt and b.get('helis'):
                        dur = (pd.to_datetime(b.get('end_datetime')).tz_convert(None) - start).total_seconds() / 3600 * 0.85
                        for h in b.get('helis'):
                            reg = id_map.get(str(h.get('id', '')))
                            if reg: book_list.append({'MergeKey': normalize_tail(reg), 'Registration': reg, 'Start': start, 'End': pd.to_datetime(b.get('end_datetime')).tz_convert(None), 'Planned': dur, 'Type': 'Blocking', 'Details': b.get('description','')})
        except: pass

    df_books = pd.DataFrame(book_list)
    if not df_books.empty:
        df_books = df_books.sort_values(by=['MergeKey', 'Start'])
        df_books['Cumulative'] = df_books.groupby('MergeKey')['Planned'].cumsum()
        df_books = pd.merge(df_books, df_ac[['MergeKey', 'Potential']], on='MergeKey', how='left')
        df_books['Is_Breach'] = df_books['Cumulative'] > df_books['Potential']
        
        breach_dates = df_books[df_books['Is_Breach']].groupby('MergeKey')['Start'].min().reset_index()
        breach_dates.rename(columns={'Start': 'Breach Date'}, inplace=True)
        
        usage = df_books.groupby('MergeKey')['Planned'].sum().reset_index()
        df = pd.merge(df_ac, usage, on='MergeKey', how='left').fillna({'Planned': 0})
        df = pd.merge(df, breach_dates, on='MergeKey', how='left')
    else:
        df = df_ac.assign(Planned=0)
        df['Breach Date'] = pd.NaT

    df['Forecast'] = df['Potential'] - df['Planned']
    df['Life Now %'] = (df['Potential'] / df['Interval']) * 100
    df['Life Now %'] = df['Life Now %'].clip(lower=0, upper=100)
    df['Life Forecast %'] = (df['Forecast'] / df['Interval']) * 100
    df['Life Forecast %'] = df['Life Forecast %'].clip(lower=0, upper=100)
    
    return df, df_books, df_defects

# --- UI Sidebar ---
with st.sidebar:
    try: st.image("toran_logo.png", use_container_width=True)
    except FileNotFoundError: pass 
    st.markdown("<br>", unsafe_allow_html=True)
    
    app_mode = st.radio("🖥️ Select Display Mode", ["Maintenance Dashboard", "Guest Welcome Screen"])
    st.markdown("---")
    
    default_date = datetime.today() + timedelta(days=35)
    selected_date = st.date_input("🗓️ Forecast End Date", value=default_date)
    if st.button('🔄 Refresh Data', use_container_width=True): 
        st.cache_data.clear()
        st.rerun()

df, raw_books_df, df_defects = fetch_and_merge_data_v2(selected_date)


# ==========================================
# MODE 1: MAINTENANCE DASHBOARD
# ==========================================
if app_mode == "Maintenance Dashboard":
    st.title("Operations & Maintenance Forecast")

    if df is not None:
        today = pd.Timestamp.now().normalize()
        df['Days Left'] = pd.to_numeric((pd.to_datetime(df['Due Date']) - today).dt.days, errors='coerce')

        for _, row in df.iterrows():
            if row['Forecast'] < 0: 
                if pd.notnull(row.get('Breach Date')):
                    st.error(f"🛑 **GROUNDING:** {row['Registration']} will breach its hours limit on **{row['Breach Date'].strftime('%d %b %Y')}**!", icon="🛑")
                else:
                    st.error(f"🛑 **GROUNDING:** {row['Registration']} breaches hours limit before {selected_date.strftime('%d %b')}!", icon="🛑")
            if pd.notnull(row['Days Left']):
                if 0 <= row['Days Left'] <= 14: st.warning(f"📅 **CALENDAR:** {row['Registration']} due in {int(row['Days Left'])} days!", icon="⚠️")

        st.markdown("---")

        tab_names = ["Fleet Overview"] + sorted(df['Registration'].unique().tolist())
        tabs = st.tabs(tab_names)

        with tabs[0]:
            st.subheader("Fleet Summary")
            styled_df = df[['Registration', 'Type', 'Current', 'Limit', 'Potential', 'Life Now %', 'Planned', 'Forecast', 'Life Forecast %', 'Due Date', 'Breach Date']].copy()
            styled_df.columns = ['Tail', 'Next Service', 'TSN', 'Limit', 'Potential', 'Life Now', 'Booked', 'Forecast', 'Life Forecast', 'Due Date', 'Est. Breach Date']
            
            st.dataframe(styled_df, 
                         column_config={
                             "Life Now": st.column_config.ProgressColumn("Life Remaining NOW", format="%.0f%%", min_value=0, max_value=100),
                             "Life Forecast": st.column_config.ProgressColumn("Life at Forecast Date", format="%.0f%%", min_value=0, max_value=100),
                             "Due Date": st.column_config.DateColumn("Due Date", format="DD MMM YYYY"),
                             "Est. Breach Date": st.column_config.DateColumn("Est. Breach Date", format="DD MMM YYYY")
                         }, hide_index=True, use_container_width=True)

            col_chart1, col_chart2 = st.columns(2)
            with col_chart1:
                st.subheader(f"Hours Remaining on {selected_date.strftime('%d %b %Y')}")
                chart_df = df[['Registration', 'Forecast', 'Planned']].copy().set_index('Registration')
                chart_df.columns = ['Remaining Potential', 'Booked Hours']
                st.bar_chart(chart_df, color=["#E4D18C", "#666666"])
            with col_chart2:
                st.subheader("Calendar Days Remaining")
                cal_df = df.dropna(subset=['Days Left']).copy()
                if not cal_df.empty:
                    cal_chart = cal_df.sort_values('Days Left')[['Registration', 'Days Left']].set_index('Registration')
                    cal_chart.columns = ['Days Until Maintenance']
                    st.bar_chart(cal_chart, color="#E4D18C")

        for i, tail in enumerate(tab_names[1:], start=1):
            with tabs[i]:
                st.subheader(f"Helicopter Details: {tail}")
                ac_df = df[df['Registration'] == tail].iloc[0]
                
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Current TSN", f"{ac_df['Current']:.1f} h")
                c2.metric("Remaining Potential Now", f"{ac_df['Potential']:.1f} h")
                c3.metric(f"Booked Flights", f"{ac_df['Planned']:.1f} h")
                c4.metric("Forecasted Potential", f"{ac_df['Forecast']:.1f} h", delta=f"{ac_df['Forecast'] - ac_df['Potential']:.1f}h")
                st.markdown("---")
                
                col_info, col_bar = st.columns([1, 1])
                with col_info:
                    st.write(f"**🛠️ Next Scheduled Service:** {ac_df['Type']}")
                    st.write(f"**⏱️ Inspection Interval:** {ac_df['Interval']} hours")
                    if pd.notnull(ac_df['Due Date']): st.write(f"**📅 Calendar Due Date:** {ac_df['Due Date'].strftime('%d %b %Y')}")
                    else: st.write("**📅 Calendar Due Date:** No date set")
                    if pd.notnull(ac_df.get('Breach Date')): st.write(f"**🚨 Flight Hours Breach Date:** {ac_df['Breach Date'].strftime('%d %b %Y')}")
                    else: st.write("**✅ Flight Hours Breach Date:** No breach scheduled")
                        
                with col_bar:
                    st.write("**Life Remaining NOW:**")
                    st.progress(int(ac_df['Life Now %']), text=f"{ac_df['Life Now %']:.0f}% (Before scheduled flights)")
                    st.write(f"**Life at Forecast Date ({selected_date.strftime('%d %b')}):**")
                    st.progress(int(ac_df['Life Forecast %']), text=f"{ac_df['Life Forecast %']:.0f}% (After scheduled flights)")

                st.markdown("<br>", unsafe_allow_html=True)
                st.subheader("🛠️ Open Defects (HIL / DDL)")
                if df_defects is not None and not df_defects.empty:
                    ac_defects = df_defects[df_defects['MergeKey'] == normalize_tail(tail)].copy()
                    if not ac_defects.empty:
                        ac_defects['Due Date'] = ac_defects['Due Date'].apply(lambda x: x.strftime('%d %b %Y') if pd.notnull(x) else "No Limit")
                        def style_defects(row): return ['background-color: rgba(255, 74, 43, 0.1)'] * len(row)
                        st.dataframe(ac_defects[['ID', 'Type', 'Status', 'Due Date', 'Description']].style.apply(style_defects, axis=1), hide_index=True, use_container_width=True)
                    else: st.info(f"✅ No open HIL or DDL defects for {tail}.")
                else: st.info(f"✅ No open HIL or DDL defects for {tail}.")

                st.markdown("<br>", unsafe_allow_html=True)
                st.subheader("📋 Scheduled Flights & Blockings")
                if not raw_books_df.empty:
                    detail_df = raw_books_df[raw_books_df['MergeKey'] == normalize_tail(tail)].copy()
                    if not detail_df.empty:
                        detail_df['Date'] = detail_df['Start'].dt.strftime('%d %b %Y')
                        detail_df['Time (UTC)'] = detail_df['Start'].dt.strftime('%H:%M') + " - " + detail_df['End'].dt.strftime('%H:%M')
                        detail_df['Flight Time'] = detail_df['Planned'].apply(lambda x: f"{x:.1f}h")
                        detail_df['Total Used'] = detail_df['Cumulative'].apply(lambda x: f"{x:.1f}h")
                        detail_df['Status'] = detail_df['Is_Breach'].apply(lambda x: "🚨 BREACH" if x else "✅ OK")
                        display_cols = ['Date', 'Time (UTC)', 'Type', 'Details', 'Flight Time', 'Total Used', 'Status']
                        def style_rows(row): return ['background-color: rgba(255, 74, 43, 0.2)'] * len(row) if "🚨" in row['Status'] else [''] * len(row)
                        st.dataframe(detail_df[display_cols].style.apply(style_rows, axis=1), hide_index=True, use_container_width=True)
                    else: st.info(f"No flights or blockings scheduled for {tail} before {selected_date.strftime('%d %b %Y')}.")

        with st.sidebar:
            st.markdown("---")
            csv_data = convert_df_to_csv(df[['Registration', 'Type', 'Current', 'Limit', 'Potential', 'Planned', 'Forecast', 'Due Date', 'Breach Date']])
            st.download_button("📥 Download Summary (CSV)", csv_data, f"Fleet_Forecast_{selected_date}.csv", "text/csv", use_container_width=True)


# ==========================================
# MODE 2: GUEST WELCOME SCREEN
# ==========================================
elif app_mode == "Guest Welcome Screen":
    st.markdown("""
        <meta http-equiv="refresh" content="300">
        <style>
        [data-testid="collapsedControl"] { display: none; }
        [data-testid="stSidebar"] { display: none; }
        header { display: none !important; }
        .welcome-title { font-size: 70px; font-weight: 900; color: #000000; line-height: 1.1; margin-bottom: 10px; }
        .welcome-subtitle { font-size: 35px; font-weight: 600; color: #666666; margin-bottom: 40px; }
        .clock-text { font-size: 50px; font-weight: 800; color: #E4D18C; text-align: right; }
        .info-card { background-color: #F8F8F8; border-left: 10px solid #E4D18C; padding: 30px; border-radius: 12px; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1); margin-bottom: 30px; }
        .info-card h3 { font-size: 35px; margin: 0 0 10px 0; color: #000000; }
        .info-card p { font-size: 25px; margin: 0; color: #666666; }
        .weather-card { background-color: #000000; color: #FFFFFF; padding: 30px; border-radius: 12px; text-align: center; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.2); }
        .weather-temp { font-size: 60px; font-weight: 800; color: #E4D18C; }
        .weather-desc { font-size: 25px; color: #999999; }
        .flight-board { width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 22px; }
        .flight-board th { background-color: #E4D18C; color: #000000; padding: 15px; text-align: left; font-weight: 800; }
        .flight-board td { padding: 15px; border-bottom: 1px solid #E2E8F0; color: #666666; font-weight: 600; }
        .flight-board tr:nth-child(even) { background-color: #F8F8F8; }
        .active-flight td { background-color: rgba(228, 209, 140, 0.2) !important; color: #000000; font-weight: 800; }
        .clickable-logo { transition: opacity 0.2s ease; }
        .clickable-logo:hover { opacity: 0.8; }
        </style>
    """, unsafe_allow_html=True)

    now_be = pd.Timestamp.now('Europe/Brussels')

    head_col1, head_col2 = st.columns([1, 1])
    with head_col1:
        try:
            with open("toran_logo.png", "rb") as image_file:
                encoded_logo = base64.b64encode(image_file.read()).decode()
            logo_html = f'<a href="/" target="_self"><img class="clickable-logo" src="data:image/png;base64,{encoded_logo}" width="300" title="Return to Dashboard"></a>'
            st.markdown(logo_html, unsafe_allow_html=True)
        except FileNotFoundError:
            st.markdown('<a href="/" target="_self" style="text-decoration:none;"><h1 style="color:#000000;" class="clickable-logo">TORAN HELICOPTERS</h1></a>', unsafe_allow_html=True)
    with head_col2:
        st.markdown(f'<div class="clock-text">{now_be.strftime("%H:%M")} Local</div>', unsafe_allow_html=True)

    st.markdown("<br><br>", unsafe_allow_html=True)

    today_flights = pd.DataFrame()
    active_flight = None

    if not raw_books_df.empty:
        raw_books_df['Local_Start'] = raw_books_df['Start'].dt.tz_localize('UTC').dt.tz_convert('Europe/Brussels')
        raw_books_df['Local_End'] = raw_books_df['End'].dt.tz_localize('UTC').dt.tz_convert('Europe/Brussels')
        
        # --- THE FIX: Include everything that is NOT a "Blocking" ---
        today_flights = raw_books_df[(raw_books_df['Local_Start'].dt.date == now_be.date()) & (raw_books_df['Type'] != 'Blocking')].copy()
        
        if not today_flights.empty:
            today_flights = today_flights.sort_values('Local_Start')
            for _, f in today_flights.iterrows():
                if (f['Local_End'] - now_be).total_seconds() >= -1800:
                    active_flight = f
                    break

    guest_name = ""
    if active_flight is not None:
        guest_name = str(active_flight['Details']).strip()
        if not guest_name or guest_name.lower() in ['nan', 'none']:
            guest_name = "Guest"

    if active_flight is not None:
        st.markdown(f'<div class="welcome-title">Welcome, {guest_name}!</div>', unsafe_allow_html=True)
        col1, col2 = st.columns([1.5, 1])
        with col1:
            st.markdown(f"""
                <div class="info-card">
                    <h3>🚁 Your Flight Details</h3>
                    <p><b>Time:</b> {active_flight['Local_Start'].strftime('%H:%M')} Local</p>
                    <p><b>Aircraft:</b> {active_flight['Registration']}</p>
                    <p><b>Instructor:</b> {active_flight.get('Instructor', 'Toran Team')}</p>
                </div>
            """, unsafe_allow_html=True)
        with col2:
            tail_clean = normalize_tail(active_flight['Registration'])
            ac_info = AIRCRAFT_DB.get(tail_clean, None)
            if ac_info:
                try: st.image(ac_info['image'], use_container_width=True, caption=active_flight['Registration'])
                except FileNotFoundError: pass
            else: st.markdown("<br>", unsafe_allow_html=True)
                
            st.markdown(f"""
                <div class="weather-card" style="margin-top: 20px;">
                    <div style="font-size: 20px; font-weight: bold; color: #E4D18C;">EBKT WEATHER</div>
                    <div class="weather-temp">{weather['temp']}</div>
                    <div class="weather-desc">Wind: {weather['wind']} at {weather['dir']}</div>
                </div>
            """, unsafe_allow_html=True)
    else:
        st.markdown('<div class="welcome-title">Welcome to Toran Helicopters</div>', unsafe_allow_html=True)
        st.markdown('<div class="welcome-subtitle">Aviation Excellence in Kortrijk</div>', unsafe_allow_html=True)

    if not today_flights.empty:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<div style="font-size: 30px; font-weight: 800; color: #000000; border-bottom: 4px solid #E4D18C; display: inline-block; margin-bottom: 10px;">TODAY\'S DEPARTURES</div>', unsafe_allow_html=True)
        
        board_html = '<table class="flight-board"><tr><th>Time</th><th>Aircraft</th><th>Pilot / Student</th><th>Instructor</th></tr>'
        for _, f in today_flights.iterrows():
            row_class = 'class="active-flight"' if (active_flight is not None and active_flight.equals(f)) else ''
            
            board_guest = str(f["Details"]).strip()
            if not board_guest or board_guest.lower() in ['nan', 'none']: board_guest = "Guest"
                
            board_html += f'<tr {row_class}><td>{f["Local_Start"].strftime("%H:%M")}</td><td>{f["Registration"]}</td><td>{board_guest}</td><td>{f.get("Instructor", "-")}</td></tr>'
        board_html += '</table>'
        st.markdown(board_html, unsafe_allow_html=True)
    else:
        st.markdown("<br>", unsafe_allow_html=True)
        empty_col1, empty_col2 = st.columns([1.5, 1])
        with empty_col1:
            try: st.image("raven2.jpg", use_container_width=True)
            except FileNotFoundError: st.info("Upload 'raven2.jpg' to GitHub to display an image here!")
        with empty_col2:
            st.markdown(f"""
                <div class="weather-card">
                    <div style="font-size: 20px; font-weight: bold; color: #E4D18C;">EBKT WEATHER</div>
                    <div class="weather-temp">{weather['temp']}</div>
                    <div class="weather-desc">Wind: {weather['wind']} at {weather['dir']}</div>
                </div>
            """, unsafe_allow_html=True)
            st.markdown(f"""
                <div class="info-card" style="text-align: center; margin-top:20px;">
                    <h3>Our Fleet</h3>
                    <p>Robinson R44 Raven I</p>
                    <p>Robinson R44 Raven II</p>
                    <p>Guimbal Cabri G2</p>
                </div>
            """, unsafe_allow_html=True)
