import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx


JMA_AREA_MASTER_URL = "https://www.jma.go.jp/bosai/common/const/area.json"
JMA_FORECAST_URL = "https://www.jma.go.jp/bosai/forecast/data/forecast/{office_code}.json"
JMA_TIMEOUT_SECONDS = 10.0
MAX_DISCORD_MESSAGE_LENGTH = 1800
JST = timezone(timedelta(hours=9))

_area_master_cache: Optional[Dict[str, Any]] = None


class JmaWeatherError(RuntimeError):
    pass


@dataclass
class JmaForecastBundle:
    report_datetime: str
    office_name: str
    area_lines: List[str]
    temperature_lines: List[str]


def parse_config(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def fetch_json(url: str) -> Any:
    try:
        async with httpx.AsyncClient(timeout=JMA_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException as exc:
        raise JmaWeatherError("JMA request timed out") from exc
    except httpx.HTTPStatusError as exc:
        status = getattr(exc.response, "status_code", "unknown")
        raise JmaWeatherError("JMA request failed with status {0}".format(status)) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise JmaWeatherError("JMA response could not be loaded") from exc


async def get_area_master(force_refresh: bool = False) -> Dict[str, Any]:
    global _area_master_cache
    if _area_master_cache is not None and not force_refresh:
        return _area_master_cache
    data = await fetch_json(JMA_AREA_MASTER_URL)
    if not isinstance(data, dict) or "offices" not in data or "class10s" not in data:
        raise JmaWeatherError("JMA area master is invalid")
    _area_master_cache = data
    return data


async def fetch_forecast(office_code: str) -> List[Dict[str, Any]]:
    office_code = str(office_code or "").strip()
    if not office_code:
        raise JmaWeatherError("office_code is required")
    data = await fetch_json(JMA_FORECAST_URL.format(office_code=office_code))
    if not isinstance(data, list) or not data:
        raise JmaWeatherError("JMA forecast payload is empty")
    return data


def list_forecast_offices(area_master: Dict[str, Any]) -> List[Dict[str, str]]:
    offices = area_master.get("offices") or {}
    rows = []
    for code, item in offices.items():
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            rows.append({"code": str(code), "name": name})
    return sorted(rows, key=lambda row: (row["name"], row["code"]))


def list_class10_areas(area_master: Dict[str, Any], office_code: str) -> List[Dict[str, str]]:
    office = (area_master.get("offices") or {}).get(str(office_code))
    class10s = area_master.get("class10s") or {}
    if not isinstance(office, dict):
        return []
    children = office.get("children") or []
    rows = []
    for code in children:
        item = class10s.get(str(code))
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            rows.append({"code": str(code), "name": name})
    return rows


def get_config_area_codes(config: Dict[str, Any]) -> List[str]:
    area_codes = []
    for key in ("area_codes", "primary_subdivision_codes", "primary_subdivision_code"):
        area_codes.extend(normalize_area_codes(config.get(key)))
    return normalize_area_codes(area_codes)


def validate_weather_config(config: Dict[str, Any], area_master: Dict[str, Any]) -> List[str]:
    errors = []
    office_code = str(config.get("office_code") or "").strip()
    area_codes = get_config_area_codes(config)
    if not office_code:
        errors.append("天気投稿の予報区を選択してください。")
        return errors
    if office_code not in (area_master.get("offices") or {}):
        errors.append("天気投稿の予報区コードが不正です。")
        return errors
    valid_area_codes = {row["code"] for row in list_class10_areas(area_master, office_code)}
    if not area_codes:
        errors.append("天気投稿の一次細分区域を1件以上選択してください。")
    invalid = [code for code in area_codes if code not in valid_area_codes]
    if invalid:
        errors.append("選択した一次細分区域が予報区に含まれていません。")
    return errors


def normalize_area_codes(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, Iterable):
        raw_items = list(value)
    else:
        raw_items = [value]
    seen = set()
    results = []
    for item in raw_items:
        code = str(item or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        results.append(code)
    return results


def get_office_name(area_master: Dict[str, Any], office_code: str) -> str:
    office = (area_master.get("offices") or {}).get(str(office_code)) or {}
    return str(office.get("name") or office_code)


def get_class10_name(area_master: Dict[str, Any], area_code: str) -> str:
    area = (area_master.get("class10s") or {}).get(str(area_code)) or {}
    return str(area.get("name") or area_code)


def parse_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_datetime(value: Any) -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        return str(value or "-")
    local = parsed.astimezone(JST)
    return "{0}/{1} {2:02d}:{3:02d}".format(local.month, local.day, local.hour, local.minute)


def format_date_label(value: Any) -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        return ""
    local = parsed.astimezone(JST)
    return "{0}/{1}".format(local.month, local.day)


def time_window_label(start: Any, end: Any) -> str:
    start_dt = parse_datetime(start)
    end_dt = parse_datetime(end)
    if start_dt is None:
        return "-"
    if end_dt is None:
        return "{0:02d}時".format(start_dt.astimezone(JST).hour)
    start_local = start_dt.astimezone(JST)
    end_local = end_dt.astimezone(JST)
    return "{0}-{1}時".format(start_local.hour, end_local.hour)


def build_value_by_area(time_series: List[Dict[str, Any]], key: str) -> Tuple[List[Any], Dict[str, List[str]]]:
    for series in time_series:
        areas = series.get("areas") if isinstance(series, dict) else None
        if not isinstance(areas, list):
            continue
        values_by_area = {}
        for area in areas:
            if not isinstance(area, dict) or key not in area:
                continue
            area_info = area.get("area") or {}
            code = str(area_info.get("code") or "").strip()
            values = area.get(key)
            if code and isinstance(values, list):
                values_by_area[code] = [str(value or "").strip() for value in values]
        if values_by_area:
            return series.get("timeDefines") or [], values_by_area
    return [], {}


def filter_future_pairs(times: List[Any], values: List[str], now: Optional[datetime]) -> List[Tuple[Any, str]]:
    pairs = list(zip(times, values))
    if now is None:
        return [(time_value, value) for time_value, value in pairs if value]
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    results = []
    for time_value, value in pairs:
        parsed = parse_datetime(time_value)
        if parsed is None or parsed >= now:
            if value:
                results.append((time_value, value))
    return results


def format_precipitation(times: List[Any], values: List[str], now: Optional[datetime]) -> str:
    parts = []
    if now is not None and now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    for index, (time_value, value) in enumerate(zip(times, values)):
        parsed = parse_datetime(time_value)
        next_time = times[index + 1] if index + 1 < len(times) else None
        next_parsed = parse_datetime(next_time)
        if now is not None and next_parsed is not None and next_parsed <= now:
            continue
        if now is not None and next_parsed is None and parsed is not None and parsed < now:
            continue
        if not value:
            continue
        parts.append("{0} {1}%".format(time_window_label(time_value, next_time), value))
    return " / ".join(parts)


def format_temperatures(time_series: List[Dict[str, Any]]) -> List[str]:
    time_defines, temps_by_area = build_value_by_area(time_series, "temps")
    _ = time_defines
    lines = []
    for series in time_series:
        areas = series.get("areas") if isinstance(series, dict) else None
        if not isinstance(areas, list):
            continue
        for area in areas:
            if not isinstance(area, dict) or "temps" not in area:
                continue
            area_info = area.get("area") or {}
            name = str(area_info.get("name") or area_info.get("code") or "").strip()
            temps = [str(value or "").strip() for value in area.get("temps") or []]
            present = [value for value in temps if value]
            if not name or not present:
                continue
            if len(present) >= 2:
                lines.append("{0}: 最低 {1}℃ / 最高 {2}℃".format(name, present[0], present[1]))
            else:
                lines.append("{0}: {1}℃".format(name, present[0]))
        if lines:
            return lines
    return lines


def parse_forecast(area_master: Dict[str, Any], payload: List[Dict[str, Any]], config: Dict[str, Any], now: Optional[datetime] = None) -> JmaForecastBundle:
    if not payload or not isinstance(payload[0], dict):
        raise JmaWeatherError("JMA forecast payload is invalid")
    first = payload[0]
    time_series = first.get("timeSeries") or []
    if not isinstance(time_series, list):
        raise JmaWeatherError("JMA forecast timeSeries is invalid")

    office_code = str(config.get("office_code") or "").strip()
    area_codes = get_config_area_codes(config)
    weather_times, weather_by_area = build_value_by_area(time_series, "weathers")
    pop_times, pops_by_area = build_value_by_area(time_series, "pops")
    date_label = format_date_label(weather_times[0] if weather_times else first.get("reportDatetime"))

    area_lines = []
    for area_code in area_codes:
        weather_values = weather_by_area.get(area_code) or []
        pop_values = pops_by_area.get(area_code) or []
        weather = next((value for value in weather_values if value), "")
        if not weather and not pop_values:
            continue
        lines = ["【{0}】".format(get_class10_name(area_master, area_code))]
        if date_label:
            lines.append("{0}の天気: {1}".format(date_label, weather or "-"))
        else:
            lines.append("天気: {0}".format(weather or "-"))
        precipitation = format_precipitation(pop_times, pop_values, now)
        if precipitation:
            lines.append("降水確率: {0}".format(precipitation))
        area_lines.append("\n".join(lines))

    if not area_lines:
        available_codes = sorted(set(weather_by_area.keys()) | set(pops_by_area.keys()))
        raise JmaWeatherError(
            "JMA forecast has no selected area data: office_code={0} requested_codes={1} available_codes={2}".format(
                office_code or "-",
                ",".join(area_codes) or "-",
                ",".join(available_codes) or "-",
            )
        )

    return JmaForecastBundle(
        report_datetime=str(first.get("reportDatetime") or ""),
        office_name=get_office_name(area_master, office_code),
        area_lines=area_lines,
        temperature_lines=format_temperatures(time_series),
    )


def split_message(text: str, limit: int = MAX_DISCORD_MESSAGE_LENGTH) -> List[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    current = []
    current_len = 0
    for block in text.split("\n\n"):
        block_len = len(block) + (2 if current else 0)
        if current and current_len + block_len > limit:
            chunks.append("\n\n".join(current))
            current = [block]
            current_len = len(block)
        else:
            current.append(block)
            current_len += block_len
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def format_forecast_message(bundle: JmaForecastBundle) -> List[str]:
    parts = [
        "【天気】{0}".format(bundle.office_name),
        "気象庁 {0}発表".format(format_datetime(bundle.report_datetime)),
    ]
    parts.extend(bundle.area_lines)
    if bundle.temperature_lines:
        parts.append("【代表地点の気温】\n{0}".format("\n".join(bundle.temperature_lines)))
    parts.append("出典: 気象庁")
    return split_message("\n\n".join(parts))


async def build_weather_messages(
    config_value: Any,
    *,
    area_master: Optional[Dict[str, Any]] = None,
    forecast_cache: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> List[str]:
    config = parse_config(config_value)
    master = area_master or await get_area_master()
    errors = validate_weather_config(config, master)
    if errors:
        raise JmaWeatherError("; ".join(errors))

    office_code = str(config.get("office_code") or "").strip()
    payload: Any
    if forecast_cache is not None and office_code in forecast_cache:
        payload = forecast_cache[office_code]
        if isinstance(payload, Exception):
            raise payload
    else:
        try:
            payload = await fetch_forecast(office_code)
        except Exception as exc:
            if forecast_cache is not None:
                forecast_cache[office_code] = exc
            raise
        if forecast_cache is not None:
            forecast_cache[office_code] = payload

    bundle = parse_forecast(master, payload, config, now=now)
    return format_forecast_message(bundle)
