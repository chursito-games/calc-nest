#!/usr/bin/env python3
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

BLS_API_BASE = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BLS_CPI_RELEASE_URL = "https://www.bls.gov/news.release/cpi.htm"
BLS_CPI_SCHEDULE_URL = "https://www.bls.gov/schedule/news_release/cpi.htm"

SERIES_SA = "CUSR0000SA0"
SERIES_NSA = "CUUR0000SA0"

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT_PATH = DATA_DIR / "inflation_snapshot.json"

def fetch_series(series_id: str) -> list[dict]:
    response = requests.get(f"{BLS_API_BASE}{series_id}", timeout=30)
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS API request failed for {series_id}: {payload}")
    data = payload["Results"]["series"][0]["data"]
    return [row for row in data if row.get("period", "").startswith("M") and row["period"] != "M13"]

def to_month_index(year: int, period: str) -> int:
    return year * 12 + int(period[1:])

def find_previous_month(data: list[dict], current: dict) -> dict:
    current_idx = to_month_index(int(current["year"]), current["period"])
    for row in data:
        if to_month_index(int(row["year"]), row["period"]) == current_idx - 1:
            return row
    raise RuntimeError("Previous month not found in BLS data.")

def find_same_month_previous_year(data: list[dict], current: dict) -> dict:
    target_year = int(current["year"]) - 1
    target_period = current["period"]
    for row in data:
        if int(row["year"]) == target_year and row["period"] == target_period:
            return row
    raise RuntimeError("Same month previous year not found in BLS data.")

def percent_change(new: float, old: float) -> float:
    return ((new / old) - 1.0) * 100.0

def parse_next_release() -> tuple[str, str, str]:
    html = requests.get(BLS_CPI_SCHEDULE_URL, timeout=30).text
    text = re.sub(r"<[^>]+>", "\n", html)

    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    row_pattern = re.compile(
        r"^((January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\s+([A-Z][a-z]{2,8}\.?\s+\d{1,2},\s+\d{4})\s+(\d{2}:\d{2}\s+[AP]M)$"
    )

    month_lookup = {
        "Jan.": "January",
        "Feb.": "February",
        "Mar.": "March",
        "Apr.": "April",
        "May": "May",
        "Jun.": "June",
        "Jul.": "July",
        "Aug.": "August",
        "Sep.": "September",
        "Oct.": "October",
        "Nov.": "November",
        "Dec.": "December",
        "Jan": "January",
        "Feb": "February",
        "Mar": "March",
        "Apr": "April",
        "Jun": "June",
        "Jul": "July",
        "Aug": "August",
        "Sep": "September",
        "Oct": "October",
        "Nov": "November",
        "Dec": "December",
    }

    today = datetime.now(timezone.utc).date()

    for line in lines:
        match = row_pattern.match(line)
        if not match:
            continue

        ref_month = match.group(1)
        release_date_raw = match.group(3)
        release_time = match.group(4)

        normalized_date = release_date_raw
        for short_month, full_month in month_lookup.items():
            if normalized_date.startswith(short_month):
                normalized_date = normalized_date.replace(short_month, full_month, 1)
                break

        parsed_date = datetime.strptime(normalized_date, "%B %d, %Y").date()

        if parsed_date >= today:
            return ref_month, normalized_date, release_time

    raise RuntimeError("Could not find next CPI release in schedule page.")

def deterministic_editorial(reference_month: str, monthly_pct: float, annual_pct: float, prev_annual_pct: float) -> str:
    if annual_pct > prev_annual_pct:
        direction = "above"
    elif annual_pct < prev_annual_pct:
        direction = "below"
    else:
        direction = "in line with"

    return (
        f"The latest CPI snapshot for {reference_month} shows a {monthly_pct:.1f}% month-over-month change "
        f"and a {annual_pct:.1f}% 12-month change. The annual reading is {direction} the prior release, "
        f"so this can serve as a practical baseline scenario in the calculator while you also test lower and higher assumptions."
    )

def ai_editorial(reference_month: str, monthly_pct: float, annual_pct: float, prev_annual_pct: float, next_release_date: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return deterministic_editorial(reference_month, monthly_pct, annual_pct, prev_annual_pct)

    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    prompt = f"""
Write one short neutral editorial paragraph for a personal-finance inflation calculator.

Use only these facts:
- latest CPI release month: {reference_month}
- latest seasonally adjusted monthly CPI change: {monthly_pct:.1f}%
- latest 12-month CPI change: {annual_pct:.1f}%
- previous 12-month CPI change: {prev_annual_pct:.1f}%
- next CPI release date: {next_release_date}

Rules:
- 2 to 3 sentences only
- neutral and practical
- no prediction
- no investment advice
- no hype
- explain why the number is useful as a planning baseline
- plain English
"""

    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"model": model, "input": prompt},
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()

    text = (data.get("output_text") or "").strip()
    if text:
        return text

    chunks = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                chunks.append(content["text"])
    text = " ".join(chunks).strip()
    return text or deterministic_editorial(reference_month, monthly_pct, annual_pct, prev_annual_pct)

def main():
    sa = fetch_series(SERIES_SA)
    nsa = fetch_series(SERIES_NSA)

    current_sa = sa[0]
    prev_sa = find_previous_month(sa, current_sa)

    current_nsa = nsa[0]
    prev_year_nsa = find_same_month_previous_year(nsa, current_nsa)
    prev_month_nsa = find_previous_month(nsa, current_nsa)
    prev_month_prev_year_nsa = find_same_month_previous_year(nsa, prev_month_nsa)

    monthly_pct = percent_change(float(current_sa["value"]), float(prev_sa["value"]))
    annual_pct = percent_change(float(current_nsa["value"]), float(prev_year_nsa["value"]))
    prev_annual_pct = percent_change(float(prev_month_nsa["value"]), float(prev_month_prev_year_nsa["value"]))

    next_ref_month, next_release_date, next_release_time = parse_next_release()

    reference_month = f'{current_nsa["periodName"]} {current_nsa["year"]}'
    editorial = ai_editorial(reference_month, monthly_pct, annual_pct, prev_annual_pct, next_release_date)

    snapshot = {
        "reference_month": reference_month,
        "monthly_cpi_change": f"{monthly_pct:.1f}%",
        "annual_cpi_change": f"{annual_pct:.1f}%",
        "previous_annual_cpi_change": f"{prev_annual_pct:.1f}%",
        "next_release_reference_month": next_ref_month,
        "next_release_date": next_release_date,
        "next_release_time": next_release_time,
        "editorial_summary": editorial,
        "source_name": "U.S. Bureau of Labor Statistics",
        "source_release_url": BLS_CPI_RELEASE_URL,
        "source_schedule_url": BLS_CPI_SCHEDULE_URL,
        "series_ids": {
            "monthly_seasonally_adjusted_all_items": SERIES_SA,
            "annual_not_seasonally_adjusted_all_items": SERIES_NSA
        },
        "updated_at_utc": datetime.now(timezone.utc).isoformat()
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")

if __name__ == "__main__":
    main()
