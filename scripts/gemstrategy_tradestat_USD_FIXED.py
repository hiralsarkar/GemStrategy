#!/usr/bin/env python3
"""
GemStrategy TRADESTAT automated collector - V2

REAL DATA ONLY
--------------
This collector requests data directly from India's official TRADESTAT/MEIDB
site. It does not generate, interpolate, estimate, or fill missing trade data.

Current official source:
https://tradestat.commerce.gov.in/meidb/commodity_wise_all_countries_export
https://tradestat.commerce.gov.in/meidb/commodity_wise_all_countries_import

Confirmed from browser inspection for EXPORTS:
POST /meidb/commodity_wise_all_countries_export
fields:
    _token
    cwacexHSCODE
    cwacexMonth
    cwacexYear
    cwacexReportVal
    cwacexReportYear

IMPORT field names follow the site's naming convention (cwacim...). The
collector validates the response before saving it. Run the IMPORT pilot first;
do not scale imports until its output is checked against the browser.

VALUE SELECTOR
--------------
TRADESTAT's reportVal is a site selector, not a currency amount.
The value 1 matched the user's browser request when the page was set to
US$ Million. Keep VALUE_SELECTOR=1 for the validated pilot. If you want
₹ Crore, determine its selector in the browser Network Payload first and
change the config after validation.

YEAR TYPE
---------
The observed browser payload used ReportYear=1 for Financial Year.

DATE COVERAGE
-------------
The official site currently reports Jan 2018 to Jun 2026, with revised/final
status conventions. The collector does NOT invent months that are absent.

IMPORTANT
---------
Do not run thousands of requests immediately. First run:
    python gemstrategy_tradestat_v2.py --mode export --hs 71012100 --year 2026 --month 6

Then compare the resulting CSV with the TRADESTAT browser page.

After validation, use --config hs_codes.csv and the date range.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup


BASE = "https://tradestat.commerce.gov.in/meidb"
ENDPOINTS = {
    "export": f"{BASE}/commodity_wise_all_countries_export",
    "import": f"{BASE}/commodity_wise_all_countries_import",
}

# Confirmed in the browser for the pilot:
DEFAULT_VALUE_SELECTOR = {"export": "1", "import": "1"}
YEAR_TYPE_SELECTOR = "1"

MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
}
MONTH_NAMES = {v: k for k, v in MONTHS.items()}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


def csrf_token(session: requests.Session, url: str) -> str:
    r = session.get(url, timeout=45)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    el = soup.find("input", {"name": "_token"})
    if el and el.get("value"):
        return el["value"]

    meta = soup.find("meta", {"name": "csrf-token"})
    if meta and meta.get("content"):
        return meta["content"]

    raise RuntimeError(f"Could not find CSRF token at {url}")


def payload_for(mode: str, token: str, hs: str, month: int, year: int, value_selector: str) -> dict:
    if mode == "export":
        prefix = "cwacex"
    elif mode == "import":
        prefix = "cwacim"
    else:
        raise ValueError("mode must be export or import")

    return {
        "_token": token,
        f"{prefix}HSCODE": hs,
        f"{prefix}Month": str(month),
        f"{prefix}Year": str(year),
        f"{prefix}ReportVal": value_selector,
        f"{prefix}ReportYear": YEAR_TYPE_SELECTOR,
    }


def post_report(
    session: requests.Session,
    mode: str,
    hs: str,
    month: int,
    year: int,
    retries: int = 3,
    value_selector: str = "1",
) -> tuple[str, dict]:
    url = ENDPOINTS[mode]

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            token = csrf_token(session, url)
            payload = payload_for(mode, token, hs, month, year, value_selector)

            headers = {
                "Referer": url,
                "Origin": "https://tradestat.commerce.gov.in",
            }

            r = session.post(
                url,
                data=payload,
                headers=headers,
                timeout=60,
            )
            r.raise_for_status()

            if "TRADESTAT" not in r.text.upper():
                raise RuntimeError("Response does not look like TRADESTAT HTML.")

            return r.text, {
                "request_url": url,
                "method": "POST",
                "mode": mode,
                "hs_code": hs,
                "month": month,
                "year": year,
                "value_selector": value_selector,
                "year_type_selector": YEAR_TYPE_SELECTOR,
                "http_status": r.status_code,
                "retrieved_at_utc": datetime.utcnow().isoformat() + "Z",
                "response_bytes": len(r.content),
                "sha256": hashlib.sha256(r.content).hexdigest(),
            }

        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2 ** (attempt - 1))

    raise RuntimeError(
        f"Failed after {retries} attempts: {mode} {hs} {year}-{month:02d}: "
        f"{last_error}"
    )


def extract_metadata(html: str) -> dict:
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)

    out = {}

    m = re.search(r"Commodity:\s*([0-9]{8})\s+(.+?)\s+(?:Unit|UNWRKD|WRKD)\b", text, re.I)
    if m:
        out["reported_hs"] = m.group(1)
        out["reported_commodity"] = m.group(2).strip()

    m = re.search(r"Commodity:\s*([0-9]{8})\s+(.+?)\s+Unit:\s*([A-Z]+)", text, re.I)
    if m:
        out["reported_hs"] = m.group(1)
        out["reported_commodity"] = m.group(2).strip()
        out["reported_unit"] = m.group(3).strip()

    m = re.search(r"Report Dated:\s*([^|]+)", text, re.I)
    if m:
        out["report_date_text"] = m.group(1).strip()

    return out


def parse_country_table(html: str) -> pd.DataFrame:
    # StringIO is required so pandas treats the response as HTML, not a filename.
    # lxml is part of this project's validated dependency set.  Specifying it
    # avoids pandas falling through to html5lib, which is not installed here.
    tables = pd.read_html(StringIO(html), flavor="lxml")
    if not tables:
        raise RuntimeError("No HTML tables found in TRADESTAT response.")

    # Select the table that contains Country and a growth/value column.
    candidates = []
    for t in tables:
        cols = " | ".join(map(str, t.columns)).upper()
        if "COUNTRY" in cols and ("GROWTH" in cols or "2026" in cols):
            candidates.append(t)

    table = max(
        candidates if candidates else tables,
        key=lambda x: x.shape[0] * max(x.shape[1], 1),
    ).copy()

    if isinstance(table.columns, pd.MultiIndex):
        table.columns = [
            " | ".join(
                str(x).strip()
                for x in col
                if str(x).strip() and str(x).lower() != "nan"
            )
            for col in table.columns
        ]
    else:
        table.columns = [str(c).strip() for c in table.columns]

    # Remove fully empty rows/columns only. Do not remove zero-valued records.
    table = table.dropna(axis=0, how="all").dropna(axis=1, how="all")
    return table


def add_lineage(
    df: pd.DataFrame,
    *,
    mode: str,
    hs: str,
    month: int,
    year: int,
    metadata: dict,
) -> pd.DataFrame:
    out = df.copy()
    out.insert(0, "source_trade_flow", mode)
    out.insert(1, "query_hs_code", hs)
    out.insert(2, "query_year", year)
    out.insert(3, "query_month", month)
    out.insert(4, "query_month_name", MONTH_NAMES[month])

    for key, value in metadata.items():
        out[f"source_{key}"] = value

    return out


def load_hs_config(path: Path) -> list[str]:
    codes = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if "hs_code" not in (reader.fieldnames or []):
            raise ValueError("HS config must contain a column named hs_code.")
        for row in reader:
            hs = str(row["hs_code"]).strip()
            if hs and hs.lower() != "nan":
                codes.append(hs)
    return list(dict.fromkeys(codes))


def already_done(manifest_path: Path, key: str) -> bool:
    if not manifest_path.exists():
        return False
    with manifest_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
                if row.get("key") == key and row.get("status") == "success":
                    return True
            except json.JSONDecodeError:
                continue
    return False


def log_manifest(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_query(
    session: requests.Session,
    *,
    mode: str,
    hs: str,
    month: int,
    year: int,
    root: Path,
    manifest: Path,
    overwrite: bool = False,
    value_selector: str = "1",
    value_unit: str = "USD_MILLION",
) -> None:
    key = f"{mode}|{hs}|{year}|{month:02d}"

    if not overwrite and already_done(manifest, key):
        print(f"SKIP {key} (already successful)")
        return

    raw_dir = root / "raw" / mode / hs
    parsed_dir = root / "parsed" / mode
    raw_dir.mkdir(parents=True, exist_ok=True)
    parsed_dir.mkdir(parents=True, exist_ok=True)

    raw_path = raw_dir / f"{hs}_{year}_{month:02d}.html"
    csv_path = parsed_dir / f"{mode}_{hs}_{year}_{month:02d}.csv"

    started = time.time()

    try:
        html, request_meta = post_report(session, mode, hs, month, year, value_selector=value_selector)
        request_meta["value_unit"] = value_unit

        # Preserve the exact server response before parsing.
        raw_path.write_text(html, encoding="utf-8")

        source_meta = extract_metadata(html)
        df = parse_country_table(html)
        df = add_lineage(
            df,
            mode=mode,
            hs=hs,
            month=month,
            year=year,
            metadata={**source_meta, "value_unit": value_unit},
        )
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")

        record = {
            "key": key,
            "status": "success",
            "raw_file": str(raw_path),
            "parsed_file": str(csv_path),
            "rows": int(len(df)),
            "seconds": round(time.time() - started, 2),
            **request_meta,
            **source_meta,
        }
        log_manifest(manifest, record)

        print(
            f"OK  {key} | rows={len(df)} | "
            f"{time.time() - started:.1f}s"
        )

    except Exception as exc:
        record = {
            "key": key,
            "status": "error",
            "error": repr(exc),
            "seconds": round(time.time() - started, 2),
        }
        log_manifest(manifest, record)
        print(f"ERROR {key}: {exc}")


def parse_args():
    p = argparse.ArgumentParser(
        description="GemStrategy real-data TRADESTAT collector"
    )
    p.add_argument("--mode", choices=["export", "import"], default="export")
    p.add_argument("--hs", help="Single 8-digit HS code for pilot")
    p.add_argument("--config", help="CSV containing hs_code column")
    p.add_argument("--year", type=int)
    p.add_argument("--month", type=int, choices=range(1, 13))
    p.add_argument("--start-year", type=int)
    p.add_argument("--end-year", type=int)
    p.add_argument("--start-month", type=int, choices=range(1, 13), default=1)
    p.add_argument("--end-month", type=int, choices=range(1, 13), default=12)
    p.add_argument("--out", default="gemstrategy_data")
    p.add_argument("--sleep", type=float, default=1.5)
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--value-selector", help="TRADESTAT Values-in selector. USD Million: 1; ₹ Crore: 3; Quantity: 2. GemStrategy uses USD Million for both trade flows.")
    return p.parse_args()


def main():
    args = parse_args()
    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "extraction_manifest.jsonl"

    if args.hs:
        hs_codes = [args.hs]
    elif args.config:
        hs_codes = load_hs_config(Path(args.config))
    else:
        raise SystemExit("Provide --hs for a pilot or --config for a batch run.")

    # Single month/year pilot.
    if args.year is not None or args.month is not None:
        if args.year is None or args.month is None:
            raise SystemExit("--year and --month must be supplied together.")
        periods = [(args.year, args.month)]
    else:
        if args.start_year is None or args.end_year is None:
            raise SystemExit(
                "For batch mode provide --start-year and --end-year, "
                "or use --year/--month for a pilot."
            )
        periods = []
        for y in range(args.start_year, args.end_year + 1):
            m_start = args.start_month if y == args.start_year else 1
            m_end = args.end_month if y == args.end_year else 12
            periods.extend((y, m) for m in range(m_start, m_end + 1))

    print("GemStrategy TRADESTAT collector")
    value_selector = args.value_selector or DEFAULT_VALUE_SELECTOR[args.mode]

    if value_selector == "1":
        value_unit = "USD_MILLION"
    elif value_selector == "3":
        value_unit = "INR_CRORE"
    elif value_selector == "2":
        value_unit = "QUANTITY"
    else:
        value_unit = f"TRADESTAT_SELECTOR_{value_selector}"

    print(f"Mode: {args.mode}")
    print(f"Value selector: {value_selector}")
    print(f"Value unit: {value_unit}")
    print(f"Value selector: {value_selector}")
    print(f"HS codes: {len(hs_codes)}")
    print(f"Periods: {len(periods)}")
    print("REAL DATA ONLY - no synthetic/fill values.")
    print()

    session = make_session()

    for hs in hs_codes:
        for year, month in periods:
            run_query(
                session,
                mode=args.mode,
                hs=hs,
                month=month,
                year=year,
                root=root,
                manifest=manifest,
                overwrite=args.overwrite,
                value_selector=value_selector,
                value_unit=value_unit,
            )
            time.sleep(args.sleep)


if __name__ == "__main__":
    main()
