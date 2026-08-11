from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from .models import WebQuery, WebSource


_WEATHER_MARKER = re.compile(r"天气|气温|温度|降雨|下雨|下雪|风力|湿度|weather|forecast", re.I)
_QUERY_NOISE = re.compile(
    r"今天|今日|明天|后天|现在|目前|最近|实时|当地|天气预报|天气|气温|温度|"
    r"降雨|下雨|下雪|风力|湿度|怎么样|如何|多少|请|帮我|联网|搜索|查询|查一下|"
    r"看一下|看看|的呢|的|呢|[?？!！,，。]",
    re.I,
)


def is_weather_query(query: WebQuery) -> bool:
    return bool(_WEATHER_MARKER.search(f"{query.original_intent} {query.query}"))


def weather_location(query: WebQuery) -> str:
    value = _QUERY_NOISE.sub(" ", query.query)
    return re.sub(r"\s+", " ", value).strip()


class WeatherProvider:
    """Fetch structured weather data without asking the chat model to parse search pages."""

    _WEATHER_CODES = {
        0: "晴",
        1: "大致晴朗",
        2: "局部多云",
        3: "阴",
        45: "雾",
        48: "雾凇",
        51: "小毛毛雨",
        53: "毛毛雨",
        55: "强毛毛雨",
        61: "小雨",
        63: "中雨",
        65: "大雨",
        71: "小雪",
        73: "中雪",
        75: "大雪",
        80: "小阵雨",
        81: "阵雨",
        82: "强阵雨",
        95: "雷暴",
        96: "雷暴伴小冰雹",
        99: "雷暴伴冰雹",
    }

    def __init__(self, http):
        self.http = http

    def search(self, query: WebQuery) -> list[WebSource]:
        location = weather_location(query)
        if not location:
            raise LookupError("weather location is required")

        geocoding = self.http.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1, "language": "zh", "format": "json"},
        )
        geocoding.raise_for_status()
        places = geocoding.json().get("results") or []
        if not places:
            raise LookupError("weather location was not found")
        place = places[0]

        forecast = self.http.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "timezone": "auto",
                "forecast_days": 3,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            },
        )
        forecast.raise_for_status()
        payload = forecast.json()
        current = payload.get("current") or {}
        daily = payload.get("daily") or {}
        current_code = int(current.get("weather_code", -1))
        days = []
        for index, date in enumerate(daily.get("time") or []):
            code = self._at(daily, "weather_code", index, -1)
            days.append(
                {
                    "date": date,
                    "condition": self._WEATHER_CODES.get(int(code), f"天气代码 {code}"),
                    "temperature_max_c": self._at(daily, "temperature_2m_max", index),
                    "temperature_min_c": self._at(daily, "temperature_2m_min", index),
                    "precipitation_probability_percent": self._at(daily, "precipitation_probability_max", index),
                }
            )
        name = " ".join(
            str(value) for value in (place.get("country"), place.get("admin1"), place.get("name")) if value
        )
        data = {
            "location": name or location,
            "timezone": payload.get("timezone") or "",
            "observed_at": current.get("time") or datetime.now(UTC).isoformat(),
            "current": {
                "condition": self._WEATHER_CODES.get(current_code, f"天气代码 {current_code}"),
                "temperature_c": current.get("temperature_2m"),
                "apparent_temperature_c": current.get("apparent_temperature"),
                "relative_humidity_percent": current.get("relative_humidity_2m"),
                "precipitation_mm": current.get("precipitation"),
                "wind_speed_kmh": current.get("wind_speed_10m"),
            },
            "forecast": days,
        }
        return [
            WebSource(
                source_type="weather",
                platform="weather",
                url="https://open-meteo.com/",
                title=f"{data['location']}结构化天气",
                text=json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                published_at=str(data["observed_at"]),
                freshness="official_api",
                evidence_level="structured_weather_api",
                official_account=True,
                authority="public_weather_api",
                source="Open-Meteo",
            )
        ]

    @staticmethod
    def _at(values: dict, key: str, index: int, default=None):
        rows = values.get(key) or []
        return rows[index] if index < len(rows) else default
