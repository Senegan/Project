import requests
import logging
from bs4 import BeautifulSoup

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