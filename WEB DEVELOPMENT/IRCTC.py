import re
import logging
import time
import requests
import PyPDF2

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
