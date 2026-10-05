"""Spheron region -> (lat, lon), for the controller's proximity sort.

The skypilot-controller ranks placement candidates by great-circle distance
from the serving region (dev-usw2 reports ``lat 45.84, lon -119.70``). It can
only do that if every region string resolves to a coordinate.

Two shapes must both resolve, because Spheron emits both:

    kansascity-usa-1              plain
    166e655a:culpeper-usa-1       opaque routing prefix

and a third family that is not a city at all:

    us-central-3, US Central 1, EU North 1, CANADA-1, Finland 2, MON1, OSL1

For the opaque ones we record the provider's own published datacentre location
where it is known, and otherwise return None. **Returning None is deliberate
and must stay loud**: a region we cannot place is a region we cannot rank, and
silently defaulting it to (0, 0) would sort it into the Gulf of Guinea and make
it look like the closest option on earth.

Coordinates are city-centre approximations. They exist to rank candidates that
are hundreds of kilometres apart, not to be accurate to the datacentre.
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

# The serving region the controller measures from (dev-usw2 / us-west-2).
SERVING_REGION_LATLON: Tuple[float, float] = (45.84, -119.70)

_CITY_LATLON: Dict[str, Tuple[float, float]] = {
    # --- United States -----------------------------------------------------
    "ashburn": (39.04, -77.49),
    "beltsville": (39.03, -76.91),
    "chicago": (41.88, -87.63),
    "culpeper": (38.47, -77.996),
    "dallas": (32.78, -96.80),
    "desmoines": (41.59, -93.62),
    "dulles": (38.95, -77.45),
    "houston": (29.76, -95.37),
    "kansascity": (39.10, -94.58),
    "newyork": (40.71, -74.01),
    "phoenix": (33.45, -112.07),
    "raleigh": (35.78, -78.64),
    "saltlakecity": (40.76, -111.89),
    # San Jose is the CLOSEST Spheron US region to us-west-2 (~960 km). It was
    # missing from the first draft of this table, which silently dropped it out
    # of the proximity ranking -- caught by test_every_fixture_region_is_mappable.
    "sanjose": (37.34, -121.89),
    "wichita": (37.69, -97.34),
    # --- Canada ------------------------------------------------------------
    "calgary": (51.05, -114.07),
    "montreal": (45.50, -73.57),
    "toronto": (43.65, -79.38),
    # --- Europe ------------------------------------------------------------
    "amsterdam": (52.37, 4.90),
    "oslo": (59.91, 10.75),
    "paris": (48.86, 2.35),
    "warsaw": (52.23, 21.01),
    # --- Asia-Pacific ------------------------------------------------------
    "mumbai": (19.08, 72.88),
    "sydney": (-33.87, 151.21),
    "tokyo": (35.68, 139.65),
}

# Regions that are not "<city>-<country>-<n>" and must be mapped by hand.
_NAMED_REGION_LATLON: Dict[str, Tuple[float, float]] = {
    # Spheron / provider shorthand. Where a provider publishes only a broad
    # area, we use the metro they name publicly.
    "canada-1": (45.50, -73.57),  # Montreal
    "mon1": (45.50, -73.57),  # Montreal
    "osl1": (59.91, 10.75),  # Oslo
    "us-1": (39.10, -94.58),  # Kansas City (massed-compute default)
    "us central 1": (41.59, -93.62),  # Des Moines
    "eu north 1": (60.17, 24.94),  # Helsinki
    "eu west 1": (53.35, -6.26),  # Dublin
    "uk south 2": (51.51, -0.13),  # London
    "finland 1": (60.17, 24.94),
    "finland 2": (60.17, 24.94),
    "finland 3": (60.17, 24.94),
    # massed-compute's us-central-N / us-east-N pools.
    "us-central-1": (41.59, -93.62),
    "us-central-2": (41.59, -93.62),
    "us-central-3": (41.59, -93.62),
    "us-central-9": (41.59, -93.62),
    "us-central-12": (41.59, -93.62),
    "us-east-1": (39.04, -77.49),
    "us-east-4": (39.04, -77.49),
    "us-east-6": (39.04, -77.49),
    "us-southeast-1": (33.75, -84.39),  # Atlanta
    # Provider IATA-style shorthand.
    "ams": (52.37, 4.90),
    "syd2": (-33.87, 151.21),
    "tyo4": (35.68, 139.65),
}


def strip_region_prefix(region: str) -> str:
    """``166e655a:culpeper-usa-1`` -> ``culpeper-usa-1``."""
    return region.split(":", 1)[1] if ":" in region else region


def region_latlon(region: str) -> Optional[Tuple[float, float]]:
    """Best-effort coordinate for a Spheron region string.

    Returns None when unknown. Callers MUST treat None as "cannot rank this
    candidate" and surface it -- never substitute a default coordinate.
    """
    if not region:
        return None
    bare = strip_region_prefix(region).strip()
    lowered = bare.lower()

    named = _NAMED_REGION_LATLON.get(lowered)
    if named is not None:
        return named

    # "<city>-<country>-<n>" -> city
    city = lowered.split("-", 1)[0]
    return _CITY_LATLON.get(city)


def great_circle_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    radius_km = 6371.0
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlat = lat2 - lat1
    dlon = math.radians(b[1] - a[1])
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(h))


def distance_from_serving_km(region: str) -> Optional[float]:
    """Great-circle km from the serving region, or None if unmappable."""
    coord = region_latlon(region)
    if coord is None:
        return None
    return great_circle_km(SERVING_REGION_LATLON, coord)
