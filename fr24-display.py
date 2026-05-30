#!/usr/bin/env python3

import os
import sys
import subprocess

# ─── Bootstrap ───────────────────────────────────────────────────────────────
# Re-exec under the venv Python if we were launched with the system Python
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_venv_py = os.path.join(BASE_DIR, '.venv', 'bin', 'python')
if os.path.exists(_venv_py) and sys.executable != _venv_py:
    os.execv(_venv_py, [_venv_py] + sys.argv)

# Auto-install any missing requirements before other imports
subprocess.check_call(
    [sys.executable, '-m', 'pip', 'install', '-q', '-r',
     os.path.join(BASE_DIR, 'requirements.txt')]
)

# ─── Imports ─────────────────────────────────────────────────────────────────
import csv
import json
import math
import time
import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

import requests
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from geopy.distance import geodesic
from dotenv import load_dotenv
import spidev as SPI
from lib import LCD_2inch4

# ─── Config ──────────────────────────────────────────────────────────────────
load_dotenv(os.path.join(BASE_DIR, '.env'))

_lat = os.getenv('LAT')
_lon = os.getenv('LON')
if not _lat or not _lon:
    logging.error('LAT and LON must be set in .env — copy .env.example to .env and fill in your coordinates')
    sys.exit(1)
HERE_LAT  = float(_lat)
HERE_LON  = float(_lon)
RADIUS_KM = float(os.getenv('RADIUS_KM',   '50'))
ADSB_URL  = os.getenv('ADSB_URL',           'http://localhost:8080/data/aircraft.json')
REFRESH   = int(os.getenv('REFRESH_RATE',   '30'))

DATA_DIR       = os.path.join(BASE_DIR, 'data')
AIRPORTS_FILE  = os.path.join(DATA_DIR, 'airports.csv')
AIRPORTS_URL   = 'https://davidmegginson.github.io/ourairports-data/airports.csv'
STATIC_MAX_AGE = 7 * 24 * 3600   # refresh static data files weekly

CACHE_TTL_AIRCRAFT = 24 * 3600   # aircraft metadata rarely changes
CACHE_TTL_ROUTE    =  6 * 3600   # route per-flight, shorter TTL
CACHE_TTL_PHOTO    = 24 * 3600

