import hashlib
import math
import re

import numpy as np
import requests
from django.core.cache import cache

from .models import Station


# ---- Settings for the assessment ------------------------------------------
MAX_RANGE_MILES = 500      # full tank range
MPG = 10                   # miles per gallon
CORRIDOR_MILES = 15        # how far off the route a station may be
EARTH_RADIUS_MI = 3958.8
OSRM_URL = "https://router.project-osrm.org/route/v1/driving/{coords}"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {
    "User-Agent": "fuel-route-assessment/1.0 (mahamhayat192@gmail.com)"
}


class PlannerError(Exception):
    """Base error. `status` is the HTTP status code the API should return."""
    status = 400


class LocationError(PlannerError):
    status = 400


class RoutingServiceError(PlannerError):
    status = 502


class NoFeasiblePlanError(PlannerError):
    status = 422


# ---- 1. Turn user input into coordinates ------------------------------------
def resolve_location(text):
    """Accepts 'lat,lng', 'City, ST' (looked up locally) or any free text."""
    text = (text or "").strip()
    if not text:
        raise LocationError("Location is empty.")

    m = re.match(
        r"^(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)$",
        text
    )

    if m:
        lat, lng = float(m.group(1)), float(m.group(2))
    else:
        lat = lng = None
        m = re.match(r"^(.+?),\s*([A-Za-z]{2})$", text)

        if m:
            # "Dallas, TX" -> our own database, no outside API call
            row = (
                Station.objects
                .filter(
                    city__iexact=m.group(1).strip(),
                    state=m.group(2).upper()
                )
                .values("lat", "lng")
                .first()
            )

            if row:
                lat, lng = row["lat"], row["lng"]

        if lat is None:
            lat, lng = _nominatim(text)

    if not (24 <= lat <= 50 and -125 <= lng <= -66):
        raise LocationError(
            f"'{text}' is not inside the continental USA."
        )

    return lat, lng


def _nominatim(text):
    key = "geo:" + hashlib.md5(text.lower().encode()).hexdigest()
    hit = cache.get(key)

    if hit:
        return hit

    try:
        r = requests.get(
            NOMINATIM_URL,
            headers=HEADERS,
            timeout=15,
            params={
                "q": text,
                "format": "json",
                "limit": 1,
                "countrycodes": "us",
            },
        )
        r.raise_for_status()
        data = r.json()

    except requests.RequestException as exc:
        raise LocationError(
            f"Could not look up '{text}': {exc}"
        )

    if not data:
        raise LocationError(
            f"Could not find '{text}'."
        )

    result = (
        float(data[0]["lat"]),
        float(data[0]["lon"])
    )

    cache.set(key, result, 86400)

    return result


# ---- 2. ONE call to the routing API -----------------------------------------
def fetch_route(start, end):
    key = (
        f"route:{start[0]:.3f},{start[1]:.3f}:"
        f"{end[0]:.3f},{end[1]:.3f}"
    )

    hit = cache.get(key)

    if hit:
        return hit

    coords = (
        f"{start[1]},{start[0]};"
        f"{end[1]},{end[0]}"
    )  # OSRM wants lng,lat

    try:
        r = requests.get(
            OSRM_URL.format(coords=coords),
            timeout=30,
            params={
                "overview": "full",
                "geometries": "geojson"
            },
        )

        r.raise_for_status()
        data = r.json()

    except requests.RequestException as exc:
        raise RoutingServiceError(
            f"Routing service failed: {exc}"
        )

    if data.get("code") != "Ok" or not data.get("routes"):
        raise RoutingServiceError(
            "No driving route found between those points."
        )

    route = data["routes"][0]

    result = {
        "coords": route["geometry"]["coordinates"],
        "miles": route["distance"] / 1609.344
    }

    cache.set(key, result, 3600)

    return result


# ---- 3. Route maths ---------------------------------------------------------
def _haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(
        np.radians,
        (lat1, lon1, lat2, lon2)
    )

    a = (
        np.sin((lat2 - lat1) / 2) ** 2
        + np.cos(lat1)
        * np.cos(lat2)
        * np.sin((lon2 - lon1) / 2) ** 2
    )

    return (
        2
        * EARTH_RADIUS_MI
        * np.arcsin(np.sqrt(a))
    )


