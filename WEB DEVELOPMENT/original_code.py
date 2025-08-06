import os
import re
import time
import logging
import sys
import io
import requests
from datetime import datetime
from flask import Flask, request, render_template_string
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from geopy.distance import geodesic
from geopy.geocoders import Photon, Nominatim
from geopy.exc import GeocoderUnavailable, GeocoderTimedOut, GeocoderServiceError
import PyPDF2
import tempfile
import inspect
import urllib3
import json
import math
from fuzzywuzzy import process, fuzz
import gc
import overpy

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Fix Unicode output for Windows
if sys.stdout.encoding != 'UTF-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
if sys.stderr.encoding != 'UTF-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)

# Optional: print signature for confirmation
print(inspect.signature(render_template_string))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("transport_finder.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)

app = Flask(__name__)

# Initialize geocoders
photon_geolocator = Photon(user_agent="transport_finder_v4", domain="photon.komoot.io")
nomi_geolocator = Nominatim(user_agent="transport_finder_v4_nominatim")

geolocator = Nominatim(user_agent="mtc_bus_finder")


def get_driver():
    options = EdgeOptions()
    options.add_argument("--headless=new")  # New headless mode
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    
    # Create unique temp directory for each session
    temp_dir = tempfile.mkdtemp()
    options.add_argument(f"--user-data-dir={temp_dir}")
    options.add_argument(f"--data-path={temp_dir}")
    options.add_argument(f"--disk-cache-dir={temp_dir}")
    
    # Explicitly set binary location if needed
    options.binary_location = "/usr/bin/microsoft-edge"
    
    # Configure service
    service = EdgeService(
        executable_path='/usr/local/bin/msedgedriver',
        service_args=['--verbose']  # Enable verbose logging
    )
    
    try:
        driver = webdriver.Edge(service=service, options=options)
        return driver
    except Exception as e:
        # Clean up temp directory if creation fails
        if os.path.exists(temp_dir):
            os.rmdir(temp_dir)
        raise e

# Cache variables
FARE_CACHE = None
stop_coords_cache = {}

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

def get_cordinates(address):
    """Geocode address using Nominatim"""
    try:
        location = geolocator.geocode(address + ", Chennai, Tamil Nadu, India", timeout=5)
        if location:
            return {
                "coords": (location.latitude, location.longitude),
                "formatted_address": location.address.upper()
            }
        return None
    except (GeocoderUnavailable, GeocoderServiceError) as e:
        print(f"Geocoding service error for {address}: {str(e)}")
        return None
    except Exception as e:
        print(f"Geocoding error for {address}: {str(e)}")
        return None

def get_nearby_bus_stops(lat, lon, radius=500):
    """Find nearby bus stops using Overpass API"""
    api = overpy.Overpass()
    query = f"""
    [out:json];
    (
      node["highway"="bus_stop"](around:{radius},{lat},{lon});
      node["public_transport"="platform"](around:{radius},{lat},{lon});
    );
    out;
    """
    try:
        result = api.query(query)
        stops = []
        for node in result.nodes:
            name = node.tags.get("name", f"Bus stop at {node.lat},{node.lon}").upper()
            if "TB HOSPITAL" in name:
                continue
            stops.append({
                "name": name,
                "coords": (float(node.lat), float(node.lon))
            })
        return stops
    except Exception as e:
        print(f"Error fetching nearby stops: {str(e)}")
        return []

def match_stop_name(osm_stop, all_stops, threshold=80):
    """Find the best matching stop name using fuzzy matching"""
    osm_normalized = normalize_stop_name(osm_stop)
    match, score = process.extractOne(osm_normalized, all_stops, scorer=fuzz.token_set_ratio)
    if score >= threshold:
        return match
    return None

def get_matched_bus_stops(lat, lon, all_stops, radius=500):
    """Get matched bus stops from OSM to the MTC stop list"""
    osm_stops = get_nearby_bus_stops(lat, lon, radius)
    matched_stops = []
    for osm_stop in osm_stops:
        match = match_stop_name(osm_stop["name"], all_stops)
        if match and "TB HOSPITAL" not in match:
            distance = haversine_distance(lat, lon, osm_stop["coords"][0], osm_stop["coords"][1])
            matched_stops.append({
                "name": match,
                "coords": osm_stop["coords"],
                "distance": distance
            })
    return sorted(matched_stops, key=lambda x: x["distance"])[:3]

def get_bus_fares():
    """Get bus fares with retry logic and fallback"""
    global FARE_CACHE
    if FARE_CACHE:
        return FARE_CACHE

    import requests
    from bs4 import BeautifulSoup
    import time

    ordinary_fares = {}
    express_fares = {}
    base_url = "https://mtcbus.tn.gov.in/Home/fares"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    def scrape_tab(soup, tab_id):
        fare_dict = {}
        tab = soup.find('div', id=tab_id)
        if not tab:
            return fare_dict
        container = tab.find('div', class_='col-md-12')
        if not container:
            return fare_dict
        for div in container.find_all('div', class_=lambda x: x and x.startswith('stage')):
            if 'end' in div.get('class', []):
                break
            stage_text = div.find(string=True, recursive=False)
            if not stage_text:
                continue
            try:
                stage_num = int(stage_text.strip())
            except ValueError:
                continue
            rate_span = div.find('span', class_='rate')
            if not rate_span:
                continue
            try:
                fare = float(rate_span.text.strip())
            except ValueError:
                continue
            fare_dict[stage_num] = fare
        return fare_dict

    # Retry logic
    for attempt in range(3):
        try:
            print(f"Fetching fare information (Attempt {attempt+1})...")
            response = requests.get(base_url, headers=headers, verify=False, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            ordinary_fares = scrape_tab(soup, "tab0")
            express_fares = scrape_tab(soup, "tab3")

            if ordinary_fares and express_fares:
                FARE_CACHE = (ordinary_fares, express_fares)
                return FARE_CACHE
            else:
                print("Incomplete fare data, retrying...")
        except Exception as e:
            print(f"Attempt {attempt+1} failed: {str(e)}")
            time.sleep(2 ** attempt)  # Exponential backoff

    # Fallback fare values if scraping fails
    print("All attempts failed. Using fallback fare table.")
    ordinary_fares = {
        1: 5, 2: 7, 3: 8, 4: 10, 5: 12, 6: 14, 7: 15, 8: 17,
        9: 18, 10: 20, 11: 22, 12: 23, 13: 25, 14: 27, 15: 28,
        16: 30, 17: 32, 18: 33, 19: 35, 20: 37, 21: 38, 22: 40
    }
    express_fares = {
        1: 10, 2: 15, 3: 20, 4: 25, 5: 30, 6: 35, 7: 40, 8: 45,
        9: 50, 10: 55, 11: 60, 12: 65, 13: 70, 14: 75, 15: 80,
        16: 85, 17: 90, 18: 95, 19: 100, 20: 105, 21: 110, 22: 115
    }
    FARE_CACHE = (ordinary_fares, express_fares)
    return FARE_CACHE


def get_fare(stages, fare_dict, max_stage):
    """Calculate fare based on stages"""
    if not fare_dict:
        return 0
    if stages in fare_dict:
        return fare_dict[stages]
    return fare_dict.get(max_stage, fare_dict.get(max(fare_dict.keys(), 0)))

def get_stop_coordinates(stop_name):
    """Get coordinates for a specific bus stop with caching"""
    if stop_name in stop_coords_cache:
        return stop_coords_cache[stop_name]
    
    try:
        api = overpy.Overpass()
        query = f"""
        [out:json];
        node["name"~"{stop_name}",i]["highway"="bus_stop"];
        out;
        """
        result = api.query(query)
        if result.nodes:
            node = result.nodes[0]
            coords = (float(node.lat), float(node.lon))
            stop_coords_cache[stop_name] = coords
            return coords
    except Exception:
        pass
    
    try:
        location = geolocator.geocode(stop_name + ", Chennai, Tamil Nadu, India", timeout=5)
        if location:
            coords = (location.latitude, location.longitude)
            stop_coords_cache[stop_name] = coords
            return coords
    except Exception:
        pass
    
    return None

def format_distance(distance):
    """Format distance in meters to human-readable format"""
    if distance < 1000:
        return f"{distance:.0f} meters"
    return f"{distance/1000:.1f} km"

def format_coords(coords):
    """Format coordinates for display"""
    return f"{coords[0]:.6f}, {coords[1]:.6f}" if coords else "Unknown"

# Fetch & parse routes
print("Fetching bus routes...")
URL = "https://greenmesg.org/dictionary/routes/chennai_bus_routes.txt?161011"
try:
    resp = requests.get(URL, timeout=5)
    resp.raise_for_status()
except Exception as e:
    print(f"Error fetching bus routes: {str(e)}")
    sys.exit(1)

routes = {}
all_stops = set()
for line in resp.text.splitlines():
    line = line.strip()
    if not line or ':' not in line:
        continue
    rno, stops_str = line.split(':', 1)
    stops = [normalize_stop_name(s.strip()) for s in stops_str.split(',') if s.strip()]
    routes[rno.strip().upper()] = stops
    all_stops.update(stops)

# Precompute stop_routes
stop_routes = {}
for route, stops in routes.items():
    for stop in stops:
        if stop not in stop_routes:
            stop_routes[stop] = set()
        stop_routes[stop].add(route)

def routes_serving(stop):
    """Find routes serving a stop"""
    return list(stop_routes.get(normalize_stop_name(stop), set()))

# -------------- Utility Functions --------------

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

# -------------- Improved Bus Stand Identification --------------

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

# -------------- AbhiBus Scraper --------------

def get_abhibus_city_id(target_city):
    """Scrape AbhiBus /routes pages to find city ID corresponding to target_city."""
    base_url = "https://www.abhibus.com/routes/"
    letter_offsets = {
        'A':0,'B':540,'C':1530,'D':1980,'E':2520,'F':2610,'G':2700,'H':3060,'I':3330,'J':3420,
        'K':3780,'L':5040,'M':5220,'N':6210,'O':6750,'P':6750,'Q':7560,'R':7560,'S':8010,'T':8460
    }
    tc = target_city.strip().lower()
    if not tc:
        return None
    first_char = tc[0].upper()
    if not first_char.isalpha():
        return None
    current_offset = letter_offsets.get(first_char, 0)
    page_increment = 90
    max_offset = 8460
    while current_offset <= max_offset:
        url = f"{base_url}{current_offset}" if current_offset > 0 else base_url
        logging.info(f"Looking up AbhiBus city ID for '{tc}' on page {url}")
        try:
            headers = {"User-Agent":"Mozilla/5.0"}
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            form = soup.find('form', {'id':'frmRoute'})
            if not form:
                current_offset += page_increment
                continue
            city_list = form.select('div.opt-list div.detrow ul li')
            if not city_list:
                break
            found = None
            for li in city_list:
                link = li.find('a')
                if not link:
                    continue
                link_text = link.get_text(strip=True).lower()
                if tc in link_text:
                    href = link.get('href','')
                    m = re.search(r'/routes/(\d+)', href)
                    if m:
                        found = m.group(1)
                        break
            if found:
                return found
            current_offset += page_increment
            time.sleep(0.2)
        except Exception as e:
            logging.error(f"Error in get_abhibus_city_id: {e}")
            break
    return None

import logging
import tempfile
import os
from selenium import webdriver
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException

def scrape_abhibus_results(search_url):
    """Use Selenium to scrape AbhiBus search results from the given search_url.
    
    Args:
        search_url (str): URL of AbhiBus search results page
        
    Returns:
        list: List of dictionaries containing bus service information
    """
    # Configure Edge options
    options = EdgeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")  # Crucial for Docker
    options.add_argument("--disable-dev-shm-usage")  # Prevents /dev/shm issues
    options.add_argument("--start-maximized")
    options.add_argument("user-agent=Mozilla/5.0")
    options.add_experimental_option('excludeSwitches', ['enable-logging'])
    
    # Create unique temp directory for browser profile
    temp_dir = tempfile.mkdtemp()
    options.add_argument(f"--user-data-dir={temp_dir}")
    
    # Initialize WebDriver with error handling
    driver = None
    results = []
    
    try:
        # Configure Edge service with logging
        service = EdgeService(
            executable_path='/usr/local/bin/msedgedriver',
            service_args=['--verbose']
        )
        
        driver = webdriver.Edge(service=service, options=options)
        
        # Set page load timeout
        driver.set_page_load_timeout(30)
        
        # Navigate to URL
        driver.get(search_url)
        
        # Wait for results to load
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "div.container.card.service.light.rounded-md")
            )
        )
        
        # Extract bus service cards
        cards = driver.find_elements(
            By.CSS_SELECTOR, 
            "div.container.card.service.light.rounded-md"
        )
        
        # Process each card
        for card in cards:
            try:
                results.append({
                    'provider': 'AbhiBus',
                    'operator': card.find_element(By.CSS_SELECTOR, "h5.title").text.strip(),
                    'bus_type': card.find_element(
                        By.CSS_SELECTOR, "div.operator-info div.sub-title"
                    ).text.strip(),
                    'departure': card.find_element(
                        By.CSS_SELECTOR, "span.departure-time"
                    ).text.strip(),
                    'arrival': card.find_element(
                        By.CSS_SELECTOR, "span.arrival-time"
                    ).text.strip(),
                    'duration': card.find_element(
                        By.CSS_SELECTOR, "div.travel-time"
                    ).text.strip(),
                    'fare': card.find_element(
                        By.CSS_SELECTOR, "span.fare"
                    ).text.strip(),
                    'booking_url': search_url
                })
            except NoSuchElementException as e:
                logging.warning(f"Missing element in card: {e}")
                continue
                
        return results
        
    except TimeoutException:
        logging.error("Timed out waiting for AbhiBus results to load")
        return []
    except WebDriverException as e:
        logging.error(f"WebDriver error during AbhiBus scrape: {e}")
        return []
    except Exception as e:
        logging.error(f"Unexpected error scraping AbhiBus: {e}")
        return []
    finally:
        # Clean up resources
        try:
            if driver:
                driver.quit()
        except Exception as e:
            logging.error(f"Error closing driver: {e}")
        
        # Clean up temp directory
        try:
            if os.path.exists(temp_dir):
                os.system(f"rm -rf {temp_dir}")
        except Exception as e:
            logging.error(f"Error cleaning temp directory: {e}")

