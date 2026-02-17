import requests
import json
import os
import sqlite3
import time

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
