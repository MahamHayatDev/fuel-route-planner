"""
One-time script: clean the raw fuel CSV and add lat/lng to every station.

Usage:
    python scripts/geocode_stations.py --limit 20
    python scripts/geocode_stations.py
"""

import argparse
import json
import time
from pathlib import Path

import pandas as pd
import requests


BASE = Path(__file__).resolve().parent.parent

RAW = BASE / "data" / "fuel-prices-for-be-assessment.csv"

CACHE = BASE / "data" / "city_coords.json"

OUT = BASE / "data" / "stations_clean.csv"


US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO",
    "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}


NOMINATIM = "https://nominatim.openstreetmap.org/search"


HEADERS = {
    "User-Agent": "fuel-route-assessment/1.0 (mahamhayat192@gmail.com)"
}


def clean(raw_path):
    df = pd.read_csv(raw_path)

    df.columns = [
        "truckstop_id",
        "name",
        "address",
        "city",
        "state",
        "rack_id",
        "price",
    ]

    for col in ("name", "address", "city", "state"):
        df[col] = df[col].astype(str).str.strip()

    # Keep only US stations and remove Canadian rows
    df = df[df["state"].isin(US_STATES)]

    # Sort by price so the cheapest price for each station comes first
    df = df.sort_values("price")

    # Keep only the cheapest price for each station
    df = df.drop_duplicates("truckstop_id", keep="first")

    # Remove duplicate addresses
    df = df.drop_duplicates(
        ["address", "city", "state"],
        keep="first"
    )

    return df.reset_index(drop=True)


def geocode_city(city, state):
    params = {
        "q": f"{city}, {state}, USA",
        "format": "json",
        "limit": 1,
        "countrycodes": "us",
    }

    response = requests.get(
        NOMINATIM,
        params=params,
        headers=HEADERS,
        timeout=20,
    )

    response.raise_for_status()

    data = response.json()

    if not data:
        return None

    return [
        float(data[0]["lat"]),
        float(data[0]["lon"]),
    ]


def main(limit=None):
    # Clean the raw CSV
    df = clean(RAW)

    print(f"{len(df)} unique US stations after cleaning")

    # Load previous geocoding progress if it exists
    if CACHE.exists():
        cache = json.loads(CACHE.read_text())
    else:
        cache = {}

    # Get unique city/state combinations
    cities = (
        df[["city", "state"]]
        .drop_duplicates()
        .itertuples(index=False)
    )

    cities = [(city, state) for city, state in cities]

    # Limit cities if --limit was provided
    if limit:
        cities = cities[:limit]

    # Find cities that haven't been geocoded yet
    todo = [
        (city, state)
        for city, state in cities
        if f"{city}|{state}" not in cache
    ]

    print(
        f"{len(cities)} cities, "
        f"{len(todo)} still to geocode"
    )

    # Geocode each city
    for i, (city, state) in enumerate(todo, 1):

        try:
            cache[f"{city}|{state}"] = geocode_city(
                city,
                state
            )

        except requests.RequestException as e:
            print(
                "  network error, will retry next run:",
                city,
                state,
                e,
            )
            continue

        # Save progress every 25 cities
        if i % 25 == 0:
            CACHE.write_text(
                json.dumps(cache)
            )

            print(
                f"  {i}/{len(todo)} done"
            )

        # Respect Nominatim's request limit
        time.sleep(1.1)

    # Save final cache
    CACHE.write_text(
        json.dumps(cache)
    )

    # Attach coordinates to each station
    coords = df.apply(
        lambda row: cache.get(
            f"{row.city}|{row.state}"
        ),
        axis=1,
    )

    df["lat"] = coords.map(
        lambda coordinate: (
            coordinate[0] if coordinate else None
        )
    )

    df["lng"] = coords.map(
        lambda coordinate: (
            coordinate[1] if coordinate else None
        )
    )

    # Count stations that couldn't be geocoded
    missing = df["lat"].isna().sum()

    # Remove stations without coordinates
    df = df.dropna(
        subset=["lat", "lng"]
    )

    # Save cleaned station data
    df.to_csv(
        OUT,
        index=False
    )

    print(
        f"Saved {len(df)} stations to {OUT} "
        f"({missing} skipped, no coordinates)"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--limit",
        type=int,
        default=None
    )

    args = parser.parse_args()

    main(args.limit)