def prepare_route(coords, total_miles, step=2.0):
    """Shrink the route to roughly one point per 2 miles and give every point
    its distance (in miles) from the start."""
    pts = np.asarray(coords, dtype=float)

    lon, lat = pts[:, 0], pts[:, 1]

    seg = _haversine(
        lat[:-1],
        lon[:-1],
        lat[1:],
        lon[1:]
    )

    cum = np.concatenate([
        [0.0],
        np.cumsum(seg)
    ])

    if cum[-1] > 0:
        cum *= total_miles / cum[-1]

    idx = np.searchsorted(
        cum,
        np.arange(0, cum[-1], step)
    )

    idx = np.unique(
        np.clip(
            np.append(idx, len(cum) - 1),
            0,
            len(cum) - 1
        )
    )

    return lat[idx], lon[idx], cum[idx]


# ---- 4. Stations (kept in memory, loaded once) -----------------------------
_STATION_CACHE = {}


def get_stations():
    if "data" not in _STATION_CACHE:
        rows = list(
            Station.objects.values(
                "id",
                "name",
                "address",
                "city",
                "state",
                "price",
                "lat",
                "lng"
            )
        )

        _STATION_CACHE["data"] = {
            "rows": rows,
            "lat": np.array(
                [r["lat"] for r in rows]
            ),
            "lng": np.array(
                [r["lng"] for r in rows]
            ),
            "price": np.array(
                [r["price"] for r in rows]
            ),
        }

    return _STATION_CACHE["data"]


