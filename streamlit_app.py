import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import urllib.parse
import re  
from datetime import datetime, timedelta

# --- Page Config ---
st.set_page_config(page_title="Toran Maintenance Forecast", layout="wide", page_icon="🚁")

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
    session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 'Accept': 'application/json'})
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
            url = f"{base_url.rstrip('/')}{prefix}{resource_name}"
            resp = session.get(url, timeout=10)
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

    if not c_sess or not t_sess: return None, "Auth Failed", None, pd.DataFrame()

    ac_data = []
    docs_data = []
    aircraft_registry = {} 

    # 1A. Parse Standard CAMO Maintenances (Pulling 100 items to avoid pagination cutoff)
    maint_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", "upcoming-aircraft-maintenances?perPage=100")
    if maint_json:
        for r in maint_json.get('resources', []):
            fields = {f['attribute']: f['value'] for f in r.get('fields', [])}
            
            reg_raw = str(fields.get('aircraft') or "Unknown")
            reg_display = reg_raw.split(' ')[0].strip().upper()
            reg_merge = normalize_tail(reg_display)

            # Extract internal Nova ID for the aircraft
            ac_id = None
            for f in r.get('fields', []):
                if f.get('attribute') == 'aircraft':
                    ac_id = f.get('belongsToId')
            
            if ac_id and reg_merge != "UNKNOWN":
                aircraft_registry[reg_merge] = {'id': ac_id, 'display': reg_display}

            maint_type_str = str(fields.get('aircraftMaintenanceType', "Standard Inspection"))
            maint_lower = maint_type_str.lower()

            due_date = None
            raw_date = fields.get('max_valid_until')
            if raw_date and str(raw_date).strip() not in ["", "—", "None", "null"]:
                try: 
                    parsed_date = pd.to_datetime(str(raw_date)).date()
                    if parsed_date.year > 2000: due_date = parsed_date
                except: pass

            is_doc = any(kw in maint_lower for kw in ["(official)", "airworthiness", "insurance", "arc"])
            
            if is_doc:
                docs_data.append({'Registration': reg_display, 'MergeKey': reg_merge, 'Document': maint_type_str, 'Due Date': due_date})
            else:
                try: curr_val = float(str(fields.get('current_hours_ttsn', 0)).replace(',', ''))
                except: curr_val = 0.0
                try: due_val = float(str(fields.get('max_hours', 0)).replace(',', ''))
                except: due_val = 0.0
                potential = max(0.0, due_val - curr_val) if due_val > 0 else 0.0

                try:
                    match = re.search(r'(\d+)', maint_type_str)
                    interval = float(match.group(1)) if match else 100.0
                except: interval = 100.0
                if interval <= 0: interval = 100.0 

                ac_data.append({
                    'Registration': reg_display, 'MergeKey': reg_merge, 'Current': curr_val, 
                    'Limit': due_val, 'Type': maint_type_str, 'Interval': interval, 'Potential': potential, 'Due Date': due_date
                })

    # 1B. Precision Document Fetcher with PAGINATION & "Is Active"
    for reg_merge, ac_info in aircraft_registry.items():
        page = 1
        
        while True:
            # Force 100 per page to minimize loops, and specify current page
            doc_url = f"documents?viaResource=aircraft&viaResourceId={ac_info['id']}&viaRelationship=documents&relationshipType=hasMany&perPage=100&page={page}"
            docs_json = fetch_resource(c_sess, "https://toran-camo.flightapp.be", doc_url)
            
            if not docs_json or 'resources' not in docs_json or not docs_json['resources']:
                break # Stop turning pages if the folder is empty
            
            for r in docs_json.get('resources', []):
                fields_list = r.get('fields', [])
                
                # Check "Is Active" BEFORE doing anything else
                is_active = True
                for f_item in fields_list:
                    attr = str(f_item.get('name', f_item.get('attribute', ''))).lower()
                    val = f_item.get('value')
                    
                    if 'active' in attr or 'actief' in attr:
                        # If the toggle is explicitly set to False, 0, or "No"
                        if val in [False, 0, '0', 'false', 'False', 'No', 'no', None, '']:
                            is_active = False
                            break
                            
                # If the document is history, skip it immediately!
                if not is_active:
                    continue

                # Parse the Active Document
                fields = {f['attribute']: f['value'] for f in fields_list}
                doc_name = str(fields.get('name') or fields.get('document_type') or fields.get('type') or fields.get('title') or "Official Document")
                
                due_date = None
                
                # Hunt for the ending date, explicitly avoiding "from" or "issue" dates
                for f_item in fields_list:
                    attr = str(f_item.get('attribute', '')).lower()
                    val = str(f_item.get('value', ''))
                    
                    if any(bad in attr for bad in ['from', 'issue', 'start', 'begin']):
                        continue
                        
                    if any(kw in attr for kw in ['expir', 'valid', 'until', 'due', 'end', 'to', 'validity']):
                        if val and str(val).strip() not in ["", "—", "None", "null"]:
                            try:
                                d = pd.to_datetime(val).date()
                                if d.year > 2000:
                                    due_date = d
                                    break
                            except: pass
                                
                docs_data.append({'Registration': ac_info['display'], 'MergeKey': reg_merge, 'Document': doc_name, 'Due Date': due_date})
            
            # Check if Nova tells us there is another page waiting
            if not docs_json.get('next_page_url'):
                break # We reached the last page!
                
            page += 1 # Turn to the next page and loop again

    # Rescue Protocol: Ensure every helicopter with a document doesn't get dropped if it has no hours-based inspection
    ac_merges = {d['MergeKey'] for d in ac_data}
    for d in docs_data:
        if d['MergeKey'] not in ac_merges and d['MergeKey'] != "UNKNOWN":
            ac_data.append({
                'Registration': d['Registration'], 'MergeKey': d['MergeKey'], 'Current': 0.0, 
                'Limit': 0.0, 'Type': "Monitoring Schedule", 'Interval': 100.0, 'Potential': 999.0, 'Due Date': None
            })

    df_ac = pd.DataFrame(ac_data).sort_values('Limit').drop_duplicates('MergeKey') if ac_data else pd.DataFrame()
    
    # Process Documents Dataframe
    df_docs = pd.DataFrame(docs_data)
    if not df_docs.empty:
        # Sort so the highest/furthest Due Date is at the top, drop identical documents
        df_docs = df_docs.sort_values('Due Date', ascending=False).drop_duplicates()
        
        # Calculate days left
        today_date = pd.Timestamp.now().normalize().date()
        df_docs['Days Left'] = df_docs['Due Date'].apply(lambda x: (x - today_date).days if pd.notnull(x) else None)

    # 2. Fetch Toran Bookings
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
                            if reg: book_list.append({'MergeKey': normalize_tail(reg), 'Registration': reg, 'Start': start, 'End': pd.to_datetime(f.get('reserved_end_datetime')).tz_convert(None), 'Planned': dur, 'Type': str(f.get('booking_type', 'Flight')).capitalize(), 'Details': f"{f.get('customer_first_name','')} {f.get('customer_last_name','')}".strip()})
                
                for b in week_data.get('blockings', []):
                    start = pd.to_datetime(b.get('start_datetime')).tz_convert(None)
                    if now < start <= end_dt and b.get('helis'):
                        dur = (pd.to_datetime(b.get('end_datetime')).tz_convert(None) - start).total_seconds() / 3600 * 0.85
                        for h in b.get('helis'):
                            reg = id_map.get(str(h.get('id', '')))
                            if reg: book_list.append({'MergeKey': normalize_tail(reg), 'Registration': reg, 'Start': start, 'End': pd.to_datetime(b.get('end_datetime')).tz_convert(None), 'Planned': dur, 'Type': 'Blocking', 'Details': b.get('description','')})
        except: pass

    df_books = pd.DataFrame(book_list)
    if not df_books.empty and not df_ac.empty:
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
        df = df_ac.assign(Planned=0) if not df_ac.empty else pd.DataFrame()
        if not df.empty: df['Breach Date'] = pd.NaT

    if not df.empty:
        df['Forecast'] = df['Potential'] - df['Planned']
        df['Life Now %'] = (df['Potential'] / df['Interval']) * 100
        df['Life Now %'] = df['Life Now %'].clip(lower=0, upper=100)
        df['Life Forecast %'] = (df['Forecast'] / df['Interval']) * 100
        df['Life Forecast %'] = df['Life Forecast %'].clip(lower=0, upper=100)
    
    return df, df_books, df_docs

