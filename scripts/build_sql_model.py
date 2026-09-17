"""Build the GemStrategy star schema (Section 7 of the proposal) in SQLite.

Reads the curated, all-commodity fact CSVs (gemstrategy_data/curated/
gemstrategy_{import,export}_country_monthly.csv) and loads a single portable
database file: gemstrategy_data/gemstrategy.db

Grain of Fact_Trade: Commodity (HS code) x Country x Month x Trade Flow.
"COUNTRY = TOTAL" government rows are kept as fact rows against a dedicated
"(TOTAL - All Countries)" Dim_Country entry rather than dropped, so the
official total remains queryable and reconcilable against the sum of partner
rows without polluting per-country analysis (is_total flag on the fact and a
matching flag on the dim row make it trivial to exclude in Power BI).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "gemstrategy_data"
DB_PATH = DATA / "gemstrategy.db"

TOTAL_COUNTRY_KEY = "TOTAL"
TOTAL_COUNTRY_LABEL = "(TOTAL - All Countries)"

# Continent-level region for every partner-country label observed in the
# curated data (see gemstrategy_data/country_list_for_review.txt). TRADESTAT
# country names are government-report conventions, not ISO names, so this
# mapping is keyed on the exact source label.
COUNTRY_REGION = {
    "ALBANIA": "Europe", "ARGENTINA": "South America", "ARMENIA": "Asia",
    "ARUBA": "North America", "AUSTRALIA": "Oceania", "AUSTRIA": "Europe",
    "BAHAMAS": "North America", "BAHARAIN IS": "Asia", "BANGLADESH PR": "Asia",
    "BELGIUM": "Europe", "BERMUDA": "North America", "BHUTAN": "Asia",
    "BOTSWANA": "Africa", "BRAZIL": "South America", "BRUNEI": "Asia",
    "BULGARIA": "Europe", "CANADA": "North America", "CAYMAN IS": "North America",
    "CHILE": "South America", "CHINA P RP": "Asia", "COLOMBIA": "South America",
    "CONGO D. REP.": "Africa", "CONGO P REP": "Africa", "COSTA RICA": "North America",
    "CROATIA": "Europe", "CYPRUS": "Europe", "CZECH REPUBLIC": "Europe",
    "DENMARK": "Europe", "DOMINIC REP": "North America", "ECUADOR": "South America",
    "EGYPT A RP": "Africa", "EL SALVADOR": "North America", "ESTONIA": "Europe",
    "FIJI IS": "Oceania", "FINLAND": "Europe", "FRANCE": "Europe",
    "GEORGIA": "Asia", "GERMANY": "Europe", "GHANA": "Africa",
    "GIBRALTAR": "Europe", "GREECE": "Europe", "GREENLAND": "North America",
    "GUAM": "Oceania", "GUATEMALA": "North America", "HONDURAS": "North America",
    "HONG KONG": "Asia", "HUNGARY": "Europe", "ICELAND": "Europe",
    "INDONESIA": "Asia", "IRELAND": "Europe", "ISRAEL": "Asia",
    "ITALY": "Europe", "JAPAN": "Asia", "JORDAN": "Asia",
    "KAZAKHSTAN": "Asia", "KENYA": "Africa", "KOREA RP": "Asia",
    "KUWAIT": "Asia", "KYRGHYZSTAN": "Asia", "LATVIA": "Europe",
    "LEBANON": "Asia", "LIECHTENSTEIN": "Europe", "LITHUANIA": "Europe",
    "LUXEMBOURG": "Europe", "MACAO": "Asia", "MADAGASCAR": "Africa",
    "MALAYSIA": "Asia", "MALDIVES": "Asia", "MALTA": "Europe",
    "MAURITIUS": "Africa", "MEXICO": "North America", "MOLDOVA": "Europe",
    "MONACO": "Europe", "MONGOLIA": "Asia", "MONTENEGRO": "Europe",
    "MOROCCO": "Africa", "NEPAL": "Asia", "NETHERLAND": "Europe",
    "NETHERLANDANTIL": "North America", "NEW CALEDONIA": "Oceania",
    "NEW ZEALAND": "Oceania", "NIGERIA": "Africa", "NORWAY": "Europe",
    "OMAN": "Asia", "PANAMA REPUBLIC": "North America", "PERU": "South America",
    "PHILIPPINES": "Asia", "POLAND": "Europe", "PORTUGAL": "Europe",
    "PUERTO RICO": "North America", "QATAR": "Asia", "ROMANIA": "Europe",
    "RUSSIA": "Europe", "SAUDI ARAB": "Asia", "SINGAPORE": "Asia",
    "SLOVAK REP": "Europe", "SLOVENIA": "Europe", "SOUTH AFRICA": "Africa",
    "SPAIN": "Europe", "SRI LANKA DSR": "Asia", "SWAZILAND": "Africa",
    "SWEDEN": "Europe", "SWITZERLAND": "Europe", "TAIWAN": "Asia",
    "TANZANIA REP": "Africa", "THAILAND": "Asia", "TURKEY": "Asia",
    "U ARAB EMTS": "Asia", "U K": "Europe", "U S A": "North America",
    "UKRAINE": "Europe", "UNION OF SERBIA & MONTENEGRO": "Europe",
    "UNSPECIFIED": "Unspecified", "URUGUAY": "South America",
    "UZBEKISTAN": "Asia", "VIETNAM SOC REP": "Asia", "VIRGIN IS US": "North America",
    "ZAMBIA": "Africa", "AFGHANISTAN": "Asia", "BURUNDI": "Africa",
    "DJIBOUTI": "Africa", "ETHIOPIA": "Africa", "GUINEA": "Africa",
    "LIBERIA": "Africa", "MALAWI": "Africa", "MOZAMBIQUE": "Africa",
    "MYANMAR": "Asia", "RWANDA": "Africa", "SOMALIA": "Africa",
    "US MINOR OUTLYING ISLANDS": "Oceania",
}

# Geocoding-friendly display names for the TRADESTAT labels that won't
# resolve on their own in a Power BI map visual. Anything not listed here
# falls back to the title-cased country_key, which geocodes fine as-is.
MAP_DISPLAY_NAME = {
    "U S A": "United States", "U K": "United Kingdom", "U ARAB EMTS": "United Arab Emirates",
    "CHINA P RP": "China", "KOREA RP": "South Korea", "HONG KONG": "Hong Kong",
    "SAUDI ARAB": "Saudi Arabia", "BAHARAIN IS": "Bahrain", "BANGLADESH PR": "Bangladesh",
    "EGYPT A RP": "Egypt", "SRI LANKA DSR": "Sri Lanka", "VIETNAM SOC REP": "Vietnam",
    "TANZANIA REP": "Tanzania", "CONGO D. REP.": "Democratic Republic of the Congo",
    "CONGO P REP": "Republic of the Congo", "DOMINIC REP": "Dominican Republic",
    "PANAMA REPUBLIC": "Panama", "NETHERLAND": "Netherlands",
    "NETHERLANDANTIL": "Netherlands Antilles", "SLOVAK REP": "Slovakia",
    "FIJI IS": "Fiji", "CAYMAN IS": "Cayman Islands", "VIRGIN IS US": "U.S. Virgin Islands",
    "KYRGHYZSTAN": "Kyrgyzstan", "UNION OF SERBIA & MONTENEGRO": "Serbia",
    "US MINOR OUTLYING ISLANDS": "United States Minor Outlying Islands",
    "UNSPECIFIED": "", "TOTAL": "",
}

PEARL_OR_GEMSTONE = {
    "Natural Pearls": "Pearl", "Cultured Pearls": "Pearl",
    "Emerald": "Coloured Gemstone", "Ruby": "Coloured Gemstone", "Sapphire": "Coloured Gemstone",
}


def load_facts() -> pd.DataFrame:
    frames = [
        pd.read_csv(DATA / "curated" / f"gemstrategy_{flow}_country_monthly.csv", dtype={"hs_code": "string"})
        for flow in ("import", "export")
    ]
    return pd.concat(frames, ignore_index=True)


def build_dim_date(facts: pd.DataFrame) -> pd.DataFrame:
    dates = pd.to_datetime(facts["period_date"].unique())
    df = pd.DataFrame({"period_date": dates}).sort_values("period_date").reset_index(drop=True)
    df["date_key"] = df["period_date"].dt.strftime("%Y%m").astype(int)
    df["year"] = df["period_date"].dt.year
    df["month_number"] = df["period_date"].dt.month
    df["month_name"] = df["period_date"].dt.strftime("%B")
    df["quarter"] = df["period_date"].dt.quarter
    # Indian financial year: Apr(y) - Mar(y+1) is FY y/y+1
    fy_start = df["year"].where(df["month_number"] >= 4, df["year"] - 1)
    df["financial_year"] = fy_start.astype(str) + "-" + (fy_start + 1).astype(str).str[-2:]
    df["period_date"] = df["period_date"].dt.date.astype(str)
    return df[["date_key", "period_date", "year", "month_number", "month_name", "quarter", "financial_year"]]


def build_dim_country(facts: pd.DataFrame) -> pd.DataFrame:
    partners = sorted(facts.loc[facts["record_type"] == "PARTNER", "partner_country"].dropna().unique())
    rows = [
        {
            "country_key": name,
            "country_name": name.title(),
            "map_display_name": MAP_DISPLAY_NAME.get(name, name.title()),
            "region": COUNTRY_REGION.get(name, "Unclassified"),
            "is_total": 0,
        }
        for name in partners
    ]
    rows.append({
        "country_key": TOTAL_COUNTRY_KEY, "country_name": TOTAL_COUNTRY_LABEL,
        "map_display_name": "", "region": "Unspecified", "is_total": 1,
    })
    df = pd.DataFrame(rows)
    unclassified = df.loc[df["region"] == "Unclassified", "country_key"].tolist()
    if unclassified:
        raise ValueError(f"Unmapped countries need a region in COUNTRY_REGION: {unclassified}")
    return df


def build_dim_hs_code() -> pd.DataFrame:
    """One row per HS code. Commodity name/group are folded in directly rather
    than living in a separate Dim_Commodity table - Dim_HS_Code is already
    exactly one row per commodity variant (10 rows, 2 per commodity), so a
    snowflaked commodity dimension would add a join hop for zero benefit.
    """
    hs = pd.read_csv(ROOT / "hs_codes.csv", dtype={"hs_code": "string"})
    hs["worked_status"] = hs["description"].str.contains("unworked", case=False).map(
        {True: "Unworked / Simply Sawn", False: "Otherwise Worked"}
    )
    hs = hs.rename(columns={"category": "commodity_name"})
    hs["commodity_group"] = hs["commodity_name"].map(PEARL_OR_GEMSTONE)
    return hs[["hs_code", "commodity_name", "commodity_group", "description", "worked_status"]]


def build_fact_trade(facts: pd.DataFrame) -> pd.DataFrame:
    df = facts.copy()
    df["date_key"] = pd.to_datetime(df["period_date"]).dt.strftime("%Y%m").astype(int)
    df["country_key"] = df["partner_country"].where(df["record_type"] == "PARTNER", TOTAL_COUNTRY_KEY)
    df["is_total"] = (df["record_type"] == "TOTAL").astype(int)
    df = df.rename(columns={"trade_flow": "trade_flow_key"})
    out = df[[
        "date_key", "hs_code", "country_key", "trade_flow_key", "is_total",
        "trade_value_usd_million", "observation_status", "reported_quantity_unit",
        "source_report_date", "source_file",
    ]].reset_index(drop=True)
    out.insert(0, "fact_id", out.index + 1)
    return out


def main() -> None:
    facts = load_facts()

    dim_date = build_dim_date(facts)
    dim_country = build_dim_country(facts)
    dim_hs_code = build_dim_hs_code()
    fact_trade = build_fact_trade(facts)

    # Referential integrity: every fact key must resolve to a dim row.
    assert set(fact_trade["date_key"]) <= set(dim_date["date_key"]), "Fact_Trade has date_key not in Dim_Date"
    assert set(fact_trade["country_key"]) <= set(dim_country["country_key"]), "Fact_Trade has country_key not in Dim_Country"
    assert set(fact_trade["hs_code"]) <= set(dim_hs_code["hs_code"]), "Fact_Trade has hs_code not in Dim_HS_Code"
    assert set(fact_trade["trade_flow_key"]) <= {"import", "export"}, "Fact_Trade has an unexpected trade_flow_key"

    powerbi_dir = DATA / "powerbi_import"
    powerbi_dir.mkdir(parents=True, exist_ok=True)
    for name, table in (
        ("Dim_Date", dim_date), ("Dim_Country", dim_country),
        ("Dim_HS_Code", dim_hs_code), ("Fact_Trade", fact_trade),
    ):
        table.to_csv(powerbi_dir / f"{name}.csv", index=False, encoding="utf-8-sig")

    DB_PATH.unlink(missing_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        dim_date.to_sql("Dim_Date", conn, index=False)
        dim_country.to_sql("Dim_Country", conn, index=False)
        dim_hs_code.to_sql("Dim_HS_Code", conn, index=False)
        fact_trade.to_sql("Fact_Trade", conn, index=False)

        conn.executescript("""
            CREATE UNIQUE INDEX idx_dim_date_key ON Dim_Date(date_key);
            CREATE UNIQUE INDEX idx_dim_country_key ON Dim_Country(country_key);
            CREATE UNIQUE INDEX idx_dim_hs_code ON Dim_HS_Code(hs_code);
            CREATE INDEX idx_fact_date ON Fact_Trade(date_key);
            CREATE INDEX idx_fact_country ON Fact_Trade(country_key);
            CREATE INDEX idx_fact_hs ON Fact_Trade(hs_code);
            CREATE INDEX idx_fact_flow ON Fact_Trade(trade_flow_key);
        """)
        conn.commit()
    finally:
        conn.close()

    print(f"Dim_Date: {len(dim_date):,} rows ({dim_date['period_date'].min()} to {dim_date['period_date'].max()})")
    print(f"Dim_Country: {len(dim_country):,} rows ({dim_country['region'].nunique()} regions)")
    print(f"Dim_HS_Code: {len(dim_hs_code):,} rows")
    print(f"Fact_Trade: {len(fact_trade):,} rows "
          f"(partner={int((fact_trade['is_total'] == 0).sum()):,}, total={int((fact_trade['is_total'] == 1).sum()):,})")
    print(f"\nWritten to {DB_PATH}")
    print(f"Power BI import CSVs written to {powerbi_dir}")


if __name__ == "__main__":
    main()