# -------------- TNSTC Scraper --------------

def get_tnstc_place_id(session, place_name, place_type='from'):
    """
    Use TNSTC's autocomplete endpoint to get place ID and code:
    - place_type: 'from' or 'to'.
    """
    url = 'https://www.tnstc.in/OTRSOnline/jqreq.do'
    params = {'hiddenAction': 'LoadFromPlaceList' if place_type == 'from' else 'LoadTOPlaceList'}
    data = {('matchStartPlace' if place_type=='from' else 'matchEndPlace'): place_name}
    try:
        r = session.post(url, params=params, data=data, timeout=10)
        if r.status_code != 200:
            return None, None
        for opt in r.text.strip().split('^'):
            if not opt:
                continue
            parts = opt.split(':')
            if len(parts) >= 3 and place_name.upper() in parts[2].upper():
                return parts[0], parts[1]
    except Exception as e:
        logging.error(f"TNSTC place_id error: {e}")
    return None, None

def parse_tnstc_schedules(html):
    """Parse TNSTC search result HTML for schedule items."""
    soup = BeautifulSoup(html, 'html.parser')
    res = []
    for item in soup.select('.bus-list .bus-item'):
        try:
            op = item.select_one('.operator-name').text.strip()
            bt = item.select_one('.text-muted.d-block').text.strip()
            dep = item.select_one('.time-info .text-4').text.strip()
            arr = item.select_one('.time-info .text-5').text.strip()
            dur = item.select_one('.duration').text.strip() if item.select_one('.duration') else 'N/A'
            price_t = item.select_one('.price').text.replace('Rs','').strip() if item.select_one('.price') else ''
            price = int(price_t) if price_t.isdigit() else price_t
            seats = item.select_one('.text-1').text.split()[0] if item.select_one('.text-1') else 'N/A'
            res.append({
                'provider': 'TNSTC',
                'operator': op,
                'bus_type': bt,
                'departure': dep,
                'arrival': arr,
                'duration': dur,
                'fare': f"₹{price}" if isinstance(price, int) else price,
                'available_seats': seats,
                'booking_url': None
            })
        except Exception:
            continue
    return res

def get_tnstc_bus_schedules(source, destination, date_str_ddmmyyyy):
    """
    Search TNSTC schedules:
    - date_str_ddmmyyyy: in format 'DD/MM/YYYY'
    """
    session = requests.Session()
    sid, scode = get_tnstc_place_id(session, source, 'from')
    did, dcode = get_tnstc_place_id(session, destination, 'to')
    if not sid or not did:
        logging.info(f"TNSTC: could not find place IDs for '{source}' or '{destination}'")
        return []
    url = 'https://www.tnstc.in/OTRSOnline/jqreq.do'
    params = {'hiddenAction':'SearchService'}
    data = {
        'hiddenStartPlaceID': sid,
        'hiddenEndPlaceID': did,
        'hiddenOnwardJourneyDate': date_str_ddmmyyyy,
        'txtStartPlaceCode': scode,
        'txtEndPlaceCode': dcode,
        'hiddenStartPlaceName': source,
        'hiddenEndPlaceName': destination,
        'matchStartPlace': source,
        'matchEndPlace': destination,
        'txtJourneyDate': date_str_ddmmyyyy,
        'hiddenMaxNoOfPassengers':'16',
        'selectStartPlace': scode,
        'selectEndPlace': dcode,
        'languageType':'E',
        'checkSingleLady':'N'
    }
    try:
        r = session.post(url, params=params, data=data, timeout=10)
        if r.status_code != 200:
            return []
        return parse_tnstc_schedules(r.text)
    except Exception as e:
        logging.error(f"TNSTC schedules error: {e}")
        return []

# -------------- RedBus Scraper --------------

