import re
import math
import time
from geopy.distance import geodesic
from bs4 import BeautifulSoup
from fuzzywuzzy import process, fuzz
from geopy.exc import GeocoderTimedOut
from geopy.geocoders import Nominatim
import requests
from utils import haversine_distance, normalize_stop_name, get_min_max_fare, replace_bus_terminal_names, get_transport_icon
from geopy.geocoders import Nominatim
import overpy

# Initialize geocoder
geolocator = Nominatim(user_agent="mtc_bus_finder")

# MTC route data and cache
routes = {}
stop_routes = {}
all_stops = set()
FARE_CACHE = None
stop_coords_cache = {}

def load_mtc_routes():
    global routes, stop_routes, all_stops
    print("Fetching bus routes...")
    URL = "https://greenmesg.org/dictionary/routes/chennai_bus_routes.txt?161011"
    try:
        resp = requests.get(URL, timeout=5)
        resp.raise_for_status()
        for line in resp.text.splitlines():
            line = line.strip()
            if not line or ':' not in line:
                continue
            rno, stops_str = line.split(':', 1)
            stops = [normalize_stop_name(s.strip()) for s in stops_str.split(',') if s.strip()]
            routes[rno.strip().upper()] = stops
            all_stops.update(stops)
        
        # Precompute stop_routes
        for route, stops in routes.items():
            for stop in stops:
                if stop not in stop_routes:
                    stop_routes[stop] = set()
                stop_routes[stop].add(route)
                
    except Exception as e:
        print(f"Error fetching bus routes: {str(e)}")
        raise

def routes_serving(stop):
    return list(stop_routes.get(normalize_stop_name(stop), set()))

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
    """Return the fare for the given number of stages, using the max stage if stages exceed max_stage."""
    if not fare_dict:  # Handle empty fare dictionary
        return 0
    if stages >= max_stage:
        return fare_dict[max_stage]
    return fare_dict.get(stages, fare_dict[max(fare_dict.keys())])

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

# Global cache for MTC routes
mtc_route_cache = {}

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

    # Initialize display names with proper default values
    source_hub_display = replace_bus_terminal_names(source_hub_name)
    if source_hub_name == "CHENNAI EGMORE":
        source_hub_display = "EGMORE"
    elif source_hub_name == "CHENNAI CENTRAL":
        source_hub_display = "CENTRAL"
    
    dest_hub_display = replace_bus_terminal_names(dest_hub_name)
    if dest_hub_name == "CHENNAI EGMORE":
        dest_hub_display = "EGMORE"
    elif dest_hub_name == "CHENNAI CENTRAL":
        dest_hub_display = "CENTRAL"

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
    mode = "bus"
    cache_key = (source_hub_name, dest_hub_name, mode)

    # First mile distance calculation
    first_mile_dist = geodesic(source_coords, source_hub_coords).km
    first_mode = "walk" if first_mile_dist <= 1 else "auto"
    first_fare = get_min_max_fare(first_mile_dist, is_night) if first_mode == "auto" else 0
    first_mile_url = (f"https://www.google.com/maps/dir/?api=1&origin={source_coords[0]},{source_coords[1]}"
                      f"&destination={source_hub_coords[0]},{source_hub_coords[1]}&travelmode=driving")

    # ====== FIRST MILE HANDLING ======
    is_special_source = (
        (is_bus and source_hub_display == "C.M.B.T") or 
        (not is_bus and source_hub_display == "EGMORE")
    )
    
    if is_special_source:
        # Check cache for MTC routes
        if cache_key in mtc_route_cache:
            all_options, start_bus, end_bus = mtc_route_cache[cache_key]
        else:
            # Find MTC routes from source location to hub
            all_options, start_bus, end_bus = find_mtc_routes(
                source_input, 
                source_hub_display,
                source_coords, 
                source_hub_coords
            )
            mtc_route_cache[cache_key] = (all_options, start_bus, end_bus)
        
        # First mile to source stop
        if start_bus:
            start_mile_dist = geodesic(source_coords, start_bus[0]["coords"]).km if start_bus[0].get("coords") else first_mile_dist
            start_mode = "walk" if start_mile_dist <= 1 else "auto"
            start_fare = get_min_max_fare(start_mile_dist, is_night) if start_mode == "auto" else 0
            start_mile_url = (f"https://www.google.com/maps/dir/?api=1&origin={source_coords[0]},{source_coords[1]}"
                              f"&destination={start_bus[0]['coords'][0]},{start_bus[0]['coords'][1]}&travelmode=driving"
                              if start_bus[0].get("coords") else first_mile_url)
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
        
        # Add MTC route to hub
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

    # ====== MAIN TRANSPORT ======
    steps.append({
        "mode": "bus",
        "description": f"{'Bus'} to {dest_hub_display}",
        "distance_km": round(hub_to_hub_distance, 1) if hub_to_hub_distance else None,
        "fare": None,
        "map_url": None
    })

    # ====== LAST MILE HANDLING ======
    is_special_destination = (
        (is_bus and dest_hub_display == "C.M.B.T")
    )
    
    if is_special_destination:
        mtc_start = dest_hub_display
        # Check cache for MTC routes
        if cache_key in mtc_route_cache:
            all_options, start_bus, end_bus = mtc_route_cache[cache_key]
        else:
            # Find MTC routes from hub to destination
            all_options, start_bus, end_bus = find_mtc_routes(
                mtc_start, 
                dest_input,
                dest_hub_coords, 
                dest_coords
            )
            mtc_route_cache[cache_key] = (all_options, start_bus, end_bus)
        
        # Add MTC routes from hub
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
                    # Update destination coordinates for final last mile
                    if option['end'].get('coords'):
                        dest_hub_coords = option['end']['coords']
                        dest_hub_display = option['end']['name']
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
                    # Update destination coordinates for final last mile
                    if option['end'].get('coords'):
                        dest_hub_coords = option['end']['coords']
                        dest_hub_display = option['end']['name']

    # ====== FINAL LAST MILE ======
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
        if step['mode'] in ('bus') and step.get('fare') is None:
            step['fare'] = provider_value
            total_min += provider_value
            total_max += provider_value
            break
    return f"₹{total_min:.0f} - ₹{total_max:.0f}", route_steps


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


