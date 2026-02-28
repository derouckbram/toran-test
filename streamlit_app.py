import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import urllib.parse
import re
from datetime import datetime, timedelta

# --- Page Config ---
st.set_page_config(page_title="Toran Operations Center", layout="wide", page_icon="🚁")

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
@st.cache_data(ttl=60)
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

    # --- PILOT DIRECTORY FETCH ---
    pilot_map = {}
    try:
        pilot_resp = t_sess.get("https://admin.toran.be/api/pilots?page_size=100", timeout=10)
        if pilot_resp.status_code == 200:
            p_data = pilot_resp.json()
            p_list = p_data.get('data', p_data.get('items', [])) if isinstance(p_data, dict) else p_data
            for p in p_list:
                if isinstance(p, dict):
                    p_id = str(p.get('id', ''))
                    p_name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
                    if not p_name: p_name = str(p.get('name', ''))
                    if p_id and p_name: pilot_map[p_id] = p_name
    except: pass

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
                            
                            guest_name = f"{f.get('customer_first_name','')} {f.get('customer_last_name','')}".strip()
                            if not guest_name and isinstance(f.get('customer'), dict):
                                guest_name = f"{f.get('customer').get('first_name','')} {f.get('customer').get('last_name','')}".strip()
                            if not guest_name and f.get('title'):
                                guest_name = str(f.get('title'))
                                
                            instructor_name = ""
                            for k in ['pilot_name', 'instructor_name', 'pic_name', 'crew_name']:
                                if isinstance(f.get(k), str) and f.get(k).strip() and f.get(k).lower() != 'none':
                                    instructor_name = f.get(k).strip()
                                    break
                            if not instructor_name:
                                for k in ['pilot', 'instructor', 'pic', 'user']:
                                    if isinstance(f.get(k), dict):
                                        name = f"{f[k].get('first_name', '')} {f[k].get('last_name', '')}".strip()
                                        if not name: name = str(f[k].get('name', ''))
                                        if name and name.lower() != 'none':
                                            instructor_name = name
                                            break
                            if not instructor_name:
                                for k in ['pilot_id', 'instructor_id', 'pic_id', 'user_id', 'assigned_user_id']:
                                    val = str(f.get(k, ''))
                                    if val in pilot_map:
                                        instructor_name = pilot_map[val]
                                        break
                            if not instructor_name or instructor_name.lower() in ['none', 'nan', '', 'null']:
                                instructor_name = 'Toran Team'
                            
                            if reg: 
                                book_list.append({
                                    'MergeKey': normalize_tail(reg), 
                                    'Registration': reg, 
                                    'Start': start, 
                                    'End': pd.to_datetime(f.get('reserved_end_datetime')).tz_convert(None), 
                                    'Planned': dur, 
                                    'Type': str(f.get('booking_type', 'Flight')).capitalize(), 
                                    'Details': guest_name,
                                    'Instructor': instructor_name
                                })
                
                for b in week_data.get('blockings', []):
                    start = pd.to_datetime(b.get('start_datetime')).tz_convert(None)
                    if now < start <= end_dt and b.get('helis'):
                        dur = (pd.to_datetime(b.get('end_datetime')).tz_convert(None) - start).total_seconds() / 3600 * 0.85
                        for h in b.get('helis'):
                            reg = id_map.get(str(h.get('id', '')))
                            if reg: book_list.append({'MergeKey': normalize_tail(reg), 'Registration': reg, 'Start': start, 'End': pd.to_datetime(b.get('end_datetime')).tz_convert(None), 'Planned': dur, 'Type': 'Blocking', 'Details': b.get('description',''), 'Instructor': '-'})
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
    
    # --- FIXED: Use (Limit - Current) / Interval as requested ---
    df['Life Now %'] = ((df['Limit'] - df['Current']) / df['Interval']) * 100
    df['Life Now %'] = df['Life Now %'].clip(lower=0, upper=100)
    
    df['Life Forecast %'] = (df['Forecast'] / df['Interval']) * 100
    df['Life Forecast %'] = df['Life Forecast %'].clip(lower=0, upper=100)
    
    return df, df_books, df_defects

# --- UI Sidebar & Mode Tracking ---
with st.sidebar:
    try: st.image("toran_logo.png", use_container_width=True)
    except FileNotFoundError: pass 
    st.markdown("<br>", unsafe_allow_html=True)
    
    default_date = datetime.today() + timedelta(days=35)
    selected_date = st.date_input("🗓️ Forecast End Date", value=default_date)
    if st.button('🔄 Refresh Data', use_container_width=True): 
        st.cache_data.clear()
        st.rerun()

df, raw_books_df, df_defects = fetch_and_merge_data_v2(selected_date)

# ==========================================
# DASHBOARD LOGIC
# ==========================================
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
                    display_cols = ['Date', 'Time (UTC)', 'Type', 'Details', 'Instructor', 'Flight Time', 'Total Used', 'Status']
                    def style_rows(row): return ['background-color: rgba(255, 74, 43, 0.2)'] * len(row) if "🚨" in row['Status'] else [''] * len(row)
                    st.dataframe(detail_df[display_cols].style.apply(style_rows, axis=1), hide_index=True, use_container_width=True)
                else: st.info(f"No flights or blockings scheduled for {tail} before {selected_date.strftime('%d %b %Y')}.")

    with st.sidebar:
        st.markdown("---")
        csv_data = convert_df_to_csv(df[['Registration', 'Type', 'Current', 'Limit', 'Potential', 'Planned', 'Forecast', 'Due Date', 'Breach Date']])
        st.download_button("📥 Download Summary (CSV)", csv_data, f"Fleet_Forecast_{selected_date}.csv", "text/csv", use_container_width=True)