def get_fully_scrolled_html(url):
    """Scroll RedBus page fully via Selenium to load all results.
    
    Args:
        url (str): RedBus search URL
        
    Returns:
        str: Fully loaded page HTML or None if failed
    """
    # Configure Edge options for Docker compatibility
    options = EdgeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")  # Essential for Docker
    options.add_argument("--disable-dev-shm-usage")  # Prevents /dev/shm issues
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("user-agent=Mozilla/5.0")
    
    # Create unique temp directory for browser profile
    temp_dir = tempfile.mkdtemp()
    options.add_argument(f"--user-data-dir={temp_dir}")
    
    driver = None
    try:
        # Initialize WebDriver with Docker-compatible path
        service = EdgeService(
            executable_path='/usr/local/bin/msedgedriver',  # Docker path
            service_args=['--verbose']  # Enable logging if needed
        )
        driver = webdriver.Edge(service=service, options=options)
        
        # Mask Selenium detection
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        
        # Load initial page
        driver.get(url)
        time.sleep(5)  # Initial load wait
        
        # Scroll to load all results
        prev_count = 0
        same_count = 0
        max_attempts = 10  # Prevent infinite loops
        attempts = 0
        
        while attempts < max_attempts:
            attempts += 1
            items = driver.find_elements(
                By.CSS_SELECTOR, 
                "div.sectionWrapper__ind-search-styles-module-scss-AITjK li"
            )
            current_count = len(items)
            
            # Check if we've stopped loading new items
            if current_count == prev_count:
                same_count += 1
                if same_count >= 2:  # Consistent count for 2 checks
                    break
            else:
                same_count = 0
                prev_count = current_count
            
            # Scroll to last item if found
            if items:
                driver.execute_script(
                    "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", 
                    items[-1]
                )
                time.sleep(3)  # Allow loading after scroll
            else:
                break
        
        return driver.page_source
        
    except WebDriverException as e:
        logging.error(f"WebDriver error during RedBus scroll: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error scrolling RedBus: {e}")
        return None
    finally:
        # Clean up resources
        try:
            if driver:
                driver.quit()
        except Exception as e:
            logging.error(f"Error closing driver: {e}")
        
        # Clean up temp directory
        try:
            if os.path.exists(temp_dir):
                os.system(f"rm -rf {temp_dir}")
        except Exception as e:
            logging.error(f"Error cleaning temp directory: {e}")

def extract_redbus_details(html):
    """Parse RedBus HTML for bus listings."""
    soup = BeautifulSoup(html, 'html.parser')
    section = soup.find("div", class_=re.compile(r"sectionWrapper.*"))
    if not section:
        return []
    res = []
    for li in section.find_all("li"):
        name_tag = li.find("div", class_=re.compile(r"travelsName.*"))
        name = name_tag.get_text(strip=True) if name_tag else 'N/A'
        wrap = li.find("div", class_=re.compile(r"timeFareBoWrap.*"))
        if wrap:
            bd = wrap.find("p", class_=re.compile(r"boardingTime.*"))
            dp = wrap.find("p", class_=re.compile(r"droppingTime.*"))
            dur = wrap.find("p", class_=re.compile(r"duration.*"))
            fare = wrap.find("p", class_=re.compile(r"finalFare.*"))
            bd_t = bd.get_text(strip=True) if bd else 'N/A'
            dp_t = dp.get_text(strip=True) if dp else 'N/A'
            dur_t = dur.get_text(strip=True) if dur else 'N/A'
            fare_t = fare.get_text(strip=True) if fare else 'N/A'
        else:
            bd_t = dp_t = dur_t = fare_t = 'N/A'
        res.append({
            'provider': 'RedBus',
            'operator': name,
            'departure': bd_t,
            'arrival': dp_t,
            'duration': dur_t,
            'fare': fare_t,
            'booking_url': None
        })
    return res

# -------------- IRCTC Train API (Improved) --------------

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

def get_irctc_api_response(source_code, destination_code, journey_date, quota="GN", retries=3):
    """Directly call IRCTC API to get train schedules with retry logic"""
    # Prepare API request
    api_url = "https://www.irctc.co.in/eticketing/protected/mapps1/altAvlEnq/TC"
    
    # Prepare headers (from your packet capture)
    headers = {
        'authority': 'www.irctc.co.in',
        'accept': 'application/json, text/plain, */*',
        'accept-language': 'en-US,en;q=0.9',
        'content-type': 'application/json; charset=UTF-8',
        'greq': str(int(time.time() * 1000)),  # Current timestamp in milliseconds
        'origin': 'https://www.irctc.co.in',
        'referer': 'https://www.irctc.co.in/nget/train-search',
        'sec-ch-ua': '"Microsoft Edge";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36 Edg/137.0.0.0',
        'bmirak': 'webbm'
    }
    
    # Prepare payload (from your packet capture)
    payload = {
        "concessionBooking": False,
        "srcStn": source_code,
        "destStn": destination_code,
        "jrnyClass": "",
        "jrnyDate": journey_date,
        "quotaCode": quota,
        "currentBooking": "false",
        "flexiFlag": False,
        "handicapFlag": False,
        "ticketType": "E",
        "loyaltyRedemptionBooking": False,
        "ftBooking": False
    }
    
    # Create a session
    session = requests.Session()
    
    for attempt in range(retries):
        try:
            logging.info(f"Sending IRCTC API request (attempt {attempt+1}/{retries})...")
            logging.info(f"Source: {source_code}, Destination: {destination_code}, Date: {journey_date}")
            response = session.post(
                api_url,
                headers=headers,
                json=payload,
                timeout=15  # Increased timeout
            )
            
            logging.info(f"API response status: {response.status_code}")
            
            if response.status_code != 200:
                logging.error(f"API request failed with status {response.status_code}")
                logging.error(f"Response text: {response.text[:500]}")
                continue  # Try again
                
            return response.json()
            
        except requests.exceptions.Timeout:
            logging.warning(f"Request timeout on attempt {attempt+1}")
        except requests.exceptions.ConnectionError as ce:
            logging.warning(f"Connection error: {str(ce)}")
        except Exception as e:
            logging.error(f"API request failed: {str(e)}")
        
        # Exponential backoff before retrying
        sleep_time = 2 ** attempt  # 1, 2, 4 seconds
        logging.info(f"Waiting {sleep_time} seconds before retry...")
        time.sleep(sleep_time)
    
    logging.error(f"All {retries} attempts failed")
    return None

def parse_train_schedules(api_response):
    """Parse train schedules from API response"""
    if not api_response:
        logging.warning("No API response to parse")
        return []
    
    try:
        trains = []
        train_list = api_response.get('trainBtwnStnsList', [])
        
        if not train_list:
            logging.warning("No trains found in API response")
            return []
        
        logging.info(f"Found {len(train_list)} trains in API response")
        
        for train in train_list:
            try:
                train_info = {
                    'train_number': train.get('trainNumber', 'N/A'),
                    'train_name': train.get('trainName', 'N/A'),
                    'departure_time': train.get('departureTime', 'N/A'),
                    'arrival_time': train.get('arrivalTime', 'N/A'),
                    'duration': train.get('duration', 'N/A'),
                    'available_classes': train.get('avlClasses', []),
                    'distance': train.get('distance', 'N/A'),
                    'train_type': train.get('trainType', ['N/A'])[0] if train.get('trainType') else 'N/A'
                }
                trains.append(train_info)
                logging.debug(f"Parsed train: {train_info['train_number']} - {train_info['train_name']}")
            except Exception as e:
                logging.error(f"Error parsing train: {str(e)}")
                continue
        
        return trains
        
    except Exception as e:
        logging.error(f"Error parsing API response: {str(e)}")
        return []

# -------------- Fare Calculation Helpers --------------

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

# -------------- Route Visualization Helpers --------------

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

def generate_route_details(steps):
    """Generate compact HTML for vertical route visualization"""
    if not steps:
        return '<span class="text-muted">N/A</span>'
    
    html = '<div class="route-details">'
    for i, step in enumerate(steps):
        # Add arrow between steps except before first
        if i > 0:
            html += '<div class="text-center my-1">↓</div>'  # Reduced margin
            
        # Format step details
        details = []
        if step.get("distance_km") is not None:
            details.append(f"{step['distance_km']:.1f} km")
        if step.get("fare"):
            if isinstance(step['fare'], tuple):
                details.append(f"₹{step['fare'][0]}-₹{step['fare'][1]}")
            else:
                details.append(f"₹{step['fare']}")
        
        details_str = f" ({', '.join(details)})" if details else ""
        
        html += f'<div class="route-step {step["mode"]}">'
        html += f'{get_transport_icon(step["mode"])} <strong>{step["description"]}</strong>{details_str}'
        html += '</div>'
    html += '</div>'
    return html

# Global cache for MTC routes
mtc_route_cache = {}

def get_fare(stages, fare_dict, max_stage):
    """Return the fare for the given number of stages, using the max stage if stages exceed max_stage."""
    if not fare_dict:  # Handle empty fare dictionary
        return 0
    if stages >= max_stage:
        return fare_dict[max_stage]
    return fare_dict.get(stages, fare_dict[max(fare_dict.keys())])