# --- UI ---
with st.sidebar:
    try: st.image("toran_logo.png", use_container_width=True)
    except FileNotFoundError: pass
    st.markdown("<br>", unsafe_allow_html=True)
    
    default_date = datetime.today() + timedelta(days=35)
    selected_date = st.date_input("🗓️ Forecast End Date", value=default_date)
    if st.button('🔄 Refresh Data', use_container_width=True): 
        st.cache_data.clear()
        st.rerun()

st.title("Operations & Maintenance Forecast")

df, raw_books_df, df_docs = fetch_and_merge_data_v2(selected_date)

if df is not None and not df.empty:
    today = pd.Timestamp.now().normalize()
    df['Days Left'] = pd.to_numeric((pd.to_datetime(df['Due Date']) - today).dt.days, errors='coerce')

    # --- GLOBAL ALERTS ---
    for _, row in df.iterrows():
        if row['Forecast'] < 0 and row['Potential'] != 999.0: 
            if pd.notnull(row.get('Breach Date')):
                st.error(f"🛑 **GROUNDING:** {row['Registration']} will breach its hours limit on **{row['Breach Date'].strftime('%d %b %Y')}**!", icon="🛑")
            else:
                st.error(f"🛑 **GROUNDING:** {row['Registration']} breaches hours limit before {selected_date.strftime('%d %b')}!", icon="🛑")
        if pd.notnull(row['Days Left']) and 0 <= row['Days Left'] <= 14:
            st.warning(f"📅 **CALENDAR:** {row['Registration']} due in {int(row['Days Left'])} days!", icon="⚠️")
            
    if df_docs is not None and not df_docs.empty:
        for _, row in df_docs.iterrows():
            if pd.notnull(row.get('Days Left')):
                if row['Days Left'] < 0:
                    st.error(f"🚨 **DOCUMENT EXPIRED:** {row['Document']} for {row['Registration']} expired {abs(int(row['Days Left']))} days ago!", icon="🚨")
                elif 0 <= row['Days Left'] <= 30:
                    st.warning(f"📄 **DOCUMENT DUE:** {row['Document']} for {row['Registration']} expires in {int(row['Days Left'])} days!", icon="⚠️")

    st.markdown("---")

    # --- DYNAMIC TABS GENERATION ---
    tab_names = ["Fleet Overview"] + sorted(df['Registration'].unique().tolist())
    tabs = st.tabs(tab_names)

    # --- TAB 0: FLEET OVERVIEW ---
    with tabs[0]:
        st.subheader("Fleet Summary")
        styled_df = df[['Registration', 'Type', 'Current', 'Limit', 'Potential', 'Life Now %', 'Planned', 'Forecast', 'Life Forecast %', 'Due Date', 'Breach Date']].copy()
        
        # Hide 999 placeholder metrics
        styled_df.loc[styled_df['Potential'] == 999.0, ['Limit', 'Potential', 'Life Now %', 'Forecast', 'Life Forecast %']] = None
        styled_df.columns = ['Tail', 'Next Service', 'TSN', 'Limit', 'Potential', 'Life Now', 'Booked', 'Forecast', 'Life Forecast', 'Due Date', 'Est. Breach Date']
        
        st.dataframe(styled_df, 
                     column_config={
                         "Life Now": st.column_config.ProgressColumn("Life Remaining NOW", format="%.0f%%", min_value=0, max_value=100),
                         "Life Forecast": st.column_config.ProgressColumn("Life at Forecast Date", format="%.0f%%", min_value=0, max_value=100),
                         "Due Date": st.column_config.DateColumn("Due Date", format="DD MMM YYYY"),
                         "Est. Breach Date": st.column_config.DateColumn("Est. Breach Date", format="DD MMM YYYY")
                     }, hide_index=True, use_container_width=True)

        st.markdown("<br>", unsafe_allow_html=True)
        col_chart1, col_chart2 = st.columns(2)
        with col_chart1:
            st.subheader(f"Hours Remaining on {selected_date.strftime('%d %b %Y')}")
            chart_df = df[df['Potential'] != 999.0][['Registration', 'Forecast', 'Planned']].copy().set_index('Registration')
            chart_df.columns = ['Remaining Potential', 'Booked Hours']
            st.bar_chart(chart_df, color=["#E4D18C", "#666666"])
        with col_chart2:
            st.subheader("Calendar Days Remaining")
            cal_df = df.dropna(subset=['Days Left']).copy()
            if not cal_df.empty:
                cal_chart = cal_df.sort_values('Days Left')[['Registration', 'Days Left']].set_index('Registration')
                cal_chart.columns = ['Days Until Maintenance']
                st.bar_chart(cal_chart, color="#E4D18C")

    # --- TABS 1 to N: AIRCRAFT SPECIFIC DETAILS ---
    for i, tail in enumerate(tab_names[1:], start=1):
        with tabs[i]:
            st.subheader(f"Helicopter Details: {tail}")
            ac_df = df[df['Registration'] == tail].iloc[0]
            
            c1, c2, c3, c4 = st.columns(4)
            if ac_df['Potential'] == 999.0:
                c1.metric("Current TSN", "N/A")
                c2.metric("Remaining Potential Now", "N/A")
                c3.metric(f"Booked Flights", f"{ac_df['Planned']:.1f} h")
                c4.metric("Forecasted Potential", "N/A")
            else:
                c1.metric("Current TSN", f"{ac_df['Current']:.1f} h")
                c2.metric("Remaining Potential Now", f"{ac_df['Potential']:.1f} h")
                c3.metric(f"Booked Flights", f"{ac_df['Planned']:.1f} h")
                c4.metric("Forecasted Potential", f"{ac_df['Forecast']:.1f} h", delta=f"{ac_df['Forecast'] - ac_df['Potential']:.1f}h")
            
            st.markdown("---")
            col_info, col_bar = st.columns([1, 1])
            with col_info:
                st.write(f"**🛠️ Next Scheduled Service:** {ac_df['Type']}")
                if ac_df['Potential'] != 999.0: st.write(f"**⏱️ Inspection Interval:** {ac_df['Interval']} hours")
                
                if pd.notnull(ac_df['Due Date']):
                    days_text = f"(in {int(ac_df['Days Left'])} days)" if ac_df['Days Left'] >= 0 else "(EXPIRED)"
                    st.write(f"**📅 Calendar Due Date:** {ac_df['Due Date'].strftime('%d %b %Y')} {days_text}")
                else: st.write("**📅 Calendar Due Date:** No date set")
                    
                if pd.notnull(ac_df.get('Breach Date')): st.write(f"**🚨 Flight Hours Breach Date:** {ac_df['Breach Date'].strftime('%d %b %Y')} (Based on bookings)")
                elif ac_df['Potential'] == 999.0: st.write("**✅ Flight Hours Breach Date:** N/A")
                else: st.write("**✅ Flight Hours Breach Date:** No breach scheduled")
                    
            with col_bar:
                if ac_df['Potential'] != 999.0:
                    st.write("**Life Remaining NOW:**")
                    st.progress(int(ac_df['Life Now %']), text=f"{ac_df['Life Now %']:.0f}% (Before scheduled flights)")
                    st.write(f"**Life at Forecast Date ({selected_date.strftime('%d %b')}):**")
                    st.progress(int(ac_df['Life Forecast %']), text=f"{ac_df['Life Forecast %']:.0f}% (After scheduled flights)")

            # --- DOCUMENTS SECTION ---
            st.markdown("<br>", unsafe_allow_html=True)
            st.subheader("📄 Tracked Documents & Expirations")
            if df_docs is not None and not df_docs.empty:
                ac_docs = df_docs[df_docs['MergeKey'] == normalize_tail(tail)].copy()
                if not ac_docs.empty:
                    # Sort by days left (closest to expiring at top)
                    ac_docs = ac_docs.sort_values('Days Left', na_position='last')
                    
                    def get_status(x):
                        if pd.isnull(x): return "❔ NO DATE"
                        if x < 0: return "🚨 EXPIRED"
                        if x <= 30: return "⚠️ DUE SOON"
                        return "✅ VALID"
                        
                    ac_docs['Status'] = ac_docs['Days Left'].apply(get_status)
                    ac_docs['Due Date'] = ac_docs['Due Date'].apply(lambda x: x.strftime('%d %b %Y') if pd.notnull(x) else "Unknown")
                    ac_docs['Days Left'] = ac_docs['Days Left'].apply(lambda x: f"{int(x)} days" if pd.notnull(x) else "-")
                    
                    def style_docs(row):
                        if "🚨" in row['Status']: return ['background-color: rgba(255, 74, 43, 0.2)'] * len(row)
                        elif "⚠️" in row['Status']: return ['background-color: rgba(228, 209, 140, 0.3)'] * len(row)
                        return [''] * len(row)
                        
                    st.dataframe(ac_docs[['Document', 'Due Date', 'Days Left', 'Status']].style.apply(style_docs, axis=1), hide_index=True, use_container_width=True)
                else: st.info(f"No official documents tracked for {tail}.")
            else: st.info("No document data available.")

            # --- FLIGHTS SECTION ---
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
                    
                    def style_rows(row):
                        return ['background-color: rgba(255, 74, 43, 0.2)'] * len(row) if "🚨" in row['Status'] else [''] * len(row)
                    
                    st.dataframe(detail_df[['Date', 'Time (UTC)', 'Type', 'Details', 'Flight Time', 'Total Used', 'Status']].style.apply(style_rows, axis=1), hide_index=True, use_container_width=True)
                else: st.info(f"No flights or blockings scheduled for {tail} before {selected_date.strftime('%d %b %Y')}.")
            else: st.info("No schedule data available.")

    with st.sidebar:
        st.markdown("---")
        csv_data = convert_df_to_csv(df[['Registration', 'Type', 'Current', 'Limit', 'Potential', 'Planned', 'Forecast', 'Due Date', 'Breach Date']])
        st.download_button("📥 Download Summary (CSV)", csv_data, f"Fleet_Forecast_{selected_date}.csv", "text/csv", use_container_width=True)
