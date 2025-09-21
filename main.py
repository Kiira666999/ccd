import time
import os
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from datetime import datetime, timedelta
from threading import Thread

# CONFIG: list of sites to monitor
# For each site: url, check_interval_seconds, needs_js (True -> use Selenium)
SITES = [
    {"name": "StaticSite", "url": "https://example.com/static", "interval": 300, "needs_js": False},
    {"name": "JSApp",      "url": "https://example.com/dynamic", "interval": 600, "needs_js": True},
    # add more...
]

# Simple storage of last run times / last ETag / last content-hash
STATE = {site["name"]: {"last_run": None, "etag": None, "last_hash": None} for site in SITES}

# -------------------------
# Selenium driver (single shared instance)
# -------------------------
def make_driver():
    CHROME_BIN = "/usr/bin/chromium"         # adjust for your env
    CHROMEDRIVER = "/usr/bin/chromedriver"   # adjust for your env

    options = Options()
    options.headless = True  # headless mode
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1200,800")
    # disable images, CSS, fonts to save CPU/bandwidth
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.managed_default_content_settings.stylesheets": 2,
        "profile.managed_default_content_settings.fonts": 2,
    }
    options.add_experimental_option("prefs", prefs)
    options.binary_location = CHROME_BIN

    service = Service(CHROMEDRIVER)
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(30)
    return driver

driver = None

def ensure_driver():
    global driver
    if driver is None:
        try:
            driver = make_driver()
        except Exception as e:
            print("Failed to start driver:", e)
            driver = None
    return driver

# -------------------------
# Small helpers
# -------------------------
def simple_hash(text: str) -> int:
    # cheap change-detection
    return hash(text) & 0xffffffff

def check_with_requests(site):
    """Use requests + conditional GET if possible"""
    headers = {}
    state = STATE[site["name"]]
    if state.get("etag"):
        headers["If-None-Match"] = state["etag"]
    try:
        r = requests.get(site["url"], headers=headers, timeout=20)
        if r.status_code == 304:
            return False, "not modified"
        if r.status_code != 200:
            return False, f"status {r.status_code}"
        # optionally parse small extract to detect changes
        text = r.text
        h = simple_hash(text[:5000])  # use prefix to be faster
        if state.get("last_hash") == h:
            return False, "no change"
        # update state
        state["last_hash"] = h
        if "ETag" in r.headers:
            state["etag"] = r.headers["ETag"]
        return True, "changed"
    except Exception as e:
        return False, f"error: {e}"

def check_with_selenium(site):
    d = ensure_driver()
    if d is None:
        return False, "no driver"
    try:
        d.get(site["url"])
        time.sleep(1)  # short wait for JS to render; tune as needed
        html = d.page_source
        h = simple_hash(html[:10000])
        state = STATE[site["name"]]
        if state.get("last_hash") == h:
            return False, "no change"
        state["last_hash"] = h
        return True, "changed"
    except Exception as e:
        # on fatal driver errors, try to restart driver
        print("Selenium error:", e)
        try:
            d.quit()
        except:
            pass
        # mark driver to None so ensure_driver will recreate it next time
        global driver
        driver = None
        return False, f"error: {e}"

# -------------------------
# Main scheduler loop (sequential)
# -------------------------
def monitor_loop():
    # stagger start times slightly to avoid ping storms
    stagger = 0
    for site in SITES:
        time.sleep(stagger)
        stagger = 1

    while True:
        start = datetime.utcnow()
        for site in SITES:
            name = site["name"]
            state = STATE[name]
            now = datetime.utcnow()
            last = state.get("last_run") or (now - timedelta(days=1))
            # if enough time passed since last_run
            if (now - last).total_seconds() >= site["interval"]:
                state["last_run"] = now
                print(f"[{now.isoformat()}] Checking {name} ({site['url']})")
                if not site["needs_js"]:
                    changed, reason = check_with_requests(site)
                else:
                    changed, reason = check_with_selenium(site)
                if changed:
                    print(f" -> CHANGE detected for {name}: {reason}")
                    # here you can notify (Discord webhook, email, push, etc.)
                else:
                    print(f" -> No change for {name}: {reason}")

                # small pause between sites to reduce burst CPU / network
                time.sleep(2)
        # sleep a small amount before next round (tune as needed)
        elapsed = (datetime.utcnow() - start).total_seconds()
        to_sleep = max(5, 1)  # minimal sleep to avoid busy loop
        time.sleep(to_sleep)

# -------------------------
# Keep-alive server (for Replit + UptimeRobot)
# -------------------------
from flask import Flask
app = Flask("")
@app.route("/")
def home():
    return "alive"

def run_flask():
    app.run(host="0.0.0.0", port=8080)

if __name__ == "__main__":
    # start flask in background
    t = Thread(target=run_flask, daemon=True)
    t.start()
    # start monitor loop
    monitor_loop()