# ─── ICAO aircraft type → model name ─────────────────────────────────────────
AIRCRAFT_TYPES = {
    "A19N": "Airbus A320neo",    "A20N": "Airbus A320neo",    "A21N": "Airbus A321neo",
    "A306": "Airbus A300-600",   "A310": "Airbus A310",
    "A318": "Airbus A318",       "A319": "Airbus A319",
    "A320": "Airbus A320",       "A321": "Airbus A321",
    "A332": "Airbus A330-200",   "A333": "Airbus A330-300",
    "A338": "Airbus A330-800neo","A339": "Airbus A330-900neo",
    "A342": "Airbus A340-200",   "A343": "Airbus A340-300",
    "A345": "Airbus A340-500",   "A346": "Airbus A340-600",
    "A359": "Airbus A350-900",   "A35K": "Airbus A350-1000",
    "A388": "Airbus A380-800",   "A380": "Airbus A380",
    "AT43": "ATR 42-300",        "AT45": "ATR 42-500",        "AT46": "ATR 42-600",
    "AT72": "ATR 72",            "AT73": "ATR 72-200",        "AT75": "ATR 72-500",
    "AT76": "ATR 72-600",
    "B712": "Boeing 717-200",
    "B732": "Boeing 737-200",    "B733": "Boeing 737-300",    "B734": "Boeing 737-400",
    "B735": "Boeing 737-500",    "B736": "Boeing 737-600",    "B737": "Boeing 737-700",
    "B738": "Boeing 737-800",    "B739": "Boeing 737-900",
    "B37M": "Boeing 737 MAX 7",  "B38M": "Boeing 737 MAX 8",
    "B39M": "Boeing 737 MAX 9",  "B3XM": "Boeing 737 MAX 10",
    "B741": "Boeing 747-100",    "B742": "Boeing 747-200",
    "B743": "Boeing 747-300",    "B744": "Boeing 747-400",    "B748": "Boeing 747-8",
    "B752": "Boeing 757-200",    "B753": "Boeing 757-300",
    "B762": "Boeing 767-200",    "B763": "Boeing 767-300",    "B764": "Boeing 767-400",
    "B772": "Boeing 777-200",    "B77L": "Boeing 777-200LR",
    "B773": "Boeing 777-300",    "B77W": "Boeing 777-300ER",
    "B778": "Boeing 777X-8",     "B779": "Boeing 777X-9",
    "B788": "Boeing 787-8",      "B789": "Boeing 787-9",      "B78X": "Boeing 787-10",
    "BCS1": "Airbus A220-100",   "BCS3": "Airbus A220-300",
    "CRJ1": "Bombardier CRJ-100","CRJ2": "Bombardier CRJ-200",
    "CRJ7": "Bombardier CRJ-700","CRJ9": "Bombardier CRJ-900","CRJX": "Bombardier CRJ-1000",
    "DH8A": "Dash 8-100",        "DH8B": "Dash 8-200",
    "DH8C": "Dash 8-300",        "DH8D": "Dash 8-400",
    "E135": "Embraer ERJ-135",   "E145": "Embraer ERJ-145",
    "E170": "Embraer 170",       "E175": "Embraer 175",
    "E190": "Embraer 190",       "E195": "Embraer 195",
    "E290": "Embraer E190-E2",   "E295": "Embraer E195-E2",
    "F50":  "Fokker 50",         "F70":  "Fokker 70",         "F100": "Fokker 100",
    "MD11": "McDonnell Douglas MD-11",
    "MD81": "MD-81",             "MD82": "MD-82",             "MD83": "MD-83",
    "MD88": "MD-88",             "MD90": "MD-90",
    "RJ85": "Avro RJ85",         "RJ1H": "Avro RJ100",
    "SU95": "Sukhoi Superjet 100",
    "T154": "Tupolev Tu-154",    "T204": "Tupolev Tu-204",
    "SF34": "Saab SF340",
    "GL5T": "Global 5000",       "GL7T": "Global 7500",       "GLEX": "Global Express",
    "GLF4": "Gulfstream IV",     "GLF5": "Gulfstream V",      "GLF6": "Gulfstream G650",
    "G150": "Gulfstream G150",   "G280": "Gulfstream G280",
    "G450": "Gulfstream G450",   "G550": "Gulfstream G550",   "G600": "Gulfstream G600",
    "C208": "Cessna Caravan",    "C510": "Citation Mustang",  "C525": "CitationJet CJ1",
    "C55B": "Citation Bravo",    "C560": "Citation V",        "C56X": "Citation Excel",
    "C680": "Citation Sovereign","C68A": "Citation Sovereign+","C700": "Citation Longitude",
    "C750": "Citation X",
    "CL30": "Challenger 300",    "CL35": "Challenger 350",    "CL60": "Challenger 650",
    "F2TH": "Dassault Falcon 2000","F900": "Dassault Falcon 900","FA50": "Dassault Falcon 50",
    "LJ45": "Learjet 45",        "LJ60": "Learjet 60",        "LJ75": "Learjet 75",
    "E50P": "Embraer Phenom 100","E55P": "Embraer Phenom 300",
    "BE40": "Hawker 400",        "H25B": "Hawker 800",
    "BE20": "King Air 200",      "B350": "King Air 350",
    "PC12": "Pilatus PC-12",     "PC24": "Pilatus PC-24",
    "TBM7": "Daher TBM 700",     "TBM8": "Daher TBM 850/900", "TBM9": "Daher TBM 930/940",
    "P180": "Piaggio P.180 Avanti",
    "C172": "Cessna 172 Skyhawk","C182": "Cessna 182 Skylane","C152": "Cessna 152",
    "PA28": "Piper PA-28",       "PA46": "Piper Meridian",
    "SR20": "Cirrus SR20",       "SR22": "Cirrus SR22",       "SF50": "Cirrus Vision Jet",
    "DA40": "Diamond DA40",      "DA42": "Diamond DA42",      "DA62": "Diamond DA62",
    "EC35": "Airbus H135",       "EC45": "Airbus H145",
    "AS50": "Airbus H125",       "AS65": "Airbus H155 Dauphin",
    "S76":  "Sikorsky S-76",     "S92":  "Sikorsky S-92",
    "AW39": "Leonardo AW139",    "AW16": "Leonardo AW169",
    "R22":  "Robinson R22",      "R44":  "Robinson R44",      "R66":  "Robinson R66",
    "B06":  "Bell 206 JetRanger","B412": "Bell 412",          "B429": "Bell 429",
    "C130": "Lockheed C-130 Hercules",
    "IL76": "Ilyushin Il-76",    "A124": "Antonov An-124",
    "B461": "BAe 146-100",       "B462": "BAe 146-200",       "B463": "BAe 146-300",
}

