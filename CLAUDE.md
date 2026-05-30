# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
./fr24-display.py          # requires Raspberry Pi with Waveshare LCD + ADS-B feed
```

The script self-bootstraps: it re-execs under `.venv/bin/python` if launched with the system Python, then auto-installs any missing packages from `requirements.txt` before doing anything else. There are no tests and no build step.

To pre-install dependencies manually:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Configuration

All runtime config lives in `.env` (never committed). See `.env.example` for the required keys: `LAT`, `LON`, `RADIUS_KM`, `ADSB_URL`, `REFRESH_RATE`. The script also accepts a file path for `ADSB_URL` (dump1090-mutability writes JSON to `/run/dump1090-mutability/aircraft.json`).

## Architecture

Everything runs in `fr24-display.py` — a single-file script with no modules of its own. Execution flow:

1. **Startup**: downloads/refreshes OpenFlights `airports.dat` and `airlines.dat` into `data/` (weekly TTL), then loads them into dicts.
2. **Main loop** (every `REFRESH_RATE` seconds):
   - `fetch_local_aircraft()` — reads the ADS-B JSON feed (HTTP or file).
   - `find_closest()` — picks the nearest aircraft within `RADIUS_KM`.
   - `enrich()` — combines ADS-B fields with three external sources (see below), returns a flat display dict.
   - `render_aircraft()` or `render_no_aircraft()` — draws a PIL image and pushes it to the LCD via `disp.ShowImage()`.

### Enrichment pipeline (inside `enrich()`)

| Source | Call | What it provides |
|--------|------|-----------------|
| adsbdb.com `/v0/aircraft/{hex}` | `fetch_adsbdb_aircraft()` | registration, ICAO type code, operator, photo thumbnail URL |
| adsbdb.com `/v0/callsign/{cs}` | `fetch_adsbdb_route()` | airline name, origin/dest airport dicts |
| Planespotters.net | `fetch_photo_url()` | photo thumbnail URL (hex lookup, then reg fallback) |
| OpenFlights dicts | in-memory lookup | full airport and airline names from IATA/ICAO codes |
| `AIRCRAFT_TYPES` dict | static lookup | ICAO type code → human model name (e.g. `B738` → `Boeing 737-800`) |

All external calls go through `_cache` (dict keyed by string, with expiry timestamps). Cache TTLs: aircraft metadata 24 h, routes 6 h, photos 24 h; misses cache for only 1 h so failures self-heal.

### Display

`lib/LCD_2inch4.py` and `lib/lcdconfig.py` are Waveshare's ST7789 SPI driver (unchanged). The display is 320 × 240 px in landscape mode — note that `disp.height = 320` (width) and `disp.width = 240` (height) due to the driver's coordinate convention.

Layout constants are at the top of `render_aircraft()`. The colour palette (`C_BG`, `C_WHITE`, etc.) is defined just below the display init block.

### Hardware pin mapping

```
RST=27, DC=25, BL=18, SPI bus=0, device=0
```

These are hardcoded constants near line 346 of `fr24-display.py`.
