# Fuel Route Planner (Django)

API that takes a start and finish location in the USA and returns the route,
the cheapest fuel stops (500-mile range, 10 MPG) and the total fuel cost.

## Setup
```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py load_stations
python manage.py runserver
```

## Endpoints
- `GET /api/route/?start=New York, NY&finish=Los Angeles, CA` returns JSON
- `POST /api/route/` with `{"start": "...", "finish": "..."}`
- `GET /api/map/?start=...&finish=...` shows an interactive map

## How it works
Stations are cleaned and geocoded once (`scripts/geocode_stations.py`).
Each request makes one call to OSRM for the route, then finds stations within
15 miles of it. A greedy algorithm picks the cheapest stops, and results are cached.

## Assumptions
- Tank starts empty. The origin fill-up is priced at the first station on the route.
- Station coordinates are city-level (the CSV has no lat/lng).