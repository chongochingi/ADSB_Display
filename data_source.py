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

# 1.6 FlightAware AeroAPI Integration (opt-in only — default off)
flightaware_queue = queue.Queue()
missing_routes_in_progress = set()
request_times = []


def flightaware_api_enabled() -> bool:
    """
    AeroAPI calls are disabled unless explicitly enabled (saves monthly quota when unset).
    To turn on later: set FLIGHTAWARE_ENABLED=1 (or true/yes/on) and FLIGHTAWARE_API_KEY.
    """
    flag = os.environ.get('FLIGHTAWARE_ENABLED', '').strip().lower()
    if flag not in ('1', 'true', 'yes', 'on'):
        return False
    return bool(os.environ.get('FLIGHTAWARE_API_KEY'))


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
                
            now = time.time()
            
            # Clean up old timestamps beyond 24 hours
            request_times = [t for t in request_times if now - t < 86400.0]
            
            # Check Daily Limit (max 600 requests per 24 hours to stay strictly under 1500 limit)
            if len(request_times) >= 600:
                sleep_time = 86400.0 - (now - request_times[0]) + 1.0
                print(f"AeroAPI: Daily budget limit reached. Sleeping for {sleep_time/3600:.1f} hours...")
                time.sleep(sleep_time)
                continue
                
            # Check Hourly Limit (max 60 requests per hour to spread it out)
            hourly_times = [t for t in request_times if now - t < 3600.0]
            if len(hourly_times) >= 60:
                sleep_time = 3600.0 - (now - hourly_times[0]) + 1.0
                print(f"AeroAPI: Hourly limit reached. Sleeping for {sleep_time/60:.1f} minutes...")
                time.sleep(sleep_time)
                continue
                
            request_times.append(now)

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
                        today = datetime.date.today().isoformat()
                        ROUTES_DB[callsign] = {"from": origin, "to": dest, "updated": today}
                        
                        try:
                            with open(ROUTES_FILE, 'r') as file:
                                current_routes = json.load(file)
                        except (FileNotFoundError, json.JSONDecodeError):
                            current_routes = {}
                        
                        current_routes[callsign] = {"from": origin, "to": dest, "updated": today}
                        
                        with open(ROUTES_FILE, 'w') as file:
                            json.dump(current_routes, file, indent=4)
                        
                        print(f"AeroAPI: Cached new route for {callsign} ({origin} -> {dest})")
                        route_found = True
                        break
                
                if not route_found:
                    today = datetime.date.today().isoformat()
                    ROUTES_DB[callsign] = {"from": "--", "to": "--", "updated": today} # Prevent repeated lookups
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