# ─── In-memory cache ──────────────────────────────────────────────────────────
_cache: dict = {}

def cache_get(key):
    entry = _cache.get(key)
    if entry and time.time() < entry['exp']:
        return entry['val']
    return None

def cache_set(key, val, ttl):
    _cache[key] = {'val': val, 'exp': time.time() + ttl}

# ─── Geometry helpers ────────────────────────────────────────────────────────
def bearing_to(lat1, lon1, lat2, lon2):
    """True bearing (degrees, 0–359) from (lat1,lon1) to (lat2,lon2)."""
    lat1, lon1 = math.radians(lat1), math.radians(lon1)
    lat2, lon2 = math.radians(lat2), math.radians(lon2)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return int((math.degrees(math.atan2(x, y)) + 360) % 360)

# ─── Static data management ───────────────────────────────────────────────────
def _needs_refresh(path):
    if not os.path.exists(path):
        return True
    return (time.time() - os.path.getmtime(path)) > STATIC_MAX_AGE

def _download(url, dest):
    logging.info(f'Refreshing {os.path.basename(dest)}...')
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'wb') as f:
            f.write(r.content)
        logging.info(f'Saved {os.path.basename(dest)} ({len(r.content):,} bytes)')
    except Exception as e:
        logging.warning(f'Could not download {url}: {e}')

def refresh_static_data():
    if _needs_refresh(AIRPORTS_FILE):
        _download(AIRPORTS_URL, AIRPORTS_FILE)