def stations_along_route(rlat, rlon, rpos):
    """Return stations within CORRIDOR_MILES of the route, with their
    position (miles from start) along the route."""
    st = get_stations()

    pad = 0.5

    mask = (
        (st["lat"] > rlat.min() - pad)
        & (st["lat"] < rlat.max() + pad)
        & (st["lng"] > rlon.min() - pad)
        & (st["lng"] < rlon.max() + pad)
    )

    cand = np.where(mask)[0]

    if cand.size == 0:
        return []

    mid_lat = math.radians(
        float(rlat.mean())
    )

    ry = rlat * 69.0
    rx = (
        rlon
        * 69.17
        * math.cos(mid_lat)
    )

    found = []

    for chunk in np.array_split(
        cand,
        max(1, cand.size // 800)
    ):
        sy = st["lat"][chunk] * 69.0

        sx = (
            st["lng"][chunk]
            * 69.17
            * math.cos(mid_lat)
        )

        d2 = (
            (sx[:, None] - rx[None, :]) ** 2
            + (sy[:, None] - ry[None, :]) ** 2
        )

        nearest = d2.argmin(axis=1)

        dist = np.sqrt(
            d2[
                np.arange(len(chunk)),
                nearest
            ]
        )

        for k in np.where(
            dist <= CORRIDOR_MILES
        )[0]:
            row = dict(
                st["rows"][chunk[k]]
            )

            row["pos"] = float(
                rpos[nearest[k]]
            )

            row["detour"] = float(
                dist[k]
            )

            found.append(row)

    found.sort(
        key=lambda s: (
            s["pos"],
            s["price"]
        )
    )

    return found


# ---- 5. The fuel-stop algorithm ----------------------------------------------
def plan_fuel_stops(
    stations,
    total_miles,
    start_price,
    max_range=MAX_RANGE_MILES,
    mpg=MPG
):
    """
    Greedy 'cheapest fuel' strategy. The tank starts empty at the origin.
    At each stop:
      * if a cheaper station is reachable on a full tank -> buy only enough
        fuel to get there;
      * otherwise -> fill the tank and drive to the cheapest reachable station.
    Returns a list of purchases: {"node": station-or-None, "miles": ..., "price": ...}
    (node None = the fill-up at the origin).
    """

    nodes = (
        [
            {
                "pos": 0.0,
                "price": start_price,
                "origin": True
            }
        ]
        + [
            s for s in stations
            if 0 < s["pos"] < total_miles
        ]
        + [
            {
                "pos": total_miles,
                "price": 0.0,
                "dest": True
            }
        ]
    )

    n = len(nodes)
    purchases = []

    fuel = 0.0

    i = 0

    while i < n - 1:
        cur = nodes[i]

        reach = []

        for j in range(i + 1, n):
            if (
                nodes[j]["pos"] - cur["pos"]
                > max_range
            ):
                break

            reach.append(j)

        if not reach:
            raise NoFeasiblePlanError(
                f"No fuel station within {max_range} miles after mile "
                f"{cur['pos']:.0f}."
            )

        cheaper = next(
            (
                j for j in reach
                if nodes[j]["price"] < cur["price"]
            ),
            None
        )

        if cheaper is not None:
            target = cheaper

            buy = max(
                0.0,
                nodes[target]["pos"]
                - cur["pos"]
                - fuel
            )

        else:
            target = min(
                reach,
                key=lambda j: nodes[j]["price"]
            )

            buy = max_range - fuel

        if buy > 1e-9:
            purchases.append(
                {
                    "node": (
                        None
                        if cur.get("origin")
                        else cur
                    ),
                    "miles": buy,
                    "price": cur["price"]
                }
            )

            fuel += buy

        fuel -= (
            nodes[target]["pos"]
            - cur["pos"]
        )

        i = target

    return purchases


# ---- 6. Put everything together ----------------------------------------------
def build_plan(start_text, finish_text):
    raw = (
        f"{start_text.strip().lower()}|"
        f"{finish_text.strip().lower()}"
    )

    key = (
        "plan:"
        + hashlib.md5(raw.encode()).hexdigest()
    )

    hit = cache.get(key)

    if hit:
        return hit

    start = resolve_location(start_text)
    end = resolve_location(finish_text)

    route = fetch_route(start, end)

    total_miles = route["miles"]

    rlat, rlon, rpos = prepare_route(
        route["coords"],
        total_miles
    )

    stations = stations_along_route(
        rlat,
        rlon,
        rpos
    )

    start_price = (
        stations[0]["price"]
        if stations
        else float(
            get_stations()["price"].mean()
        )
    )

    purchases = plan_fuel_stops(
        stations,
        total_miles,
        start_price
    )

    stops = []
    origin_fill = None
    total_cost = 0.0

    for p in purchases:
        gallons = p["miles"] / MPG
        cost = gallons * p["price"]

        total_cost += cost

        if p["node"] is None:
            origin_fill = {
                "gallons": round(gallons, 2),
                "price_per_gallon": round(
                    p["price"],
                    3
                ),
                "cost_usd": round(
                    cost,
                    2
                )
            }

            continue

        s = p["node"]

        stops.append(
            {
                "order": len(stops) + 1,
                "station": s["name"],
                "address": s["address"],
                "city": s["city"],
                "state": s["state"],
                "lat": s["lat"],
                "lng": s["lng"],
                "mile_marker": round(
                    s["pos"],
                    1
                ),
                "detour_miles": round(
                    s["detour"],
                    1
                ),
                "price_per_gallon": round(
                    s["price"],
                    3
                ),
                "gallons_purchased": round(
                    gallons,
                    2
                ),
                "cost_usd": round(
                    cost,
                    2
                ),
            }
        )

    plan = {
        "start": {
            "input": start_text,
            "lat": start[0],
            "lng": start[1]
        },
        "finish": {
            "input": finish_text,
            "lat": end[0],
            "lng": end[1]
        },
        "total_distance_miles": round(
            total_miles,
            1
        ),
        "total_gallons": round(
            total_miles / MPG,
            2
        ),
        "total_fuel_cost_usd": round(
            total_cost,
            2
        ),
        "origin_fill": origin_fill,
        "fuel_stops": stops,
        "assumptions": {
            "max_range_miles": MAX_RANGE_MILES,
            "miles_per_gallon": MPG,
            "stations_searched_within_miles_of_route": CORRIDOR_MILES,
            "note": (
                "Tank starts empty; the first fill-up at the origin is "
                "priced at the first station found on the route."
            ),
        },
        "route": {
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [
                        round(float(x), 5),
                        round(float(y), 5)
                    ]
                    for x, y in zip(
                        rlon,
                        rlat
                    )
                ]
            },
        },
    }

    cache.set(
        key,
        plan,
        3600
    )

    return plan