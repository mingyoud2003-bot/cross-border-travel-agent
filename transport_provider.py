from datetime import datetime
from zoneinfo import ZoneInfo

import requests


TRANSITOUS_URL = "https://api.transitous.org/api/v6/plan"


STATIONS = {
    "伦敦": {
        "name": "London St Pancras International",
        "lat": 51.5310,
        "lon": -0.1260,
        "tz": "Europe/London",
    },
    "巴黎": {
        "name": "Paris Gare du Nord",
        "lat": 48.8809,
        "lon": 2.3553,
        "tz": "Europe/Paris",
    },
    "布鲁塞尔": {
        "name": "Bruxelles-Midi / Brussel-Zuid",
        "lat": 50.8357,
        "lon": 4.3365,
        "tz": "Europe/Brussels",
    },
}


class TransportProviderError(Exception):
    """Raised when the transport provider cannot return valid results."""


def _to_local_time(
    utc_time: str,
    timezone_name: str,
) -> str:
    """
    Convert Transitous UTC timestamp to local station time.
    """

    dt = datetime.fromisoformat(
        utc_time.replace("Z", "+00:00")
    )

    local_dt = dt.astimezone(
        ZoneInfo(timezone_name)
    )

    return local_dt.isoformat(
        timespec="minutes"
    )


def search_train_journeys(
    origin: str,
    destination: str,
    travel_date: str,
    limit: int = 3,
) -> dict:
    """
    Search train journeys using Transitous
    and return normalized business data.
    """

    if origin not in STATIONS:
        raise TransportProviderError(
            f"暂不支持出发城市：{origin}"
        )

    if destination not in STATIONS:
        raise TransportProviderError(
            f"暂不支持目的城市：{destination}"
        )

    origin_station = STATIONS[origin]
    destination_station = STATIONS[destination]

    # 使用出发地当地时间的早上 06:00 开始查询
    departure_time = datetime.fromisoformat(
        f"{travel_date}T06:00:00"
    ).replace(
        tzinfo=ZoneInfo(origin_station["tz"])
    )

    params = {
        "fromPlace": (
            f"{origin_station['lat']},"
            f"{origin_station['lon']}"
        ),
        "toPlace": (
            f"{destination_station['lat']},"
            f"{destination_station['lon']}"
        ),
        "time": departure_time.isoformat(),
        "radius": 250,
        "detailedLegs": "false",
        "numItineraries": limit,
        "maxItineraries": limit,
    }

    headers = {
        "User-Agent": (
            "TravelAgentV2/0.1 "
            "(student portfolio project)"
        )
    }

    try:
        response = requests.get(
            TRANSITOUS_URL,
            params=params,
            headers=headers,
            timeout=20,
        )

        response.raise_for_status()

    except requests.Timeout as exc:
        raise TransportProviderError(
            "铁路查询服务响应超时。"
        ) from exc

    except requests.RequestException as exc:
        raise TransportProviderError(
            "铁路查询服务暂时不可用。"
        ) from exc

    try:
        data = response.json()
    except (requests.JSONDecodeError, ValueError) as exc:
        raise TransportProviderError(
            "铁路查询服务返回了无效数据。"
        ) from exc

    if not isinstance(data, dict):
        raise TransportProviderError(
            "铁路查询服务返回了无效数据。"
        )

    raw_itineraries = data.get(
        "itineraries",
        []
    )

    if not raw_itineraries:
        return {
            "status": "no_results",
            "origin": origin,
            "destination": destination,
            "date": travel_date,
            "journeys": [],
        }

    journeys = []

    for itinerary in raw_itineraries[:limit]:
        legs = itinerary.get("legs", [])

        if not legs:
            continue

        first_leg = legs[0]
        last_leg = legs[-1]

        from_info = first_leg.get(
            "from",
            {}
        )

        to_info = last_leg.get(
            "to",
            {}
        )

        departure_utc = from_info.get(
            "departure"
        )

        arrival_utc = to_info.get(
            "arrival"
        )

        from_tz = from_info.get(
            "tz",
            origin_station["tz"],
        )

        to_tz = to_info.get(
            "tz",
            destination_station["tz"],
        )

        operators = []

        services = []

        realtime = False

        for leg in legs:
            agency = leg.get("agencyName")

            if (
                agency
                and agency not in operators
            ):
                operators.append(agency)

            service = (
                leg.get("displayName")
                or leg.get("tripShortName")
            )

            if (
                service
                and service not in services
            ):
                services.append(service)

            if leg.get("realTime") is True:
                realtime = True

        journey = {
            "departure_station": from_info.get(
                "name"
            ),
            "arrival_station": to_info.get(
                "name"
            ),
            "departure_local": (
                _to_local_time(
                    departure_utc,
                    from_tz,
                )
                if departure_utc
                else None
            ),
            "arrival_local": (
                _to_local_time(
                    arrival_utc,
                    to_tz,
                )
                if arrival_utc
                else None
            ),
            "duration_minutes": (
                itinerary.get(
                    "duration",
                    0,
                )
                // 60
            ),
            "transfers": itinerary.get(
                "transfers"
            ),
            "operators": operators,
            "services": services,
            "realtime": realtime,
        }

        journeys.append(journey)

    return {
        "status": "success",
        "source": "Transitous",
        "origin": origin,
        "destination": destination,
        "date": travel_date,
        "journeys": journeys,
    }