def load_airports():
    """Build IATA → 'Airport Name (Municipality)' lookup from OurAirports airports.csv.
    Columns: id,ident,type,name,lat,lon,elev,continent,country,region,municipality,
             scheduled_service,gps_code,iata_code,local_code,home_link,wikipedia_link,keywords
    """
    lookup = {}
    if not os.path.exists(AIRPORTS_FILE):
        logging.warning('airports.csv not found — airport names will show as IATA codes')
        return lookup
    with open(AIRPORTS_FILE, encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            iata = row.get('iata_code', '').strip()
            name = row.get('name', '').strip()
            city = row.get('municipality', '').strip()
            if not iata:
                continue
            label = f'{name} ({city})' if city and city not in name else name
            lookup[iata.upper()] = label
    logging.info(f'Loaded {len(lookup)} airports')
    return lookup

# ─── API fetchers ─────────────────────────────────────────────────────────────
_HTTP = requests.Session()
_HTTP.headers.update({'User-Agent': 'fr24-display/2.0 (github.com/timhanley/fr24-display)'})

def fetch_local_aircraft():
    """Fetch the live aircraft list from the local ADS-B receiver.
    Supports both HTTP URLs and local file paths."""
    try:
        if ADSB_URL.startswith('http'):
            r = _HTTP.get(ADSB_URL, timeout=5)
            r.raise_for_status()
            data = r.json()
        else:
            with open(ADSB_URL, 'r') as f:
                data = json.load(f)
        return data.get('aircraft', [])
    except Exception as e:
        logging.warning(f'ADS-B fetch failed: {e}')
        return []

def fetch_adsbdb_aircraft(hex_code):
    """
    Aircraft metadata from adsbdb.com by ICAO hex.
    Returns the 'aircraft' dict, or {} on miss.
    Cached 24 h on hit, 1 h on miss (self-heals quickly).
    """
    key = f'adsbdb_ac_{hex_code}'
    cached = cache_get(key)
    if cached is not None:
        return cached
    result = {}
    try:
        r = _HTTP.get(f'https://api.adsbdb.com/v0/aircraft/{hex_code}', timeout=8)
        if r.status_code == 200:
            result = (r.json().get('response', {}) or {}).get('aircraft', {}) or {}
    except Exception as e:
        logging.warning(f'adsbdb aircraft lookup failed ({hex_code}): {e}')
    cache_set(key, result, CACHE_TTL_AIRCRAFT if result else 3600)
    return result

def fetch_adsbdb_route(callsign):
    """
    Route data from adsbdb.com by callsign (dedicated /v0/callsign/ endpoint).
    Returns the 'flightroute' dict, or {} on miss.
    Cached 6 h on hit, 1 h on miss.
    """
    key = f'adsbdb_route_{callsign}'
    cached = cache_get(key)
    if cached is not None:
        return cached
    result = {}
    try:
        r = _HTTP.get(f'https://api.adsbdb.com/v0/callsign/{callsign}', timeout=8)
        if r.status_code == 200:
            result = (r.json().get('response', {}) or {}).get('flightroute', {}) or {}
    except Exception as e:
        logging.warning(f'adsbdb route lookup failed ({callsign}): {e}')
    cache_set(key, result, CACHE_TTL_ROUTE if result else 3600)
    return result

def fetch_photo_url(hex_code, registration=''):
    """
    Fetch an aircraft thumbnail from airport-data.com by ICAO hex.
    Cached 24 h on hit, 1 h on miss so transient failures self-heal.
    Returns a URL string or empty string.
    """
    key = f'photo_{hex_code}'
    cached = cache_get(key)
    if cached is not None:
        return cached
    url = ''
    try:
        params = {'m': hex_code, 'n': 1}
        if registration:
            params['r'] = registration
        r = _HTTP.get('https://airport-data.com/api/ac_thumb.json', params=params, timeout=8)
        if r.status_code == 200:
            data = r.json().get('data', [])
            if data:
                url = data[0].get('image', '')
    except Exception as e:
        logging.warning(f'airport-data photo lookup failed ({hex_code}): {e}')
    cache_set(key, url, CACHE_TTL_PHOTO if url else 3600)
    return url

def fetch_airportdata_info(hex_code):
    """
    Aircraft info from airport-data.com by ICAO hex.
    Returns dict with 'model', 'icao_model', 'reg', 'country', or {} on miss.
    Cached 24 h on hit, 1 h on miss.
    """
    key = f'adinfo_{hex_code}'
    cached = cache_get(key)
    if cached is not None:
        return cached
    result = {}
    try:
        r = _HTTP.get(f'https://airport-data.com/api/ac_info.json?m={hex_code}', timeout=8)
        if r.status_code == 200:
            result = r.json() or {}
    except Exception as e:
        logging.warning(f'airport-data info lookup failed ({hex_code}): {e}')
    cache_set(key, result, CACHE_TTL_AIRCRAFT if result else 3600)
    return result

def fetch_image(url):
    """Download an image from a URL and return a PIL Image, or None on failure.
    Cached by URL for 24 h so the same photo isn't re-downloaded every render cycle."""
    key = f'img_{url}'
    cached = cache_get(key)
    if cached is not None:
        return cached
    try:
        r = _HTTP.get(url, timeout=10)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert('RGB')
        cache_set(key, img, CACHE_TTL_PHOTO)
        return img
    except Exception as e:
        logging.warning(f'Image download failed: {e}')
        return None

# ─── Image utilities ──────────────────────────────────────────────────────────
def fit_to_box(img, w, h):
    """Scale and centre-crop image to exactly w×h pixels."""
    src_ratio = img.width / img.height
    tgt_ratio = w / h
    if src_ratio > tgt_ratio:
        new_w, new_h = int(src_ratio * h), h
    else:
        new_w, new_h = w, int(w / src_ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top  = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))

def truncate(text, font, max_px):
    """Truncate text with ellipsis to fit within max_px width."""
    if not text:
        return text
    if font.getlength(text) <= max_px:
        return text
    while text and font.getlength(text + '…') > max_px:
        text = text[:-1]
    return text + '…'

# ─── Display setup ────────────────────────────────────────────────────────────
RST, DC, BL, bus, device = 27, 25, 18, 0, 0