def build_route_steps(source_input, dest_input, source_coords, source_hub_coords, source_hub_name, 
                     hub_to_hub_distance, hub_to_hub_name,
                     dest_hub_coords, dest_hub_name,
                     dest_coords, is_bus=True, departure_time=None):
    """Build route steps with Google Maps links, handling Chennai Bus Station as C.M.B.T, with MTC route caching."""
    steps = []
    is_night = False
    if departure_time:
        try:
            hour = int(departure_time.split(':')[0])
            if hour >= 23 or hour < 5:
                is_night = True
        except:
            pass

    # Replace bus terminal names with C.M.B.T
    source_hub_display = replace_bus_terminal_names(source_hub_name)
    dest_hub_display = replace_bus_terminal_names(dest_hub_name)

    # Step 1: "You" starting point
    steps.append({
        "mode": "you",
        "description": "You",
        "distance_km": None,
        "fare": None,
        "map_url": None
    })

    # Get fares for MTC
    ordinary_fares, express_fares = get_bus_fares()
    max_ordinary_stage = max(ordinary_fares.keys()) if ordinary_fares else 0
    max_express_stage = max(express_fares.keys()) if express_fares else 0

    # Function to find MTC routes between two stops
    def find_mtc_routes(start_stop, end_stop, start_coords_mtc, end_coords_mtc):
        all_options = []
        start_bus = []
        end_bus = []

        # Handle start stop
        if start_stop in all_stops:
            coords = get_stop_coordinates(start_stop)
            routes_list = routes_serving(start_stop)
            if routes_list:
                start_bus.append({
                    "name": start_stop,
                    "coords": coords,
                    "distance": 0,
                    "routes": routes_list
                })
        elif start_coords_mtc:
            rad_sour = get_matched_bus_stops(start_coords_mtc[0], start_coords_mtc[1], all_stops, radius=2000)
            for stop in rad_sour:
                stop["routes"] = routes_serving(stop["name"])
                if stop["routes"]:
                    start_bus.append(stop)

        # Handle end stop
        if end_stop in all_stops:
            coords = get_stop_coordinates(end_stop)
            routes_list = routes_serving(end_stop)
            if routes_list:
                end_bus.append({
                    "name": end_stop,
                    "coords": coords,
                    "distance": 0,
                    "routes": routes_list
                })
        elif end_coords_mtc:
            rad_dest = get_matched_bus_stops(end_coords_mtc[0], end_coords_mtc[1], all_stops, radius=2000)
            for stop in rad_dest:
                stop["routes"] = routes_serving(stop["name"])
                if stop["routes"]:
                    end_bus.append(stop)

        # Select closest stop with routes
        start_bus = sorted(start_bus, key=lambda x: x["distance"])[:1] if start_bus else []
        end_bus = sorted(end_bus, key=lambda x: x["distance"])[:1] if end_bus else []

        # Find MTC routes
        start_time = time.time()
        max_duration = 10
        for sv in start_bus:
            for ev in end_bus:
                pair_options = []
                directs = []
                for r, stops in routes.items():
                    if sv["name"] in stops and ev["name"] in stops:
                        try:
                            i, j = stops.index(sv["name"]), stops.index(ev["name"])
                            segment = stops[i:j+1] if i < j else list(reversed(stops[j:i+1]))
                            stops_count = len(segment)
                            stages = stops_count - 1
                            min_fare = get_fare(stages, ordinary_fares, max_ordinary_stage)
                            max_fare = get_fare(stages, express_fares, max_express_stage)
                            directs.append({
                                'type': 'direct',
                                'route': r,
                                'path': segment,
                                'stops': stops_count,
                                'stages': stages,
                                'min_fare': min_fare,
                                'max_fare': max_fare,
                                'start': sv,
                                'end': ev,
                                'start_coords': sv.get("coords"),
                                'end_coords': ev.get("coords")
                            })
                        except Exception:
                            pass
                for direct in directs[:1]:  # Limit to one direct route
                    pair_options.append(direct)
                if len(pair_options) < 1:
                    transfers = []
                    start_routes = sv["routes"]
                    end_routes = ev["routes"]
                    for r1 in start_routes:
                        if time.time() - start_time > max_duration:
                            break
                        try:
                            stops1 = routes[r1]
                            i1 = stops1.index(sv["name"])
                            for j1 in range(i1+1, min(i1+10, len(stops1))):
                                transfer = stops1[j1]
                                for r2 in end_routes:
                                    if r2 == r1 or transfer not in routes[r2]:
                                        continue
                                    try:
                                        stops2 = routes[r2]
                                        i2 = stops2.index(transfer)
                                        j2 = stops2.index(ev["name"])
                                        leg1 = stops1[i1:j1+1]
                                        leg1_stops = len(leg1)
                                        leg1_stages = leg1_stops - 1
                                        leg2 = stops2[i2:j2+1] if i2 < j2 else list(reversed(stops2[j2:i2+1]))
                                        leg2_stops = len(leg2)
                                        leg2_stages = leg2_stops - 1
                                        min_fare_leg1 = get_fare(leg1_stages, ordinary_fares, max_ordinary_stage)
                                        max_fare_leg1 = get_fare(leg1_stages, express_fares, max_express_stage)
                                        min_fare_leg2 = get_fare(leg2_stages, ordinary_fares, max_ordinary_stage)
                                        max_fare_leg2 = get_fare(leg2_stages, express_fares, max_express_stage)
                                        min_fare_total = min_fare_leg1 + min_fare_leg2
                                        max_fare_total = max_fare_leg1 + max_fare_leg2
                                        transfers.append({
                                            'type': 'transfer',
                                            'transfer_point': transfer,
                                            'route1': r1,
                                            'leg1': leg1,
                                            'leg1_stages': leg1_stages,
                                            'route2': r2,
                                            'leg2': leg2,
                                            'leg2_stages': leg2_stages,
                                            'stops': leg1_stops + leg2_stops - 1,
                                            'min_fare': min_fare_total,
                                            'max_fare': max_fare_total,
                                            'start': sv,
                                            'end': ev,
                                        })
                                        break
                                    except ValueError:
                                        continue
                            for j1 in range(max(0, i1-10), i1):
                                transfer = stops1[j1]
                                for r2 in end_routes:
                                    if r2 == r1 or transfer not in routes[r2]:
                                        continue
                                    try:
                                        stops2 = routes[r2]
                                        i2 = stops2.index(transfer)
                                        j2 = stops2.index(ev["name"])
                                        leg1 = list(reversed(stops1[j1:i1+1]))
                                        leg1_stops = len(leg1)
                                        leg1_stages = leg1_stops - 1
                                        leg2 = stops2[i2:j2+1] if i2 < j2 else list(reversed(stops2[j2:i2+1]))
                                        leg2_stops = len(leg2)
                                        leg2_stages = leg2_stops - 1
                                        min_fare_leg1 = get_fare(leg1_stages, ordinary_fares, max_ordinary_stage)
                                        max_fare_leg1 = get_fare(leg1_stages, express_fares, max_express_stage)
                                        min_fare_leg2 = get_fare(leg2_stages, ordinary_fares, max_ordinary_stage)
                                        max_fare_leg2 = get_fare(leg2_stages, express_fares, max_express_stage)
                                        min_fare_total = min_fare_leg1 + min_fare_leg2
                                        max_fare_total = max_fare_leg1 + max_fare_leg2
                                        transfers.append({
                                            'type': 'transfer',
                                            'transfer_point': transfer,
                                            'route1': r1,
                                            'leg1': leg1,
                                            'leg1_stages': leg1_stages,
                                            'route2': r2,
                                            'leg2': leg2,
                                            'leg2_stages': leg2_stages,
                                            'stops': leg1_stops + leg2_stops - 1,
                                            'min_fare': min_fare_total,
                                            'max_fare': max_fare_total,
                                            'start': sv,
                                            'end': ev,
                                        })
                                        break
                                    except ValueError:
                                        continue
                        except ValueError:
                            continue
                    transfers.sort(key=lambda x: x['min_fare'])
                    for transfer in transfers[:1 - len(pair_options)]:
                        pair_options.append(transfer)
                all_options.extend(pair_options)
        return all_options, start_bus, end_bus

    # Cache key based on source_hub_name, dest_hub_name, and transport mode
    mode = "bus" if is_bus else "train"
    cache_key = (source_hub_name, dest_hub_name, mode)

    # First mile (source to source hub)
    first_mile_dist = geodesic(source_coords, source_hub_coords).km
    first_mode = "walk" if first_mile_dist <= 1 else "auto"
    first_fare = get_min_max_fare(first_mile_dist, is_night) if first_mode == "auto" else 0
    first_mile_url = (f"https://www.google.com/maps/dir/?api=1&origin={source_coords[0]},{source_coords[1]}"
                      f"&destination={source_hub_coords[0]},{source_hub_coords[1]}&travelmode=driving")

    # Handle source as Chennai Bus Station (C.M.B.T) for buses
    if source_hub_display == "C.M.B.T" and is_bus:
        # Check cache for MTC routes
        if cache_key in mtc_route_cache:
            all_options, start_bus, end_bus = mtc_route_cache[cache_key]
        else:
            # Find MTC routes from source_input to C.M.B.T
            all_options, start_bus, end_bus = find_mtc_routes(source_input, "C.M.B.T", source_coords, source_hub_coords)
            mtc_route_cache[cache_key] = (all_options, start_bus, end_bus)
        
        # First mile to source stop
        if start_bus:
            start_mile_dist = geodesic(source_coords, start_bus[0]["coords"]).km if start_bus[0]["coords"] else first_mile_dist
            start_mode = "walk" if start_mile_dist <= 1 else "auto"
            start_fare = get_min_max_fare(start_mile_dist, is_night) if start_mode == "auto" else 0
            start_mile_url = (f"https://www.google.com/maps/dir/?api=1&origin={source_coords[0]},{source_coords[1]}"
                              f"&destination={start_bus[0]['coords'][0]},{start_bus[0]['coords'][1]}&travelmode=driving"
                              if start_bus[0]["coords"] else first_mile_url)
            steps.append({
                "mode": start_mode,
                "description": f"To {start_bus[0]['name']}",
                "distance_km": round(start_mile_dist, 1),
                "fare": start_fare,
                "map_url": start_mile_url
            })
        else:
            steps.append({
                "mode": first_mode,
                "description": f"To {source_hub_display}",
                "distance_km": round(first_mile_dist, 1),
                "fare": first_fare,
                "map_url": first_mile_url
            })
        # MTC route to C.M.B.T
        seen_routes = set()
        for option in all_options[:1]:  # Limit to one MTC route
            fare = (option['min_fare'], option['max_fare'])
            if option['type'] == 'direct':
                route_key = option['route']
                if route_key not in seen_routes:
                    seen_routes.add(route_key)
                    steps.append({
                        "mode": "bus",
                        "description": f"MTC Bus {option['route']} to {option['end']['name']}",
                        "distance_km": None,
                        "fare": fare,
                        "map_url": None
                    })
            else:
                route_key = (option['route1'], option['route2'], option['transfer_point'])
                if route_key not in seen_routes:
                    seen_routes.add(route_key)
                    steps.append({
                        "mode": "bus",
                        "description": f"MTC Bus {option['route1']} to {option['transfer_point']}, then Bus {option['route2']} to {option['end']['name']}",
                        "distance_km": None,
                        "fare": fare,
                        "map_url": None
                    })
    else:
        # Regular first mile
        steps.append({
            "mode": first_mode,
            "description": f"To {source_hub_display}",
            "distance_km": round(first_mile_dist, 1),
            "fare": first_fare,
            "map_url": first_mile_url
        })

    # Main transport
    transport_icon = "Bus" if is_bus else "Train"
    steps.append({
        "mode": "bus" if is_bus else "train",
        "description": f"{transport_icon} to {dest_hub_display}",
        "distance_km": round(hub_to_hub_distance, 1) if hub_to_hub_distance else None,
        "fare": None,
        "map_url": None
    })

    # Handle destination as Chennai Bus Station (C.M.B.T) for buses or railway station for trains
    if dest_hub_display == "C.M.B.T" and is_bus:
        # For buses, assume destination hub is C.M.B.T
        mtc_start = "C.M.B.T"
        mtc_start_coords = dest_hub_coords
        # Check cache for MTC routes
        if cache_key in mtc_route_cache:
            all_options, start_bus, end_bus = mtc_route_cache[cache_key]
        else:
            # Find MTC routes from C.M.B.T to dest_input
            all_options, start_bus, end_bus = find_mtc_routes(mtc_start, dest_input, mtc_start_coords, dest_coords)
            mtc_route_cache[cache_key] = (all_options, start_bus, end_bus)
        
        # MTC route from C.M.B.T
        seen_routes = set()
        for option in all_options[:1]:  # Limit to one MTC route
            fare = (option['min_fare'], option['max_fare'])
            if option['type'] == 'direct':
                route_key = option['route']
                if route_key not in seen_routes:
                    seen_routes.add(route_key)
                    steps.append({
                        "mode": "bus",
                        "description": f"MTC Bus {option['route']} to {option['end']['name']}",
                        "distance_km": None,
                        "fare": fare,
                        "map_url": None
                    })
            else:
                route_key = (option['route1'], option['route2'], option['transfer_point'])
                if route_key not in seen_routes:
                    seen_routes.add(route_key)
                    steps.append({
                        "mode": "bus",
                        "description": f"MTC Bus {option['route1']} to {option['transfer_point']}, then Bus {option['route2']} to {option['end']['name']}",
                        "distance_km": None,
                        "fare": fare,
                        "map_url": None
                    })
            # Update dest_hub for last mile
            dest_hub_coords = option['end']['coords'] if option['end']['coords'] else dest_hub_coords
            dest_hub_display = option['end']['name']
        # If no MTC routes found, try fallback to Chennai Central
        if not all_options:
            fallback_stop = "CHENNAI CENTRAL"
            fallback_coords = get_stop_coordinates(fallback_stop)
            if cache_key not in mtc_route_cache:
                all_options, start_bus, end_bus = find_mtc_routes(mtc_start, fallback_stop, mtc_start_coords, fallback_coords)
                mtc_route_cache[cache_key] = (all_options, start_bus, end_bus)
            else:
                all_options, start_bus, end_bus = mtc_route_cache[cache_key]
            for option in all_options[:1]:
                fare = (option['min_fare'], option['max_fare'])
                if option['type'] == 'direct':
                    route_key = option['route']
                    if route_key not in seen_routes:
                        seen_routes.add(route_key)
                        steps.append({
                            "mode": "bus",
                            "description": f"MTC Bus {option['route']} to Chennai Central",
                            "distance_km": None,
                            "fare": fare,
                            "map_url": None
                        })
                else:
                    route_key = (option['route1'], option['route2'], option['transfer_point'])
                    if route_key not in seen_routes:
                        seen_routes.add(route_key)
                        steps.append({
                            "mode": "bus",
                            "description": f"MTC Bus {option['route1']} to {option['transfer_point']}, then Bus {option['route2']} to Chennai Central",
                            "distance_km": None,
                            "fare": fare,
                            "map_url": None
                        })
                dest_hub_coords = option['end']['coords'] if option['end']['coords'] else dest_hub_coords
                dest_hub_display = "Chennai Central"

    # Last mile (from final hub/stop to destination)
    last_mile_dist = geodesic(dest_hub_coords, dest_coords).km
    last_mode = "walk" if last_mile_dist <= 1 else "auto"
    last_mode_text = "Walk" if last_mode == "walk" else "Auto"
    last_fare = get_min_max_fare(last_mile_dist, is_night) if last_mode == "auto" else 0
    last_mile_url = (f"https://www.google.com/maps/dir/?api=1&origin={dest_hub_coords[0]},{dest_hub_coords[1]}"
                     f"&destination={dest_coords[0]},{dest_coords[1]}&travelmode=driving")
    steps.append({
        "mode": last_mode,
        "description": f"{last_mode_text} to final destination",
        "distance_km": round(last_mile_dist, 1),
        "fare": last_fare,
        "map_url": last_mile_url
    })

    return steps