if flightaware_api_enabled():
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
        c.execute('''
            CREATE TABLE IF NOT EXISTS daily_flights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                flight TEXT,
                route_from TEXT,
                route_to TEXT,
                type TEXT,
                date TEXT,
                first_seen REAL,
                last_seen REAL,
                UNIQUE(flight, date)
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
                
            # DAILY SCHEDULE TRACKING
            flight_upper = flight.upper()
            if flight_upper and len(flight_upper) >= 3 and flight_upper.isalnum():
                is_military = ac.get('mil') or (ac.get('dbFlags', 0) & 1)
                is_ga_tail = flight_upper.startswith('N') and len(flight_upper) >= 2 and flight_upper[1].isdigit()
                
                if not is_military and not is_ga_tail:
                    today_date = time.strftime('%Y-%m-%d', time.localtime(now))
                    route_from = ac.get('route_from', '')
                    route_to = ac.get('route_to', '')
                    
                    c.execute('''
                        INSERT INTO daily_flights (flight, route_from, route_to, type, date, first_seen, last_seen)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(flight, date) DO UPDATE SET
                        last_seen = excluded.last_seen,
                        route_from = CASE WHEN excluded.route_from != '' THEN excluded.route_from ELSE route_from END,
                        route_to = CASE WHEN excluded.route_to != '' THEN excluded.route_to ELSE route_to END
                    ''', (flight_upper, route_from, route_to, type_code, today_date, now, now))
        
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
                            ac['route_from_lat'] = AIRPORTS_DB[route_from].get('lat')
                            ac['route_from_lon'] = AIRPORTS_DB[route_from].get('lon')
                        if route_to in AIRPORTS_DB:
                            ac['route_to_name'] = AIRPORTS_DB[route_to].get('name', '')
                            ac['route_to_lat'] = AIRPORTS_DB[route_to].get('lat')
                            ac['route_to_lon'] = AIRPORTS_DB[route_to].get('lon')
                elif flight_callsign and len(flight_callsign) >= 3 and flight_callsign.isalnum():
                    is_military = ac.get('mil') or (ac.get('dbFlags', 0) & 1)
                    is_ga_tail = flight_callsign.startswith('N') and len(flight_callsign) >= 2 and flight_callsign[1].isdigit()
                    
                    if not is_military and not is_ga_tail:
                        if (
                            flight_callsign not in missing_routes_in_progress
                            and flightaware_api_enabled()
                        ):
                            missing_routes_in_progress.add(flight_callsign)
                            flightaware_queue.put(flight_callsign)

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

def get_flight_schedule():
    """
    Returns a schedule of predicted commercial flights based on historical data.
    Groups flights by their average time seen, rounded to 15-minute buckets.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute('''
            SELECT flight, MAX(route_from) as route_from, MAX(route_to) as route_to, MAX(type) as type,
                   COUNT(DISTINCT date) as days_seen,
                   GROUP_CONCAT(first_seen) as first_seen_list,
                   GROUP_CONCAT(last_seen) as last_seen_list
            FROM daily_flights
            GROUP BY flight
            HAVING days_seen >= 2
        ''')
        rows = c.fetchall()
        conn.close()
        
        schedule = []
        import statistics
        
        for r in rows:
            flight = r[0]
            route_from = r[1]
            route_to = r[2]
            type_code = r[3]
            days_seen = r[4]
            first_seen_str = r[5]
            last_seen_str = r[6]
            
            first_seen_times = [float(x) for x in first_seen_str.split(',')]
            last_seen_times = [float(x) for x in last_seen_str.split(',')]
            
            def get_tod_seconds(ts):
                lt = time.localtime(ts)
                return lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec
            
            first_tods = [get_tod_seconds(ts) for ts in first_seen_times]
            last_tods = [get_tod_seconds(ts) for ts in last_seen_times]
            
            avg_first_tod = sum(first_tods) / len(first_tods)
            avg_last_tod = sum(last_tods) / len(last_tods)
            
            if len(first_tods) >= 2:
                std_dev = statistics.stdev(first_tods)
                if std_dev > 7200: # Exclude if variance > 2 hours
                    continue
            
            # Round down to nearest 15 mins
            bucket_hour = int(avg_first_tod // 3600)
            bucket_minute = int(((avg_first_tod % 3600) // 900) * 15)
            bucket_sort_val = bucket_hour + (bucket_minute / 60.0)
            
            bucket_time_str = f"{bucket_hour:02d}:{bucket_minute:02d}"
            bucket_datetime = time.strptime(bucket_time_str, "%H:%M")
            bucket_display = time.strftime("%I:%M %p", bucket_datetime)
            
            avg_duration_sec = avg_last_tod - avg_first_tod
            if avg_duration_sec < 0: avg_duration_sec += 86400
            duration_mins = max(1, int(avg_duration_sec / 60))
            
            # Prioritize the manual routes.json database
            display_from = route_from
            display_to = route_to
            if flight in ROUTES_DB:
                display_from = ROUTES_DB[flight].get('from', route_from)
                display_to = ROUTES_DB[flight].get('to', route_to)

            schedule.append({
                'flight': flight,
                'route_from': display_from if display_from else '--',
                'route_to': display_to if display_to else '--',
                'type': type_code,
                'bucket_sort': bucket_sort_val,
                'bucket_display': bucket_display,
                'duration_mins': duration_mins,
                'days_seen': days_seen
            })
            
        schedule.sort(key=lambda x: x['bucket_sort'])
        
        # Group by 15-min bucket
        # Returns a list of dicts: [{'bucket': '08:15 AM', 'flights': [...]}]
        grouped_list = []
        current_bucket = None
        current_flights = []
        
        for s in schedule:
            if s['bucket_display'] != current_bucket:
                if current_bucket is not None:
                    grouped_list.append({'bucket': current_bucket, 'flights': current_flights})
                current_bucket = s['bucket_display']
                current_flights = [s]
            else:
                current_flights.append(s)
                
        if current_bucket is not None:
            grouped_list.append({'bucket': current_bucket, 'flights': current_flights})
            
        return grouped_list
    except Exception as e:
        print(f"Error getting schedule: {e}")
        return []