try:
    disp = LCD_2inch4.LCD_2inch4(
        spi=SPI.SpiDev(bus, device), spi_freq=10000000, rst=RST, dc=DC, bl=BL
    )
    disp.Init()
    disp.clear()
    FONT_PATH = os.path.join(BASE_DIR, 'Font', 'helvetica.ttf')
    f20 = ImageFont.truetype(FONT_PATH, 20)
    f18 = ImageFont.truetype(FONT_PATH, 18)
    f16 = ImageFont.truetype(FONT_PATH, 16)
    f14 = ImageFont.truetype(FONT_PATH, 14)
    DW = disp.height   # 320px — landscape width
    DH = disp.width    # 240px — landscape height
except IOError as e:
    logging.error(f'Display init failed: {e}')
    sys.exit(1)
except KeyboardInterrupt:
    disp.module_exit()
    sys.exit(0)

# Layout constants
PHOTO_H = 77     # photo occupies the top 77px (no gap below)

# Colour palette
C_BG      = '#0b0d17'   # deep dark blue-black
C_WHITE   = '#ffffff'
C_SILVER  = '#cccccc'
C_ACCENT  = '#5bc8f5'   # sky blue for data values
C_ORIGIN  = '#ff6b6b'   # soft red
C_DEST    = '#69db7c'   # soft green
C_AMBER   = '#ffd43b'   # amber for distance
C_DIVIDER = '#2c3045'   # subtle divider lines
C_DIM     = '#6677aa'   # dimmed text for "no data" placeholders


def _new_frame():
    return Image.new('RGB', (DW, DH), C_BG)


def _divider(draw, y):
    draw.line([(6, y), (DW - 6, y)], fill=C_DIVIDER, width=1)


