import streamlit as st
import requests
import pandas as pd
from bs4 import BeautifulSoup

# --- Page Config ---
st.set_page_config(page_title="Toran Data Hunter", layout="wide", page_icon="🕵️")

# --- AUTHENTICATION ---
def get_session():
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
    
    # Login to CAMO
    try:
        login_url = "https://toran-camo.flightapp.be/admin/login"
        r = session.get(login_url, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        token = soup.find('meta', {'name': 'csrf-token'}).get('content')
        payload = {
            "_token": token,
            "email": st.secrets["CAMO_EMAIL"],
            "password": st.secrets["CAMO_PASS"],
            "remember": "on"
        }
        session.post(login_url, data=payload, headers={'Referer': login_url}, timeout=10)
        return session
    except Exception as e:
        st.error(f"Login Failed: {e}")
        return None

def fetch_json(session, endpoint):
    url = f"https://toran-camo.flightapp.be/nova-api/{endpoint}"
    try:
        r = session.get(url, timeout=10)
        if r.status_code == 200:
            return r.json()
        else:
            return {"error": r.status_code, "text": r.text}
    except Exception as e:
        return {"error": str(e)}

# --- MAIN APP ---
st.title("🕵️ API Data Hunter")
st.write("This tool ignores the dashboard and focuses 100% on finding the missing maintenance record.")

if st.button("🚀 Connect & Start Hunt"):
    session = get_session()
    
    if session:
        st.success("Connected to CAMO System")
        
        # 1. GET AIRCRAFT LIST
        st.info("Fetching Aircraft List...")
        ac_json = fetch_json(session, "aircraft?perPage=100")
        
        if 'resources' in ac_json:
            # Create a map of Tail -> ID
            ac_map = {}
            for r in ac_json['resources']:
                title = r.get('title', 'Unknown') # usually the tail number
                id_val = r.get('id', {}).get('value')
                if title and id_val:
                    ac_map[title] = id_val
            
            # USER SELECTION
            target_tail = st.selectbox("Select Aircraft to Investigate", sorted(ac_map.keys()))
            target_id = ac_map[target_tail]
            
            st.markdown(f"### Investigating: **{target_tail}** (ID: `{target_id}`)")
            st.divider()

            # --- STRATEGY 1: PARENT OBJECT ---
            st.subheader("Strategy 1: The Parent Object")
            st.write("Checking if the 'Last Maintenance' is hidden directly inside the Aircraft profile...")
            parent_data = fetch_json(session, f"aircraft/{target_id}")
            
            # flatten fields for display
            if 'resource' in parent_data:
                fields = {f['attribute']: f['value'] for f in parent_data['resource']['fields']}
                st.json(fields)
            else:
                st.warning("Strategy 1 failed to retrieve aircraft details.")

            st.divider()

            # --- STRATEGY 2: THE RELATIONSHIP (Using your log parameters) ---
            st.subheader("Strategy 2: The Relationship Link")
            st.write("Using the exact 'maintenanceHistory' relationship found in your logs...")
            
            # The Magic URL based on your log
            rel_url = (
                f"aircraft-maintenance-histories?"
                f"viaResource=aircraft&"
                f"viaResourceId={target_id}&"
                f"viaRelationship=maintenanceHistory&"
                f"relationshipType=hasMany&" # This was missing before!
                f"perPage=25&"
                f"orderBy=date&"
                f"orderByDirection=desc"
            )
            st.code(rel_url, language="http")
            
            rel_data = fetch_json(session, rel_url)
            
            found_2020 = False
            if 'resources' in rel_data:
                st.write(f"Found {len(rel_data['resources'])} history records.")
                
                # Parse them nicely
                history_table = []
                for r in rel_data['resources']:
                    f = {field['attribute']: field['value'] for field in r['fields']}
                    history_table.append(f)
                    
                    # Check for the magic number
                    val = str(f.get('ttsn', '') or f.get('total_time', ''))
                    if "2020" in val:
                        found_2020 = True
                        st.success(f"🎯 FOUND IT! Record with TTSN {val} found in Strategy 2!")
                
                if history_table:
                    st.dataframe(pd.DataFrame(history_table))
                else:
                    st.warning("Relationship returned valid JSON but 0 records.")
            else:
                st.error(f"Strategy 2 Failed: {rel_data}")

            st.divider()

            # --- STRATEGY 3: THE SEARCH ---
            st.subheader("Strategy 3: Global Search")
            st.write("Searching the entire history table for this registration string...")
            
            search_url = f"aircraft-maintenance-histories?search={target_tail}&perPage=25&orderBy=date&orderByDirection=desc&filters=W10%3D"
            search_data = fetch_json(session, search_url)
            
            if 'resources' in search_data:
                st.write(f"Found {len(search_data['resources'])} records via search.")
                if len(search_data['resources']) > 0:
                    st.json(search_data['resources'][0]['fields']) # Show first result fields
            else:
                st.error("Strategy 3 Failed")

        else:
            st.error("Could not fetch aircraft list. Check credentials.")