def calculate_total_fare(route_steps, provider_fare):
    """Calculate total fare including transport and first/last mile"""
    total_min = 0
    total_max = 0
    provider_value = 0
    try:
        if provider_fare:
            match = re.search(r'₹?(\d+[\.,]?\d*)', provider_fare)
            if match:
                provider_value = float(match.group(1).replace(',', ''))
    except:
        pass
    for step in route_steps[1:]:
        if step.get('fare'):
            if isinstance(step['fare'], tuple):
                total_min += step['fare'][0]
                total_max += step['fare'][1]
            else:
                total_min += step['fare']
                total_max += step['fare']
    for step in route_steps:
        if step['mode'] in ('bus', 'train') and step.get('fare') is None:
            step['fare'] = provider_value
            total_min += provider_value
            total_max += provider_value
            break
    return f"₹{total_min:.0f} - ₹{total_max:.0f}", route_steps

# -------------- Sorting JavaScript --------------

SORT_JS = r"""
<script>
// Simple table sorter: assumes <table id="resultsTable">, <th data-type="time|number|string"> headers.
document.addEventListener('DOMContentLoaded', function(){
    const getCellValue = (tr, idx) => tr.children[idx].getAttribute('data-sort') || tr.children[idx].innerText;
    const comparer = function(idx, asc, type) {
        return function(a, b) {
            let v1 = getCellValue(asc ? a : b, idx);
            let v2 = getCellValue(asc ? b : a, idx);
            if(type==='number'){
                let n1 = parseFloat(v1.replace(/[^0-9\.]/g,'')) || 0;
                let n2 = parseFloat(v2.replace(/[^0-9\.]/g,'')) || 0;
                return n1 - n2;
            } else if(type==='time'){
                // parse HH:MM or H:MM
                const parseTime = s => {
                    const m = /(\d{1,2}):(\d{2})/.exec(s);
                    if(m){
                        return parseInt(m[1])*60 + parseInt(m[2]);
                    }
                    return 0;
                };
                return parseTime(v1) - parseTime(v2);
            } else {
                // string
                return v1.toString().localeCompare(v2);
            }
        };
    };
    document.querySelectorAll('th.sortable').forEach(function(th){
        th.addEventListener('click', function(){
            const table = th.closest('table');
            const tbody = table.querySelector('tbody');
            Array.from(table.querySelectorAll('th')).forEach(th2 => th2.classList.remove('asc','desc'));
            let asc = !th.classList.contains('asc');
            th.classList.toggle('asc', asc);
            th.classList.toggle('desc', !asc);
            const idx = Array.prototype.indexOf.call(th.parentNode.children, th);
            const type = th.getAttribute('data-type') || 'string';
            const rows = Array.from(tbody.querySelectorAll('tr'));
            rows.sort(comparer(idx, asc, type));
            rows.forEach(r => tbody.appendChild(r));
        });
    });
    
    // Toggle route details
    document.querySelectorAll('.toggle-route').forEach(button => {
        button.addEventListener('click', function() {
            const detailsRow = this.closest('tr').nextElementSibling;
            if (detailsRow.style.display === 'none') {
                detailsRow.style.display = 'table-row';
                this.textContent = '▲ Hide Route';
            } else {
                detailsRow.style.display = 'none';
                this.textContent = '▼ Show Route';
            }
        });
    });
});
</script>
<style>
th.sortable { cursor: pointer; }
th.asc::after { content: " ▲"; }
th.desc::after { content: " ▼"; }
.route-details {
    padding: 10px;
    background: #f8f9fa;
    border-radius: 5px;
    font-size: 0.9rem;
    line-height: 1.4;
}
.route-step {
    padding: 4px 0;
    margin-bottom: 0;
    border-left: none !important;
}
.route-step.you { border-color: #dc3545; }
.route-step.walk { border-color: #28a745; }
.route-step.auto { border-color: #ffc107; }
.route-step.cab { border-color: #17a2b8; }
.route-step.bus { border-color: #6610f2; }
.route-step.train { border-color: #e83e8c; }
</style>
"""