def render_no_aircraft(message='No aircraft nearby'):
    img  = _new_frame()
    draw = ImageDraw.Draw(img)
    icon_path = os.path.join(BASE_DIR, 'images', 'error.png')
    if os.path.exists(icon_path):
        icon = Image.open(icon_path).convert('RGB').resize((72, 72), Image.LANCZOS)
        img.paste(icon, ((DW - 72) // 2, 72))
    draw.text((DW // 2, 168), message, fill=C_ORIGIN, font=f18, anchor='mm')
    disp.ShowImage(img)


def render_aircraft(f):
    """
    Layout (320 × 240 px, landscape):

    y=  0–77   Photo (full width, cropped) — no gap after
    y= 77–102  AIRLINE (f20, white)  |  CALLSIGN (f18, accent, right)
    y=102–120  AIRCRAFT MODEL (f16, silver)  |  REG (f16, accent, right)
    y=120      ─── divider ───
    y=123–145  ORIGIN (f18, red)   — or dim placeholder if unknown
    y=145–167  DEST (f18, green)   — or dim placeholder if unknown
    y=167      ─── divider ───
    y=170–192  ALT ft (f18, accent)  |  VS fpm (f18, accent, right)
    y=192–214  SPD kts (f18, accent)  |  HDG° (f18, accent, right)
    y=214      ─── divider ───
    y=217–240  DISTANCE (f20, amber, centred)
    """
    img  = _new_frame()
    draw = ImageDraw.Draw(img)

    # ── Photo (full-width, 77 px tall, no gap after) ──────────────────────────
    photo = fetch_image(f['photo_url']) if f.get('photo_url') else None
    if photo:
        photo = fit_to_box(photo, DW, PHOTO_H)
    else:
        fallback = os.path.join(BASE_DIR, 'images', 'noimage.png')
        photo = Image.open(fallback).convert('RGB')
        photo = fit_to_box(photo, DW, PHOTO_H)
    img.paste(photo, (0, 0))

    # Fade the bottom 10 px of the photo into the background
    bg_r, bg_g, bg_b = int(C_BG[1:3], 16), int(C_BG[3:5], 16), int(C_BG[5:7], 16)
    for i in range(10):
        alpha = i / 9
        r1, g1, b1 = photo.getpixel((DW // 2, PHOTO_H - 10 + i))
        blend = (
            int(r1 + (bg_r - r1) * alpha),
            int(g1 + (bg_g - g1) * alpha),
            int(b1 + (bg_b - b1) * alpha),
        )
        draw.line([(0, PHOTO_H - 10 + i), (DW, PHOTO_H - 10 + i)], fill=blend)

    y = PHOTO_H                                       # 77 — no gap

    # ── Airline  |  Callsign  (y = 77) ───────────────────────────────────────
    airline  = f.get('airline', '') or 'Unknown operator'
    airline  = truncate(airline, f20, DW - 96)
    callsign = f.get('callsign', '') or '—'
    draw.text((6, y),      airline,  fill=C_WHITE,  font=f20)
    draw.text((DW - 6, y), callsign, fill=C_ACCENT, font=f18, anchor='ra')
    y += 25                                           # 102

    # ── Model  |  Registration  (y = 102) ────────────────────────────────────
    model = f.get('aircraft_model', '') or 'Unknown type'
    model = truncate(model, f16, DW - 96)
    reg   = f.get('registration', '') or ''
    draw.text((6, y),      model, fill=C_SILVER, font=f16)
    if reg:
        draw.text((DW - 6, y), reg, fill=C_ACCENT, font=f16, anchor='ra')
    y += 18                                           # 120

    _divider(draw, y); y += 3                         # 123

    # ── Origin  (y = 123) ────────────────────────────────────────────────────
    origin = f.get('origin', '') or ''
    dest   = f.get('destination', '') or ''

    if origin:
        draw.text((6, y), truncate(origin, f18, DW - 12), fill=C_ORIGIN, font=f18)
    elif not dest:
        draw.text((6, y), 'No route data', fill=C_DIM, font=f16)
    else:
        draw.text((6, y), 'Origin unknown', fill=C_DIM, font=f16)
    y += 22                                           # 145

    # ── Destination  (y = 145) ───────────────────────────────────────────────
    if dest:
        draw.text((6, y), truncate(dest, f18, DW - 12), fill=C_DEST, font=f18)
    elif origin:
        draw.text((6, y), 'Destination unknown', fill=C_DIM, font=f16)
    # If neither known, leave this line blank (already shown "No route data" above)
    y += 22                                           # 167

    _divider(draw, y); y += 3                         # 170

    # ── Altitude  |  Vertical speed  (y = 170) ───────────────────────────────
    # Use ASCII ^ v ~ instead of Unicode arrows (not in all fonts)
    alt = f.get('altitude', 0)
    vs  = f.get('vspeed', 0)
    vs_sym = '^' if vs > 50 else ('v' if vs < -50 else '~')
    draw.text((6, y),      f'{alt:,} ft',                 fill=C_ACCENT, font=f18)
    draw.text((DW - 6, y), f'{vs_sym} {abs(vs):,} fpm',   fill=C_ACCENT, font=f18, anchor='ra')
    y += 22                                           # 192

    # ── Speed  |  Track  (y = 192) ───────────────────────────────────────────
    spd = f.get('speed', 0)
    trk = f.get('track', 0)
    draw.text((6, y),      f'{spd} kts',    fill=C_ACCENT, font=f18)
    draw.text((DW - 6, y), f'Track  {trk}°', fill=C_ACCENT, font=f18, anchor='ra')
    y += 22                                           # 214

    _divider(draw, y); y += 3                         # 217

    # ── Bottom bar: distance + bearing, centred amber ────────────────────────
    brg  = f.get('bearing', 0)
    dist = f.get('distance_miles', 0.0)
    draw.text((DW // 2, y + 11),
              f'{dist:.1f} miles away, bearing {brg}°',
              fill=C_AMBER, font=f18, anchor='mm')

    disp.ShowImage(img)

# ─── Flight enrichment ────────────────────────────────────────────────────────
def enrich(ac, dist_km):
    """Combine raw ADS-B data with external enrichment into a display dict."""
    hex_code = (ac.get('hex') or '').strip().lower()
    callsign = (ac.get('flight') or '').strip()

    # Raw ADS-B fields (field names differ between dump1090 versions)
    lat = ac.get('lat') or 0
    lon = ac.get('lon') or 0
    alt = ac.get('alt_baro') or ac.get('altitude') or 0
    spd = ac.get('gs')       or ac.get('speed')    or 0
    trk = ac.get('track')    or 0      # direction aircraft is flying (degrees true)
    vs  = ac.get('baro_rate')or ac.get('vert_rate') or 0

    # adsbdb: two separate endpoints — aircraft metadata and callsign route
    ac_data    = fetch_adsbdb_aircraft(hex_code) if hex_code else {}
    route_data = fetch_adsbdb_route(callsign)    if callsign else {}

    registration = ac_data.get('registration', '')
    type_code    = (ac_data.get('type', '') or ac_data.get('icao_type', '')).upper()
    operator     = ac_data.get('registered_owner', '') or ac_data.get('operator', '')

    # Route details from adsbdb
    airline_info = route_data.get('airline',     {}) or {}
    origin_info  = route_data.get('origin',      {}) or {}
    dest_info    = route_data.get('destination', {}) or {}

    # Airline name: adsbdb route → adsbdb operator
    airline_name = airline_info.get('name', '') or operator

    # Airport names: adsbdb → OpenFlights
    origin_iata = (origin_info.get('iata_code', '') or '').upper()
    dest_iata   = (dest_info.get('iata_code',   '') or '').upper()
    origin_name = (
        origin_info.get('name', '')
        or airports_lookup.get(origin_iata, origin_iata)
    )
    dest_name = (
        dest_info.get('name', '')
        or airports_lookup.get(dest_iata, dest_iata)
    )

    # Aircraft model: ICAO type table → airport-data.com model name → raw type code
    model = AIRCRAFT_TYPES.get(type_code, '')
    if not model and hex_code:
        model = fetch_airportdata_info(hex_code).get('model', '') or type_code
    else:
        model = model or type_code

    # Photo: airport-data.com primary, adsbdb thumbnail URL as fallback
    photo_url = (
        (fetch_photo_url(hex_code, registration) if hex_code else '')
        or ac_data.get('url_photo_thumbnail', '')
        or ac_data.get('url_photo', '')
    )

    # Bearing from receiver to aircraft
    brg = bearing_to(HERE_LAT, HERE_LON, lat, lon) if (lat and lon) else 0

    return {
        'hex':            hex_code,
        'callsign':       callsign,
        'registration':   registration,
        'airline':        airline_name,
        'aircraft_model': model,
        'origin':         origin_name,
        'destination':    dest_name,
        'altitude':       int(alt) if isinstance(alt, (int, float)) else 0,
        'speed':          int(spd) if isinstance(spd, (int, float)) else 0,
        'track':          int(trk) if isinstance(trk, (int, float)) else 0,
        'bearing':        brg,
        'vspeed':         int(vs)  if isinstance(vs,  (int, float)) else 0,
        'photo_url':      photo_url or '',
        'distance_miles': round(dist_km * 0.621371, 1),
    }

# ─── Main pipeline ────────────────────────────────────────────────────────────
def find_closest(aircraft_list):
    """Return (aircraft_dict, distance_km) for the nearest aircraft within radius."""
    best, best_km = None, float('inf')
    for ac in aircraft_list:
        lat, lon = ac.get('lat'), ac.get('lon')
        if lat is None or lon is None:
            continue
        km = geodesic((HERE_LAT, HERE_LON), (lat, lon)).km
        if km <= RADIUS_KM and km < best_km:
            best, best_km = ac, km
    return best, best_km

# ─── Startup ─────────────────────────────────────────────────────────────────
logging.info('fr24-display v2 starting')
refresh_static_data()
airports_lookup = load_airports()

# ─── Main loop ───────────────────────────────────────────────────────────────
try:
    while True:
        aircraft_list = fetch_local_aircraft()
        closest, dist_km = find_closest(aircraft_list)

        if closest is None:
            render_no_aircraft('No aircraft nearby')
        else:
            flight = enrich(closest, dist_km)
            logging.info(
                f"{flight['callsign']} {flight['airline']} {flight['aircraft_model']} "
                f"— {flight['distance_miles']} miles, {flight['altitude']:,} ft, "
                f"track {flight['track']}°, bearing {flight['bearing']}°"
            )
            render_aircraft(flight)

        time.sleep(REFRESH)

except KeyboardInterrupt:
    logging.info('Shutting down')
    disp.module_exit()
    sys.exit(0)
