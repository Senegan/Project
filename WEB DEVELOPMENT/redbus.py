from selenium import webdriver
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from bs4 import BeautifulSoup
import re
import time
import tempfile
import os
import logging

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
            executable_path=r'S:\Project\example\webdriver\msedgedriver.exe',  # Docker path
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

def get_redbus_schedules(url):
    """Get RedBus schedules from search URL"""
    html = get_fully_scrolled_html(url)
    return extract_redbus_details(html)