# -------------- HTML Templates --------------

INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Journey Planner TN</title>
    <!-- Tailwind CSS CDN -->
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- Font Awesome for icons -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.2/css/all.min.css">
    <!-- Google Fonts: Poppins -->
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        /* Custom background with image and overlay */
        body {
            background: linear-gradient(rgba(0, 0, 0, 0.5), rgba(0, 0, 0, 0.5)), url('https://images.unsplash.com/photo-1600585154340-be6161a56a0c?auto=format&fit=crop&w=1920&q=80');
            background-size: cover;
            background-position: center;
            background-attachment: fixed;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            font-family: 'Poppins', sans-serif;
            color: #333;
        }
        /* Form container styling */
        .form-container {
            background: #ffffff;
            border-radius: 1.5rem;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
            padding: 2.5rem;
            max-width: 700px;
            width: 90%;
            animation: slideIn 0.5s ease-out;
        }
        /* Slide-in animation */
        @keyframes slideIn {
            from {
                opacity: 0;
                transform: translateY(50px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        /* Input and button animations */
        .form-input, .form-select, .form-btn {
            transition: all 0.3s ease-in-out;
        }
        .form-input:focus, .form-select:focus {
            border-color: #f59e0b;
            box-shadow: 0 0 0 4px rgba(245, 158, 11, 0.2);
            transform: scale(1.02);
        }
        .form-btn {
            background: linear-gradient(to right, #f59e0b, #d97706);
            border: none;
            padding: 0.75rem 1.5rem;
        }
        .form-btn:hover {
            background: linear-gradient(to right, #d97706, #b45309);
            transform: translateY(-3px);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
        }
        /* Logo animation */
        .logo-icon {
            transition: transform 0.3s ease;
        }
        .logo-icon:hover {
            transform: rotate(360deg);
        }
        /* Responsive adjustments */
        @media (max-width: 640px) {
            .form-container {
                padding: 1.5rem;
                margin: 1rem;
            }
            h1 {
                font-size: 1.75rem;
            }
            .form-btn {
                padding: 0.5rem 1rem;
            }
        }
    </style>
</head>
<body>
    <div class="form-container">
        <div class="text-center mb-8">
            <h1 class="text-4xl font-bold text-gray-800 flex items-center justify-center">
                <i class="fas fa-compass mr-3 text-amber-500 logo-icon"></i> Journey Planner TN
            </h1>
            <p class="text-gray-600 mt-2 text-lg">Discover the best routes across Tamil Nadu!</p>
        </div>
        <form method="post" action="/search" class="space-y-6">
            <div>
                <label for="source" class="block text-sm font-semibold text-gray-700 mb-2">
                    <i class="fas fa-map-pin mr-2 text-amber-500"></i> From Where?
                </label>
                <input type="text" class="form-input w-full px-5 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring" 
                       id="source" name="source" required placeholder="e.g., Head Post Office, Chennai" 
                       aria-label="Source address or place">
            </div>
            <div>
                <label for="destination" class="block text-sm font-semibold text-gray-700 mb-2">
                    <i class="fas fa-flag mr-2 text-amber-500"></i> To Where?
                </label>
                <input type="text" class="form-input w-full px-5 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring" 
                       id="destination" name="destination" required placeholder="e.g., Rajiv Gandhi Hospital" 
                       aria-label="Destination address or place">
            </div>
            <div>
                <label for="date" class="block text-sm font-semibold text-gray-700 mb-2">
                    <i class="fas fa-calendar-day mr-2 text-amber-500"></i> When?
                </label>
                <input type="date" class="form-input w-full px-5 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring" 
                       id="date" name="date" required aria-label="Journey date">
            </div>
            <div>
                <label for="mode" class="block text-sm font-semibold text-gray-700 mb-2">
                    <i class="fas fa-train mr-2 text-amber-500"></i> How?
                </label>
                <select class="form-select w-full px-5 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring" 
                        id="mode" name="mode" aria-label="Preferred travel mode">
                    <option value="both" selected>Bus & Train</option>
                    <option value="bus">Bus Only</option>
                    <option value="train">Train Only</option>
                </select>
            </div>
            <button type="submit" class="form-btn w-full py-3 px-6 text-white font-semibold rounded-lg shadow-lg">
                <i class="fas fa-search-location mr-2"></i> Find Your Route
            </button>
        </form>
        
    </div>
</body>
</html>
"""

RESULTS_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Search Results - TN Transport Finder</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  {{ sort_js|safe }}
</head>
<body class="bg-light">
<div class="container py-4">
  <h1 class="mb-4">Results for "{{ source_loc }}" → "{{ destination_loc }}" on {{ date_str }}</h1>

  {% if source_city and source_city_coords %}
    <p><strong>Source city:</strong> {{ source_city }} — city-center coords: {{ source_city_coords[0]|round(6) }}, {{ source_city_coords[1]|round(6) }}</p>
    {% if source_bus_stand_coords %}
      <p><strong>Source main bus stand:</strong> {{ source_bus_stand_name }} at {{ source_bus_stand_coords[0]|round(6) }}, {{ source_bus_stand_coords[1]|round(6) }}</p>
    {% endif %}
  {% endif %}
  {% if destination_city and dest_city_coords %}
    <p><strong>Destination city:</strong> {{ destination_city }} — city-center coords: {{ dest_city_coords[0]|round(6) }}, {{ dest_city_coords[1]|round(6) }}</p>
    {% if dest_bus_stand_coords %}
      <p><strong>Destination main bus stand:</strong> {{ dest_bus_stand_name }} at {{ dest_bus_stand_coords[0]|round(6) }}, {{ dest_bus_stand_coords[1]|round(6) }}</p>
    {% endif %}
  {% endif %}

  <div class="mb-4">
    <a href="/" class="btn btn-secondary">&larr; New Search</a>
  </div>
  {% if error %}
    <div class="alert alert-warning">{{ error }}</div>
  {% endif %}
  
  <!-- Fare Information Card -->
  <div class="card mb-4">
    <div class="card-header">
      <h5>Fare Information</h5>
    </div>
    <div class="card-body">
      <h6>🚖 Auto Rickshaw Fare</h6>
      <ul>
        <li>Minimum fare: ₹50 for the first 1.8 km</li>
        <li>After that: ₹18 per km</li>
        <li>Waiting charge: ₹1.50 per minute</li>
        <li>Night surcharge (11 PM – 5 AM): 50% extra</li>
      </ul>
      
      <h6>🚗 Cab Fare (Standard taxis or app-based like Ola/Uber)</h6>
      <ul>
        <li>Base fare: ₹100 (includes 1–2 km depending on service)</li>
        <li>Per km after base: ₹15–₹20</li>
        <li>Waiting charge: ₹100–₹120 per hour</li>
        <li>Night surcharge: 50% extra</li>
      </ul>
    </div>
  </div>
  
  {% if results %}
    <table class="table table-striped" id="resultsTable">
      <thead>
        <tr>
          <th>Provider</th>
          <th>Operator / Train</th>
          <th class="sortable" data-type="time">Departure</th>
          <th class="sortable" data-type="time">Arrival</th>
          <th>Duration</th>
          <th class="sortable" data-type="number">Fare</th>
          <th class="sortable" data-type="number">Total Cost</th>
          <th>Route</th>
          <th>Book</th>
        </tr>
      </thead>
      <tbody>
      {% for r in results %}
        <tr>
          <td data-sort="{{ r.provider }}">{{ r.provider }}</td>
          <td data-sort="{{ r.operator }}">{{ r.operator }}</td>
          <td data-sort="{{ r.departure }}">{{ r.departure }}</td>
          <td data-sort="{{ r.arrival }}">{{ r.arrival }}</td>
          <td data-sort="{{ r.duration }}">{{ r.duration }}</td>
          <td data-sort="{{ r.fare }}">{{ r.fare }}</td>
          <td data-sort="{{ r.total_cost }}">{{ r.total_cost }}</td>
          <td>
            <button class="btn btn-sm btn-info toggle-route">
              ▼ Show Route
            </button>
          </td>
          <td>
            {% if r.booking_link %}
              <a href="{{ r.booking_link }}" class="btn btn-sm btn-primary" target="_blank">Book</a>
            {% else %}
              <span class="text-muted">N/A</span>
            {% endif %}
          </td>
        </tr>
        <tr style="display: none;">
          <td colspan="9">
            {{ r.route_details|safe }}
          </td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  {% else %}
    <div class="alert alert-info">No options found.</div>
  {% endif %}
</div>

<!-- Bootstrap JS for better interaction -->
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
"""

# -------------- Flask Routes --------------

@app.route("/", methods=["GET"])
def index():
    return render_template_string(INDEX_HTML)

@app.route("/search", methods=["POST"])
def search():
    source_input = request.form.get("source", "").strip()
    dest_input = request.form.get("destination", "").strip()
    date_input = request.form.get("date", "").strip()  # 'YYYY-MM-DD'
    mode = request.form.get("mode", "both")

    logging.info(f"Search requested: '{source_input}' -> '{dest_input}' on {date_input}")

    if not source_input or not dest_input or not date_input:
        return render_template_string(RESULTS_HTML,
                                      source_loc=source_input, destination_loc=dest_input,
                                      date_str=date_input, results=[], error="Missing input",
                                      sort_js=SORT_JS,
                                      source_city=None, source_city_coords=None, source_bus_stand_coords=None,
                                      destination_city=None, dest_city_coords=None, dest_bus_stand_coords=None)

    # Geocode source and destination
    source_coords = get_coordinates(source_input)
    dest_coords = get_coordinates(dest_input)
    if not source_coords or not dest_coords:
        return render_template_string(RESULTS_HTML,
                                      source_loc=source_input, destination_loc=dest_input,
                                      date_str=date_input, results=[], error="Could not geocode source or destination",
                                      sort_js=SORT_JS,
                                      source_city=None, source_city_coords=None, source_bus_stand_coords=None,
                                      destination_city=None, dest_city_coords=None, dest_bus_stand_coords=None)

    logging.info(f"Coordinates: Source={source_coords}, Destination={dest_coords}")

    # Derive source city
    if ',' in source_input:
        src_input_city = extract_city(source_input)
        if src_input_city:
            source_city = src_input_city
            logging.info(f"Using extracted source city from input: '{source_city}'")
        else:
            rev = get_city_from_coords(source_coords)
            source_city = rev if rev else ""
            logging.info(f"Extracted source empty; using reverse-geocoded source city: '{source_city}'")
    else:
        rev = get_city_from_coords(source_coords)
        source_city = rev if rev else ""
        logging.info(f"No comma in source input; using reverse-geocoded source city: '{source_city}'")

    # Derive destination city
    if ',' in dest_input:
        dst_input_city = extract_city(dest_input)
        if dst_input_city:
            destination_city = dst_input_city
            logging.info(f"Using extracted destination city from input: '{destination_city}'")
        else:
            rev2 = get_city_from_coords(dest_coords)
            destination_city = rev2 if rev2 else ""
            logging.info(f"Extracted destination empty; using reverse-geocoded destination city: '{destination_city}'")
    else:
        rev2 = get_city_from_coords(dest_coords)
        destination_city = rev2 if rev2 else ""
        logging.info(f"No comma in dest input; using reverse-geocoded destination city: '{destination_city}'")

    # Normalize lowercase for lookups
    source_city_l = source_city.lower().strip()
    dest_city_l = destination_city.lower().strip()
    logging.info(f"Derived source city/town: '{source_city_l}', destination city/town: '{dest_city_l}'")

    # Geocode the extracted city/town names to get city-center coordinates
    source_city_coords = None
    dest_city_coords = None
    if source_city:
        ccoords = get_coordinates(source_city)
        if ccoords:
            source_city_coords = ccoords
            logging.info(f"Source city-center coords: {source_city_coords}")
    if destination_city:
        ccoords2 = get_coordinates(destination_city)
        if ccoords2:
            dest_city_coords = ccoords2
            logging.info(f"Destination city-center coords: {dest_city_coords}")

    # Find best bus stands using new identification system
    source_bus_stand_info = None
    dest_bus_stand_info = None
    
    # For source bus stand
    if source_city and source_city_coords:
        source_bus_stand_info = find_best_bus_stand(source_city, source_city_coords)
        if source_bus_stand_info:
            source_bus_stand_coords = source_bus_stand_info['coords']
            source_bus_stand_name = source_bus_stand_info['name']
            logging.info(f"Found source bus stand: {source_bus_stand_name} at {source_bus_stand_coords}")
        else:
            source_bus_stand_coords = None
            source_bus_stand_name = None
            logging.info("No suitable source bus stand found")
    else:
        source_bus_stand_coords = None
        source_bus_stand_name = None
        logging.info("Skipping source bus stand search - missing city name or coordinates")

    # For destination bus stand
    if destination_city and dest_city_coords:
        dest_bus_stand_info = find_best_bus_stand(destination_city, dest_city_coords)
        if dest_bus_stand_info:
            dest_bus_stand_coords = dest_bus_stand_info['coords']
            dest_bus_stand_name = dest_bus_stand_info['name']
            logging.info(f"Found destination bus stand: {dest_bus_stand_name} at {dest_bus_stand_coords}")
        else:
            dest_bus_stand_coords = None
            dest_bus_stand_name = None
            logging.info("No suitable destination bus stand found")
    else:
        dest_bus_stand_coords = None
        dest_bus_stand_name = None
        logging.info("Skipping destination bus stand search - missing city name or coordinates")

    # Prepare date formats
    try:
        date_obj = datetime.strptime(date_input, "%Y-%m-%d")
    except ValueError:
        return render_template_string(RESULTS_HTML,
                                      source_loc=source_input, destination_loc=dest_input,
                                      date_str=date_input, results=[], error="Invalid date format",
                                      sort_js=SORT_JS,
                                      source_city=source_city, source_city_coords=source_city_coords, source_bus_stand_coords=source_bus_stand_coords,
                                      destination_city=destination_city, dest_city_coords=dest_city_coords, dest_bus_stand_coords=dest_bus_stand_coords)
    date_bus_abhibus = date_obj.strftime("%d-%m-%Y")  # e.g. 27-06-2025
    date_redbus = date_obj.strftime("%d-%b-%Y")       # e.g. 27-Jun-2025
    date_tnstc = date_obj.strftime("%d/%m/%Y")        # e.g. 27/06/2025

    # Load station codes if PDF exists
    station_data = {}
    pdf_path = "/app/Station_code.pdf"
    if os.path.exists(pdf_path):
        logging.info(f"Loading station codes from: {pdf_path}")
        station_data = extract_station_codes(pdf_path)
        logging.info(f"Loaded {len(station_data)} station codes")
    else:
        logging.warning(f"Station code PDF not found at: {pdf_path}")

    results = []
    seen = set()

    # Determine nearby source & dest train stations with larger radius
    src_train_pts = find_nearby_transport(source_coords, 'train', radius=20000)  # 20km radius
    dest_train_pts = find_nearby_transport(dest_coords, 'train', radius=20000)
    src_station = src_train_pts[0] if src_train_pts else None
    dest_station = dest_train_pts[0] if dest_train_pts else None

    # Special handling for Chennai stations
    if src_station and 'CHENNAI' in src_station['name'].upper():
        src_station['name'] = "CHENNAI EGMORE"  # Default to Egmore
        logging.info(f"Changed source station to Chennai Egmore")
    
    if dest_station and 'CHENNAI' in dest_station['name'].upper():
        dest_station['name'] = "CHENNAI EGMORE"
        logging.info(f"Changed destination station to Chennai Egmore")

    # Calculate hub-to-hub distance (either bus stands or train stations)
    hub_to_hub_distance = None
    hub_to_hub_name = ""
    
    # For bus routes
    if source_bus_stand_coords and dest_bus_stand_coords:
        hub_to_hub_distance = geodesic(source_bus_stand_coords, dest_bus_stand_coords).km
        hub_to_hub_name = f"{source_bus_stand_name} to {dest_bus_stand_name}"
        logging.info(f"Bus hub-to-hub distance: {hub_to_hub_distance:.1f} km")
    
    # For train routes
    elif src_station and dest_station:
        hub_to_hub_distance = geodesic(src_station['coords'], dest_station['coords']).km
        hub_to_hub_name = f"{src_station['name']} to {dest_station['name']}"
        logging.info(f"Train station-to-station distance: {hub_to_hub_distance:.1f} km")


    # 1) Bus searches if mode includes bus
    if mode in ('bus','both'):
        # TNSTC: try combinations of city and "Bus Stand"
        tnstc_source_try = []
        tnstc_dest_try = []
        if source_city:
            tnstc_source_try.append(source_city)
            if source_bus_stand_info:
                tnstc_source_try.append(f"{source_city} Bus Stand")
        if destination_city:
            tnstc_dest_try.append(destination_city)
            if dest_bus_stand_info:
                tnstc_dest_try.append(f"{destination_city} Bus Stand")

        # Attempt TNSTC with each combination until results found
        tn_found = False
        for sc in tnstc_source_try:
            for dc in tnstc_dest_try:
                logging.info(f"Checking TNSTC direct schedules for '{sc}' -> '{dc}' on {date_tnstc}")
                tn_results = get_tnstc_bus_schedules(sc.lower().strip(), dc.lower().strip(), date_tnstc)
                if tn_results:
                    tn_found = True
                    for r in tn_results:
                        key = (r['provider'], r['operator'], r['departure'], r['arrival'])
                        if key in seen: continue
                        seen.add(key)
                        
                        # Build route steps
                        route_steps = build_route_steps(
                            source_input,
                            dest_input,
                            source_coords, 
                            source_bus_stand_coords if source_bus_stand_coords else source_city_coords, 
                            source_bus_stand_name or source_city,
                            hub_to_hub_distance or 0,
                            hub_to_hub_name or "Bus Journey",
                            dest_bus_stand_coords if dest_bus_stand_coords else dest_city_coords, 
                            dest_bus_stand_name or destination_city,
                            dest_coords,
                            is_bus=True,
                            departure_time=r['departure']
                        )
                        
                        # Calculate total cost and update route steps
                        total_cost, route_steps = calculate_total_fare(route_steps, r['fare'])
                        
                        entry = {
                            'provider': r['provider'],
                            'operator': r['operator'],
                            'departure': r['departure'],
                            'arrival': r['arrival'],
                            'duration': r.get('duration',''),
                            'fare': r.get('fare',''),
                            'total_cost': total_cost,
                            'route_details': generate_route_details(route_steps),
                            'booking_link': "https://www.tnstc.in"
                        }
                        results.append(entry)
                    break
            if tn_found:
                break
        if not tn_found:
            logging.info(f"TNSTC: no schedules found for any combination for '{source_city}' -> '{destination_city}'")

        # AbhiBus: try source_city then "Bus Stand", same for dest
        abhi_src_id = None
        abhi_dest_id = None
        # Try source variants
        if source_city:
            for sc in [source_city] + ([f"{source_city} Bus Stand"] if source_bus_stand_info else []):
                sc_l = sc.lower().strip()
                abhi_src_id = get_abhibus_city_id(sc_l)
                if abhi_src_id:
                    logging.info(f"AbhiBus: found city ID for source '{sc_l}': {abhi_src_id}")
                    break
        # Try dest variants
        if destination_city:
            for dc in [destination_city] + ([f"{destination_city} Bus Stand"] if dest_bus_stand_info else []):
                dc_l = dc.lower().strip()
                abhi_dest_id = get_abhibus_city_id(dc_l)
                if abhi_dest_id:
                    logging.info(f"AbhiBus: found city ID for dest '{dc_l}': {abhi_dest_id}")
                    break
        if abhi_src_id and abhi_dest_id:
            url_ab = f"https://www.abhibus.com/bus_search/{source_city.lower().strip()}/{abhi_src_id}/{destination_city.lower().strip()}/{abhi_dest_id}/{date_bus_abhibus}/O"
            logging.info(f"Checking AbhiBus direct schedules with URL: {url_ab}")
            abhi_results = scrape_abhibus_results(url_ab)
            for r in abhi_results:
                key = (r['provider'], r['operator'], r['departure'], r['arrival'])
                if key in seen: continue
                seen.add(key)
                
                # Build route steps
                route_steps = build_route_steps(
                    source_input,
                    dest_input,
                    source_coords, 
                    source_bus_stand_coords if source_bus_stand_coords else source_city_coords, 
                    source_bus_stand_name or source_city,
                    hub_to_hub_distance or 0,
                    hub_to_hub_name or "Bus Journey",
                    dest_bus_stand_coords if dest_bus_stand_coords else dest_city_coords, 
                    dest_bus_stand_name or destination_city,
                    dest_coords,
                    is_bus=True,
                    departure_time=r['departure']
                )
                
                # Calculate total cost and update route steps
                total_cost, route_steps = calculate_total_fare(route_steps, r['fare'])
                
                entry = {
                    'provider': r['provider'],
                    'operator': r['operator'],
                    'departure': r['departure'],
                    'arrival': r['arrival'],
                    'duration': r.get('duration',''),
                    'fare': r.get('fare',''),
                    'total_cost': total_cost,
                    'route_details': generate_route_details(route_steps),
                    'booking_link': r.get('booking_url')
                }
                results.append(entry)
        else:
            logging.info(f"AbhiBus fallback: could not obtain city IDs for '{source_city}' or '{destination_city}'")

        # RedBus: use city slugs
        try:
            src_rb = source_city.lower().strip().replace(' ', '-')
            dst_rb = destination_city.lower().strip().replace(' ', '-')
            rb_search_url = f"https://www.redbus.in/bus-tickets/{src_rb}-to-{dst_rb}/?fromCityName={source_city}&toCityName={destination_city}&onward={date_redbus}&doj={date_redbus}"
            logging.info(f"Checking RedBus direct schedules for {source_city.lower().strip()} -> {destination_city.lower().strip()} on {date_redbus}")
            html_rb = get_fully_scrolled_html(rb_search_url)
            rb_results = extract_redbus_details(html_rb)
            for r in rb_results:
                key = (r['provider'], r['operator'], r.get('departure',''), r.get('arrival',''))
                if key in seen: continue
                seen.add(key)
                
                # Build route steps
                route_steps = build_route_steps(
                    source_input,
                    dest_input,
                    source_coords, 
                    source_bus_stand_coords if source_bus_stand_coords else source_city_coords, 
                    source_bus_stand_name or source_city,
                    hub_to_hub_distance or 0,
                    hub_to_hub_name or "Bus Journey",
                    dest_bus_stand_coords if dest_bus_stand_coords else dest_city_coords, 
                    dest_bus_stand_name or destination_city,
                    dest_coords,
                    is_bus=True,
                    departure_time=r.get('departure')
                )
                
                # Calculate total cost and update route steps
                total_cost, route_steps = calculate_total_fare(route_steps, r.get('fare'))
                
                entry = {
                    'provider': r['provider'],
                    'operator': r['operator'],
                    'departure': r.get('departure',''),
                    'arrival': r.get('arrival',''),
                    'duration': r.get('duration',''),
                    'fare': r.get('fare',''),
                    'total_cost': total_cost,
                    'route_details': generate_route_details(route_steps),
                    'booking_link': rb_search_url
                }
                results.append(entry)
        except Exception as e:
            logging.error(f"RedBus error: {e}")

    # 2) Train direct search if mode includes train
    if mode in ('train','both') and src_station and dest_station and station_data:
        logging.info(f"Searching trains for stations: {src_station['name']} -> {dest_station['name']}")
        
        # Find station codes
        m_src = search_station(station_data, src_station['name'])
        m_dest = search_station(station_data, dest_station['name'])
        
        # Special handling for Chennai if no exact match
        if not m_src and 'CHENNAI' in src_station['name'].upper():
            # Try Chennai Central as fallback
            m_src = search_station(station_data, "CHENNAI CENTRAL")
            if not m_src:
                m_src = search_station(station_data, "CHENNAI EGMORE")
            if m_src:
                logging.info(f"Using Chennai station fallback: {m_src[0][0]}")
                
        if not m_dest and 'CHENNAI' in dest_station['name'].upper():
            m_dest = search_station(station_data, "CHENNAI CENTRAL")
            if not m_dest:
                m_dest = search_station(station_data, "CHENNAI EGMORE")
            if m_dest:
                logging.info(f"Using Chennai station fallback: {m_dest[0][0]}")
            
        if m_src and m_dest:
            src_code = m_src[0][1]
            dest_code = m_dest[0][1]
            date_irctc = date_obj.strftime("%Y%m%d")
            logging.info(f"Checking Train direct schedules from station {src_station['name']} ({src_code}) to {dest_station['name']} ({dest_code}) on {date_irctc}")
            api_resp = get_irctc_api_response(src_code, dest_code, date_irctc)
            train_results = parse_train_schedules(api_resp)
            
            for train in train_results:
                key = ('IRCTC', train['train_number'], train['departure_time'], train['arrival_time'])
                if key in seen: 
                    continue
                seen.add(key)
                
                # Format available classes
                classes_str = ', '.join(train['available_classes']) if train['available_classes'] else 'N/A'
                
                # Build route steps
                route_steps = build_route_steps(
                    source_input,
                    dest_input,
                    source_coords, 
                    src_station['coords'], 
                    src_station['name'],
                    hub_to_hub_distance or 0,
                    f"{src_station['name']} to {dest_station['name']}",
                    dest_station['coords'], 
                    dest_station['name'],
                    dest_coords,
                    is_bus=False,
                    departure_time=train['departure_time']
                    
                )
                
                # Calculate total cost and update route steps
                total_cost, route_steps = calculate_total_fare(route_steps, f"₹{train.get('fare', 'N/A')}")
                
                entry = {
                    'provider': 'IRCTC',
                    'operator': f"{train['train_number']} {train['train_name']} ({train['train_type']})",
                    'departure': train['departure_time'],
                    'arrival': train['arrival_time'],
                    'duration': train['duration'],
                    'fare': f"Classes: {classes_str}",
                    'total_cost': total_cost,
                    'route_details': generate_route_details(route_steps),
                    'booking_link': 'https://www.irctc.co.in/nget/train-search'
                }
                results.append(entry)
        else:
            if src_station and dest_station:
                logging.info(f"Train search: could not map station codes for '{src_station['name']}' or '{dest_station['name']}'")
            else:
                logging.info("Train search: missing nearby source or destination station")

    if not results:
        return render_template_string(RESULTS_HTML,
                                      source_loc=source_input, destination_loc=dest_input,
                                      date_str=date_input, results=[], error="No routes found with the current logic.",
                                      sort_js=SORT_JS,
                                      source_city=source_city, source_city_coords=source_city_coords, source_bus_stand_coords=source_bus_stand_coords,
                                      destination_city=destination_city, dest_city_coords=dest_city_coords, dest_bus_stand_coords=dest_bus_stand_coords)

    return render_template_string(RESULTS_HTML,
                                  source_loc=source_input, destination_loc=dest_input,
                                  date_str=date_input, results=results, error=None,
                                  sort_js=SORT_JS,
                                  source_city=source_city, source_city_coords=source_city_coords, source_bus_stand_coords=source_bus_stand_coords,
                                  destination_city=destination_city, dest_city_coords=dest_city_coords, dest_bus_stand_coords=dest_bus_stand_coords)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)