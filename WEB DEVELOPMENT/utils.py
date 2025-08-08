import re
import math
import time
import logging
import requests
from geopy.geocoders import Photon, Nominatim
from geopy.distance import geodesic
from geopy.exc import GeocoderUnavailable, GeocoderTimedOut
import overpy
import PyPDF2

# Initialize geocoders
photon_geolocator = Photon(user_agent="transport_finder_v4", domain="photon.komoot.io")
nomi_geolocator = Nominatim(user_agent="transport_finder_v4_nominatim")

def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate the great-circle distance between two points in meters"""
    R = 6371000  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (math.sin(delta_phi/2)**2 + math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda/2)**2))
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def normalize_stop_name(name):
    """Normalize stop name by removing punctuation and converting to uppercase"""
    name = re.sub(r'[^\w\s]', '', name)
    return name.upper().strip()

def get_road_distance(origin, destination):
    """Get road distance in meters using OSRM API"""
    # OSRM demo server (public, no API key needed)
    url = "http://router.project-osrm.org/route/v1/driving/"
    
    # Format: lon,lat;lon,lat
    coords = f"{origin[1]},{origin[0]};{destination[1]},{destination[0]}"
    
    try:
        response = requests.get(url + coords, timeout=10)
        data = response.json()
        
        if data['code'] == 'Ok':
            # Get distance in meters from first route
            distance = data['routes'][0]['distance']
            return distance
        else:
            logging.warning(f"OSRM API error: {data.get('message', 'Unknown error')}")
            return None
    except Exception as e:
        logging.error(f"Error getting road distance: {e}")
        return None

def get_coordinates(location, is_station=False):
    """
    Get latitude and longitude for a given location with retry logic, using Nominatim.
    If is_station is True, try variations with "Railway Station" and "Junction".
    Returns (lat, lon) or None.
    """
    try:
        logging.info(f"Geocoding location: {location} (is_station={is_station})")
        
        # Create variations for station searches
        variations = [location]
        if is_station:
            variations += [
                f"{location} Railway Station",
                f"{location} Junction",
                f"{location} Station"
            ]
        
        # Try each variation
        for loc_query in variations:
            location_info = nomi_geolocator.geocode(loc_query, exactly_one=True, timeout=10)
            if location_info:
                lat, lon = location_info.latitude, location_info.longitude
                logging.info(f"Found coordinates: {lat}, {lon} for '{loc_query}'")
                return (lat, lon)
        
        # If no success with variations, try simplified address
        simplified = simplify_address(location)
        if simplified and simplified != location:
            logging.info(f"Retry geocoding with simplified address: {simplified}")
            location_info = nomi_geolocator.geocode(simplified, exactly_one=True, timeout=10)
            if location_info:
                lat, lon = location_info.latitude, location_info.longitude
                logging.info(f"Found coordinates (simplified): {lat}, {lon} for '{simplified}'")
                return (lat, lon)
        
        # Try fallback city part
        city_part = extract_city(location)
        if city_part and city_part != location:
            logging.info(f"Retry geocoding with city part: {city_part}")
            location_info = nomi_geolocator.geocode(city_part, exactly_one=True, timeout=10)
            if location_info:
                lat, lon = location_info.latitude, location_info.longitude
                logging.info(f"Found coordinates (city part): {lat}, {lon} for '{city_part}'")
                return (lat, lon)
        
        logging.warning(f"Could not geocode: '{location}'")
        return None
    except (GeocoderUnavailable, GeocoderTimedOut) as e:
        logging.warning(f"Geocoding unavailable/timed out for '{location}': {e}")
        return None
    except Exception as e:
        logging.error(f"Error getting coordinates for '{location}': {e}")
        return None

def simplify_address(address):
    """Simplify address by removing initial numbers or 'near ...' parts."""
    if not address:
        return address
    # remove leading house/building number
    if address[0].isdigit():
        parts = address.split(',', 1)
        if len(parts) > 1:
            address = parts[1].strip()
    # remove trailing 'near ...'
    if 'near' in address.lower():
        address = address.split('near', 1)[0].strip()
    return address

def extract_city(address):
    """
    Extract the last comma-separated component from the input as a likely city/town.
    E.g. "Temple Name, Sirkazhi" -> "Sirkazhi".
    """
    if not address:
        return ""
    parts = [p.strip() for p in address.split(',') if p.strip()]
    if parts:
        return parts[-1]
    return ""

def get_city_from_coords(coords, timeout=10):
    """
    Reverse geocode coords to get a city/town/village name.
    First try Photon; if too generic, fallback to Nominatim.
    Returns a string or None.
    """
    lat, lon = coords

    def extract_from_address(addr: dict):
        # priority: city, town, village, hamlet, municipality, county, suburb, locality, then state/region
        for key in ('city', 'town', 'village', 'hamlet', 'municipality', 'county', 'suburb', 'locality'):
            if key in addr and addr[key].strip():
                return addr[key].strip()
        for key in ('state', 'region', 'state_district'):
            if key in addr and addr[key].strip():
                return addr[key].strip()
        return None

    # Try Photon reverse
    try:
        loc = photon_geolocator.reverse((lat, lon), exactly_one=True, timeout=timeout)
        if loc and loc.raw:
            addr = loc.raw.get('address', {})
            city = extract_from_address(addr)
            if city and city.lower() not in ('india', 'country', ''):
                logging.info(f"Photon reverse: using '{city}' for coords {coords}")
                return city
            else:
                logging.info(f"Photon reverse too generic ('{city}'), falling back to Nominatim for coords {coords}")
        else:
            logging.info(f"Photon reverse returned no useful data for coords {coords}, falling back to Nominatim")
    except (GeocoderUnavailable, GeocoderTimedOut) as e:
        logging.warning(f"Photon reverse unavailable/timed out: {e}. Falling back to Nominatim.")
    except Exception as e:
        logging.warning(f"Photon reverse error: {e}. Falling back to Nominatim.")

    # Fallback: Nominatim reverse
    try:
        loc2 = nomi_geolocator.reverse((lat, lon), exactly_one=True, timeout=timeout)
        if loc2 and loc2.raw:
            addr2 = loc2.raw.get('address', {})
            city2 = extract_from_address(addr2)
            if city2 and city2.lower() not in ('india', 'country', ''):
                logging.info(f"Nominatim reverse: using '{city2}' for coords {coords}")
                return city2
            else:
                # Last resort: last part of display name
                display = loc2.address or ""
                parts = [p.strip() for p in display.split(',') if p.strip()]
                if parts:
                    last = parts[-1]
                    logging.info(f"Nominatim reverse fallback to last component '{last}' for coords {coords}")
                    return last
        else:
            logging.info(f"Nominatim reverse returned no useful data for coords {coords}")
    except (GeocoderUnavailable, GeocoderTimedOut) as e:
        logging.warning(f"Nominatim reverse unavailable/timed out: {e}.")
    except Exception as e:
        logging.warning(f"Nominatim reverse error: {e}.")

    return None

def find_nearby_transport(coords, transport_type, radius=5000):
    """Find nearby bus stops/stations or train stations using Overpass API."""
    try:
        lat, lon = coords
        if transport_type == "bus":
            query = f"""
            [out:json];
            (
              node["highway"="bus_stop"](around:{radius}, {lat}, {lon});
              node["amenity"="bus_station"](around:{radius}, {lat}, {lon});
            );
            out body;
            """
        elif transport_type == "train":
            query = f"""
            [out:json];
            (
              node["railway"="station"](around:{radius}, {lat}, {lon});
              node["railway"="halt"](around:{radius}, {lat}, {lon});
            );
            out body;
            """
        else:
            return []
        response = requests.post("https://overpass-api.de/api/interpreter", data={'data': query}, timeout=15)
        data = response.json()
        transport_points = []
        for element in data.get('elements', []):
            name = element.get('tags', {}).get('name', 'Unnamed')
            point_coords = (element['lat'], element['lon'])
            distance = geodesic(coords, point_coords).meters
            transport_points.append({
                'name': name,
                'distance': round(distance),
                'coords': point_coords,
                'type': transport_type
            })
        transport_points.sort(key=lambda x: x['distance'])
        return transport_points
    except Exception as e:
        logging.error(f"Error finding nearby {transport_type}: {e}")
        return []

def find_best_bus_stand(city_name, reference_coords):
    """
    Find the best bus stand for a city with multiple variations and validation.
    Returns dictionary with 'coords', 'name', and 'distance' (in km) or None.
    """
    # List of possible bus stand name variations
    variations = [
        f"{city_name} Bus Stand",
        f"{city_name} Bus Terminal",
        f"{city_name} Bus Station",
        f"Bus Stand, {city_name}",
        f"{city_name} Main Bus Stand"
    ]
    
    best_stand = None
    min_distance = float('inf')  # initialize with a large number
    
    for name in variations:
        coords = get_coordinates(name)
        if coords:
            # Calculate distance to reference coordinates
            distance = geodesic(coords, reference_coords).km
            if distance < min_distance and distance <= 20:  # within 20 km
                min_distance = distance
                best_stand = {
                    'coords': coords,
                    'name': name,
                    'distance': distance
                }
                # If we found one within 5 km, break early (good enough)
                if distance <= 5:
                    break
    
    return best_stand

def extract_station_codes(pdf_path):
    """Extract station names and codes from the PDF"""
    station_data = {}
    logging.info(f"Extracting station codes from: {pdf_path}")
    
    try:
        with open(pdf_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            logging.info(f"PDF has {len(reader.pages)} pages")
            
            for page in reader.pages:
                text = page.extract_text()
                if not text:
                    continue
                    
                # Split into lines and process
                lines = text.split('\n')
                for line in lines:
                    line = line.strip()
                    if not line or re.match(r'Page \d+', line):
                        continue
                    
                    # Handle multi-line station names
                    if not re.search(r'[A-Z]{3,5}$', line):
                        continue
                        
                    # Extract station code (last 3-5 uppercase letters)
                    parts = re.split(r'\s{2,}', line)
                    if len(parts) < 2:
                        continue
                        
                    # Last part is the code
                    code = parts[-1].strip()
                    name = ' '.join(parts[:-1]).strip()
                    
                    if name and code and len(code) >= 2:
                        station_data[name.upper()] = code
                        
        logging.info(f"Loaded {len(station_data)} station codes from PDF")
        return station_data
    except Exception as e:
        logging.error(f"Error extracting station codes: {e}")
        return {}

def clean_station_name(name):
    """Normalize station names for matching"""
    if not name:
        return ""
    name = name.upper().strip()
    # Remove common suffixes and special characters
    name = re.sub(r'\b(JUNCTION|STATION|RAILWAY STATION|JN|STN)\b', '', name)
    name = re.sub(r'[^A-Z0-9 ]', '', name)  # Keep only alphanumeric and space
    name = re.sub(r'\s+', ' ', name).strip()
    return name

def search_station(station_data, query):
    """Search for a station by name with transliteration support"""
    query = clean_station_name(query)
    logging.info(f"Searching station: {query}")

    # Try exact match first
    if query in station_data:
        return [(query, station_data[query])]
        
    # Try partial matches
    results = []
    for name, code in station_data.items():
        clean_name = clean_station_name(name)
        if query in clean_name:
            results.append((name, code))
        
    return results

def get_auto_fare(distance_km, is_night=False):
    """Calculate auto fare based on distance and time"""
    base_fare = 50
    base_km = 1.8
    per_km = 18
    
    if distance_km <= base_km:
        fare = base_fare
    else:
        fare = base_fare + (distance_km - base_km) * per_km
    
    # Add night surcharge (50% extra from 11 PM to 5 AM)
    if is_night:
        fare *= 1.5
    
    return round(fare)

def get_cab_fare(distance_km, is_night=False):
    """Calculate cab fare based on distance and time"""
    base_fare = 100
    base_km = 2
    per_km = 18  # Average of 15-20
    
    if distance_km <= base_km:
        fare = base_fare
    else:
        fare = base_fare + (distance_km - base_km) * per_km
    
    # Add night surcharge (50% extra from 11 PM to 5 AM)
    if is_night:
        fare *= 1.5
    
    return round(fare)

def get_min_max_fare(distance_km, is_night=False):
    """Get min (auto) and max (cab) fare estimates"""
    min_fare = get_auto_fare(distance_km, is_night)
    max_fare = get_cab_fare(distance_km, is_night)
    return min_fare, max_fare

def get_transport_icon(mode):
    """Return appropriate emoji for transport mode"""
    icons = {
        "walk": "🚶",
        "auto": "🛺",
        "cab": "🚕",
        "bus": "🚌",
        "train": "🚆",
        "you": "📍"
    }
    return icons.get(mode, "➡️")

def replace_bus_terminal_names(name):
    """Replace various Chennai bus terminal names with C.M.B.T"""
    if not name:
        return name
    name = name.upper()
    if "CHENNAI BUS STATION" in name or "CHENNAI BUS TERMINAL" in name or "C.M.B.T" in name:
        return "C.M.B.T"
    return name