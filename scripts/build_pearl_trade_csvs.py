"""Create clean, country-level pearl import and export fact files from TRADESTAT raw HTML.

The source tables use a different monthly-value column name for every report.
This program selects the column that exactly matches the report's requested
month/year, preserving the government Total row with an explicit record type.
"""

from __future__ import annotations

import re
from io import StringIO
from pathlib import Path

import pandas as pd

from gemstrategy_tradestat_USD_FIXED import extract_metadata


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "gemstrategy_data"
HS_MASTER = pd.read_csv(ROOT / "hs_codes.csv", dtype={"hs_code": "string"})
OUTPUT_COLUMNS = [
    "trade_flow", "period_date", "year", "month_number", "month_name",
    "hs_code", "product_category", "product_description", "partner_country",
    "record_type", "trade_value_usd_million", "observation_status",
    "reported_quantity_unit", "source_report_date", "source_value_column",
    "source_file",
]
PEARL_FAMILIES = {"Natural Pearls", "Cultured Pearls"}


def value_column(columns: pd.Index, year: int, month: int) -> str:
    """Return the report column for the requested calendar month and year."""
    month_abbr = pd.Timestamp(year=year, month=month, day=1).strftime("%b")
    wanted = re.compile(rf"\b{month_abbr}-{year}\b", re.IGNORECASE)
    candidates = [str(column) for column in columns if wanted.search(str(column))]
    if len(candidates) != 1:
        raise ValueError(
            f"Expected one value column for {year}-{month:02d}; found {candidates}"
        )
    return candidates[0]


def parse_country_table(html: str) -> pd.DataFrame:
    """Parse the country table using the installed lxml HTML parser only."""
    tables = pd.read_html(StringIO(html), flavor="lxml")
    candidates = []
    for table in tables:
        columns = " | ".join(map(str, table.columns)).upper()
        if "COUNTRY" in columns and ("GROWTH" in columns or "20" in columns):
            candidates.append(table)
    table = max(candidates if candidates else tables, key=lambda item: item.shape[0] * max(item.shape[1], 1)).copy()
    if isinstance(table.columns, pd.MultiIndex):
        table.columns = [
            " | ".join(str(value).strip() for value in column if str(value).strip() and str(value).lower() != "nan")
            for column in table.columns
        ]
    else:
        table.columns = [str(column).strip() for column in table.columns]
    return table.dropna(axis=0, how="all").dropna(axis=1, how="all")


def numeric_value(series: pd.Series) -> pd.Series:
    cleaned = (
        series.astype("string")
        .str.replace(",", "", regex=False)
        .str.strip()
        .replace({"-": pd.NA, "": pd.NA, "nan": pd.NA})
    )
    return pd.to_numeric(cleaned, errors="coerce")


def build_flow(flow: str) -> pd.DataFrame:
    master = HS_MASTER.set_index("hs_code")
    records: list[pd.DataFrame] = []
    raw_root = DATA / "raw" / flow

    for raw_path in sorted(raw_root.glob("*/*.html")):
        match = re.fullmatch(r"(\d{8})_(\d{4})_(\d{2})", raw_path.stem)
        if not match:
            raise ValueError(f"Unexpected raw filename: {raw_path.name}")
        hs_code, year_text, month_text = match.groups()
        year, month = int(year_text), int(month_text)
        if hs_code not in master.index or master.loc[hs_code, "category"] not in PEARL_FAMILIES:
            continue
        html = raw_path.read_text(encoding="utf-8")
        try:
            table = parse_country_table(html)
        except ValueError as exc:
            if "No tables found" not in str(exc):
                raise
            # TRADESTAT returned a page with no country table. This is distinct
            # from a zero-valued trade observation, so leave this HS-month absent.
            continue
        metadata = extract_metadata(html)
        selected_value_column = value_column(table.columns, year, month)

        country_column = next(
            (column for column in table.columns if str(column).strip().lower() == "country"),
            None,
        )
        if country_column is None:
            raise ValueError(f"Country column not found: {raw_path}")
        product = master.loc[hs_code]
        countries = table[country_column].astype("string").str.strip()
        status_match = re.search(r"\(([RFP])\)", selected_value_column, re.IGNORECASE)
        report_date = pd.to_datetime(
            metadata.get("report_date_text"), dayfirst=True, errors="coerce"
        )
        frame = pd.DataFrame({
            "trade_flow": flow,
            "period_date": pd.Timestamp(year=year, month=month, day=1).date().isoformat(),
            "year": year,
            "month_number": month,
            "month_name": pd.Timestamp(year=year, month=month, day=1).strftime("%B"),
            "hs_code": hs_code,
            "product_category": product["category"],
            "product_description": product["description"],
            "partner_country": countries,
            "record_type": countries.str.casefold().eq("total").map({True: "TOTAL", False: "PARTNER"}),
            "trade_value_usd_million": numeric_value(table[selected_value_column]),
            "observation_status": status_match.group(1).upper() if status_match else pd.NA,
            "reported_quantity_unit": metadata.get("reported_unit"),
            "source_report_date": report_date.date().isoformat() if pd.notna(report_date) else pd.NA,
            "source_value_column": selected_value_column,
            "source_file": raw_path.relative_to(ROOT).as_posix(),
        })
        records.append(frame[OUTPUT_COLUMNS])

    result = pd.concat(records, ignore_index=True)
    result = result.sort_values(
        ["period_date", "hs_code", "record_type", "partner_country"],
        kind="stable",
    ).reset_index(drop=True)
    if result.duplicated(["trade_flow", "period_date", "hs_code", "partner_country"]).any():
        raise ValueError(f"Duplicate fact grain detected in {flow}.")
    if result["trade_value_usd_million"].isna().any():
        missing = int(result["trade_value_usd_million"].isna().sum())
        raise ValueError(f"{flow} contains {missing} non-numeric monthly values; no CSV written.")
    return result


def main() -> None:
    output_dir = DATA / "curated"
    output_dir.mkdir(parents=True, exist_ok=True)
    for flow in ("import", "export"):
        result = build_flow(flow)
        output_path = output_dir / f"pearl_{flow}_country_monthly.csv"
        result.to_csv(output_path, index=False, encoding="utf-8-sig")
        print(
            f"{output_path.name}: {len(result):,} rows | "
            f"partners={(result['record_type'] == 'PARTNER').sum():,} | "
            f"totals={(result['record_type'] == 'TOTAL').sum():,}"
        )
        # Natural/Cultured are separable in Power BI via Dim_HS_Code[commodity_name]
        # directly, so no separate subset CSVs are written here.


if __name__ == "__main__":
    main()
