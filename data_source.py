import requests
import json
import os
import sqlite3
import time
import threading
import queue

# Load .env file automatically
if os.path.exists('.env'):
    with open('.env', 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ[k.strip()] = v.strip().strip('"\'')

# 1. Load Aircraft Database into Memory
AIRCRAFT_DB = {}
DB_FILE = "aircraft_db.json"

if os.path.exists(DB_FILE):
    print("Loading aircraft database...")
    try:
        with open(DB_FILE, 'r') as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    # Key by hex code (lowercase)
                    if 'icao' in entry:
                        AIRCRAFT_DB[entry['icao'].lower()] = entry
                except json.JSONDecodeError:
                    continue
        print(f"Loaded {len(AIRCRAFT_DB)} aircraft records.")
    except Exception as e:
        print(f"Error loading database: {e}")
else:
    print("Warning: aircraft_db.json not found.")

# 1.5 Load Routes Database
ROUTES_DB = {}
ROUTES_FILE = "routes.json"

if os.path.exists(ROUTES_FILE):
    print("Loading routes database...")
    try:
        with open(ROUTES_FILE, 'r') as f:
            # We enforce uppercase keys to match against uppercase callsigns
            raw_routes = json.load(f)
            ROUTES_DB = {k.upper(): v for k, v in raw_routes.items()}
        print(f"Loaded {len(ROUTES_DB)} routes.")
    except Exception as e:
        print(f"Error loading routes database: {e}")

# 1.5.5 Load Airports Database
AIRPORTS_DB = {}
AIRPORTS_FILE = "airports.json"

if os.path.exists(AIRPORTS_FILE):
    print("Loading airports database...")
    try:
        with open(AIRPORTS_FILE, 'r') as f:
            AIRPORTS_DB = json.load(f)
        print(f"Loaded {len(AIRPORTS_DB)} airports.")
    except Exception as e:
        print(f"Error loading airports database: {e}")

# 1.6 FlightAware AeroAPI Integration
flightaware_queue = queue.Queue()
missing_routes_in_progress = set()
request_times = []

def fetch_flightaware_route_worker():
    global request_times
    api_key = os.environ.get('FLIGHTAWARE_API_KEY')
    if not api_key:
        return

    print("FlightAware AeroAPI worker started. Rate limit set to 10/min.")
    while True:
        try:
            callsign = flightaware_queue.get()
            if not callsign:
                continue
                
            # Enforce 10 requests per rolling 60 seconds
            now = time.time()
            request_times = [t for t in request_times if now - t < 60.0]
            if len(request_times) >= 10:
                sleep_time = 60.0 - (now - request_times[0]) + 0.5
                print(f"AeroAPI: Approaching 10/min limit. Sleeping for {sleep_time:.1f}s...")
                time.sleep(sleep_time)
                
            request_times.append(time.time())

            # API details: AeroAPI v4 GET /flights/{ident}
            url = f"https://aeroapi.flightaware.com/aeroapi/flights/{callsign}"
            headers = {"x-apikey": api_key}
            
            response = requests.get(url, headers=headers, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                flights = data.get("flights", [])
                
                route_found = False
                for f in flights:
                    origin_obj = f.get("origin") or {}
                    dest_obj = f.get("destination") or {}
                    
                    origin = origin_obj.get("code_icao")
                    dest = dest_obj.get("code_icao")
                    
                    if origin and dest:
                        ROUTES_DB[callsign] = {"from": origin, "to": dest}
                        
                        try:
                            with open(ROUTES_FILE, 'r') as file:
                                current_routes = json.load(file)
                        except (FileNotFoundError, json.JSONDecodeError):
                            current_routes = {}
                        
                        current_routes[callsign] = {"from": origin, "to": dest}
                        
                        with open(ROUTES_FILE, 'w') as file:
                            json.dump(current_routes, file, indent=4)
                        
                        print(f"AeroAPI: Cached new route for {callsign} ({origin} -> {dest})")
                        route_found = True
                        break
                
                if not route_found:
                    ROUTES_DB[callsign] = {"from": "--", "to": "--"} # Prevent repeated lookups
            else:
                print(f"AeroAPI Error for {callsign}: {response.status_code} - {response.text}")
                if response.status_code in [401, 403, 429]:
                    print("AeroAPI: Rate limit or auth error encountered. Pausing for 60s.")
                    time.sleep(60) # Back off heavily on auth or quota limits
            
            missing_routes_in_progress.discard(callsign)
            
            time.sleep(0.5) # Small buffer between requests
            
        except Exception as e:
            print(f"AeroAPI Worker Error: {e}")
            time.sleep(5)

if os.environ.get('FLIGHTAWARE_API_KEY'):
    threading.Thread(target=fetch_flightaware_route_worker, daemon=True).start()



# 2. Setup SQLite for Persistent Tracking
DB_PATH = 'seen_aircraft.db'

def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS seen_aircraft (
                hex TEXT PRIMARY KEY,
                first_seen REAL,
                last_seen REAL,
                flight TEXT,
                type TEXT,
                desc TEXT
            )
        ''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Database init error: {e}")

# Initialize immediately
init_db()

# 3. Cache Seen Types for "New Type" Alert
SEEN_TYPES = set()

def load_seen_types():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT DISTINCT type FROM seen_aircraft WHERE type IS NOT NULL AND type != ""')
        rows = c.fetchall()
        for r in rows:
            SEEN_TYPES.add(r[0])
        conn.close()
        print(f"Loaded {len(SEEN_TYPES)} unique aircraft types.")
    except Exception as e:
        print(f"Error loading seen types: {e}")

load_seen_types()

def fix_existing_descriptions():
    """Calculates full descriptions for all existing records using the static DB"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Get all records
        c.execute('SELECT hex FROM seen_aircraft')
        rows = c.fetchall()
        
        count = 0
        for r in rows:
            hex_code = r[0].lower()
            if hex_code in AIRCRAFT_DB:
                db_info = AIRCRAFT_DB[hex_code]
                parts = []
                if db_info.get('manufacturer'): parts.append(db_info.get('manufacturer'))
                if db_info.get('model'): parts.append(db_info.get('model'))
                
                if parts:
                    full_desc = ' '.join(parts)
                    c.execute('UPDATE seen_aircraft SET desc = ? WHERE hex = ?', (full_desc, hex_code))
                    count += 1
        
        conn.commit()
        conn.close()
        if count > 0:
            print(f"Migrated {count} aircraft records to full descriptions.")
    except Exception as e:
        print(f"Migration error: {e}")

# Run migration once on startup
fix_existing_descriptions()

def track_sightings(aircraft_list):
    if not aircraft_list:
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        now = time.time()
        
        for ac in aircraft_list:
            hex_code = ac.get('hex')
            if not hex_code: continue
            
            flight = ac.get('flight', '').strip()
            type_code = ac.get('t', '')
            
            # Use full description if available (Manuf + Model), otherwise fallback to (short) desc
            desc = ac.get('full_desc') or ac.get('desc', '')
            
            # Check if exists
            c.execute('SELECT 1 FROM seen_aircraft WHERE hex = ?', (hex_code,))
            exists = c.fetchone()
            
            if exists:
                c.execute('''
                    UPDATE seen_aircraft 
                    SET last_seen = ?, flight = ?, type = ?, desc = ?
                    WHERE hex = ?
                ''', (now, flight, type_code, desc, hex_code))
            else:
                c.execute('''
                    INSERT INTO seen_aircraft (hex, first_seen, last_seen, flight, type, desc)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (hex_code, now, now, flight, type_code, desc))
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Tracking error: {e}")

def get_stats():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM seen_aircraft')
        total_seen = c.fetchone()[0]
        conn.close()
        return {
            'total_seen': total_seen,
            'unique_types': len(SEEN_TYPES)
        }
    except:
        return {'total_seen': 0, 'unique_types': 0}

def get_unique_types_data():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        # Group by type, get count, last seen, and a representative description
        c.execute('''
            SELECT type, MAX(desc), MAX(last_seen), COUNT(*) 
            FROM seen_aircraft 
            WHERE type IS NOT NULL AND type != "" 
            GROUP BY type 
            ORDER BY MAX(last_seen) DESC
        ''')
        rows = c.fetchall()
        conn.close()
        
        results = []
        for r in rows:
            results.append({
                'type': r[0],
                'desc': r[1],
                'last_seen': r[2], # Unix timestamp
                'count': r[3]
            })
        return results
    except Exception as e:
        print(f"Error getting unique types: {e}")
        return []

MANUFACTURERS = [
    "BOEING", "AIRBUS", "BOMBARDIER", "EMBRAER", "CESSNA", "PIPER", "BEECH", "BELL", 
    "SIKORSKY", "CIRRUS", "DIAMOND", "PILATUS", "GULFSTREAM", "DASSAULT", "TEXTRON", 
    "NORTHROP", "GRUMMAN", "LOCKHEED", "DOUGLAS", "MCDONNELL", "DE HAVILLAND", 
    "AEROSPATIALE", "ROBINSON", "AGUSTA", "LEONARDO", "RAYTHEON", "HAWKER"
]

def clean_desc(desc):
    if not desc: return ""
    upper_desc = desc.upper()
    for manuf in MANUFACTURERS:
        if upper_desc.startswith(manuf):
            # Remove manufacturer and any following space/hyphen
            return desc[len(manuf):].lstrip(" -").strip()
    return desc

def get_aircraft_data():
    try:
        # Fetch live data
        response = requests.get('http://adsbexchange.local/tar1090/data/aircraft.json', timeout=2)
        if response.status_code == 200:
            data = response.json()
            aircraft_list = data.get('aircraft', [])
            
            # Enrich with DB data
            for ac in aircraft_list:
                hex_code = ac.get('hex', '').lower()
                
                # Check for route matches based on callsign
                flight_callsign = ac.get('flight', '').strip().upper()
                if flight_callsign in ROUTES_DB:
                    route_from = ROUTES_DB[flight_callsign].get('from')
                    route_to = ROUTES_DB[flight_callsign].get('to')
                    if route_from and route_to and route_from != "--":
                        ac['route_from'] = route_from
                        ac['route_to'] = route_to
                        # Enrich with full airport names
                        if route_from in AIRPORTS_DB:
                            ac['route_from_name'] = AIRPORTS_DB[route_from].get('name', '')
                        if route_to in AIRPORTS_DB:
                            ac['route_to_name'] = AIRPORTS_DB[route_to].get('name', '')
                elif flight_callsign and len(flight_callsign) >= 3 and flight_callsign.isalnum():
                    if flight_callsign not in missing_routes_in_progress and os.environ.get('FLIGHTAWARE_API_KEY'):
                        missing_routes_in_progress.add(flight_callsign)
                        flightaware_queue.put(flight_callsign)

                if hex_code in AIRCRAFT_DB:
                    db_info = AIRCRAFT_DB[hex_code]
                    
                    # 1. Registration (Tail Number)
                    if 'r' not in ac and db_info.get('reg'):
                        ac['r'] = db_info['reg']
                    
                    # 2. Type Code (ICAO Type)
                    if 't' not in ac and db_info.get('icaotype'):
                        ac['t'] = db_info['icaotype']
                    
                    # Capture Full Description for DB Storage (Manufacturer + Model)
                    # We do this BEFORE shortening it for the UI
                    parts = []
                    if db_info.get('manufacturer'): parts.append(db_info.get('manufacturer'))
                    if db_info.get('model'): parts.append(db_info.get('model'))
                    if parts:
                        ac['full_desc'] = ' '.join(parts)
                    
                    # 3. Description (Model only)
                    # User requested shorter descriptions (no manufacturer)
                    # Prefer DB 'model' if available as it is usually usage-ready (e.g. "C-17A")
                    if db_info.get('model'):
                        ac['desc'] = db_info.get('model')
                    elif 'desc' not in ac and db_info.get('manufacturer'):
                        # Fallback to manufacturer only if no model AND no live description
                        ac['desc'] = db_info.get('manufacturer')

                    # 4. Military Flag
                    if 'mil' not in ac and db_info.get('mil'):
                        ac['mil'] = True

                # Final Cleanup: Remove common manufacturer names from ALL aircraft
                # Done here so it applies whether data came from DB or live feed
                if 'desc' in ac:
                    try:
                        ac['desc'] = clean_desc(ac['desc'])
                    except Exception:
                        pass # Don't crash on string manipulation errors

                # Check for New Type
                type_code = ac.get('t', '')
                if type_code and type_code not in SEEN_TYPES:
                    ac['is_new_type'] = True
                    SEEN_TYPES.add(type_code)
                else:
                    ac['is_new_type'] = False
            
            # Track sightings
            track_sightings(aircraft_list)
            
            # Add stats
            data['stats'] = get_stats()

            return data
    except requests.RequestException:
        pass # Fallback

    # Mock Data Fallback
    try:
        with open('mock_data.json', 'r') as f:
            data = json.load(f)
            # Add mock stats
            data['stats'] = {'total_seen': 123, 'unique_types': 12}
            return data
    except FileNotFoundError:
        return None
