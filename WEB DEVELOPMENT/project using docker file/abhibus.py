import requests
import logging
import re
import tempfile
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.action_chains import ActionChains
import os
import time
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException

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
            executable_path=r'/app/webdriver/msedgedriver',
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

def get_abhibus_schedules(search_url):
    return scrape_abhibus_results(search_url)