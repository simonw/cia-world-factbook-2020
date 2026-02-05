"""
Extract CIA World Factbook 2020 data from HTML files into a SQLite database.

Usage:
    python extract.py [--output factbook.db] [--data-dir .]
"""

import argparse
import os
import re

from bs4 import BeautifulSoup, Tag
import sqlite_utils


def get_country_files(data_dir):
    """Return sorted list of (code, filepath) for non-print country HTML files."""
    geos_dir = os.path.join(data_dir, "geos")
    results = []
    for filename in sorted(os.listdir(geos_dir)):
        if filename.startswith("print_") or not filename.endswith(".html"):
            continue
        code = filename.replace(".html", "")
        results.append((code, os.path.join(geos_dir, filename)))
    return results


def parse_country_name_and_region(soup):
    """Extract country name and region from the page title and header."""
    title_tag = soup.find("title")
    name = ""
    region = ""
    if title_tag:
        # Title format: "Region :: Country Name — The World Factbook"
        title_text = title_tag.get_text()
        match = re.match(r"(.+?)\s*::\s*(.+?)\s*—", title_text)
        if match:
            region = match.group(1).strip()
            name = match.group(2).strip()
        else:
            # Fallback: some pages like "The World Factbook"
            match2 = re.match(r"(.+?)\s*—", title_text)
            if match2:
                name = match2.group(1).strip()
    return name, region


def clean_text(text):
    """Clean extracted text: normalize whitespace, strip."""
    if text is None:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_field_name_from_anchor(category_div):
    """Extract the field name from a category div (the anchor/header for a field)."""
    link = category_div.find("a", href=re.compile(r"notesanddefs\.html"))
    if link:
        return clean_text(link.get_text())
    return None


def extract_field_id_from_anchor(category_div):
    """Extract the numeric field ID from the anchor div's field listing link."""
    link = category_div.find("a", href=re.compile(r"\.\./fields/\d+"))
    if link:
        href = link.get("href", "")
        match = re.search(r"/fields/(\d+)", href)
        if match:
            return match.group(1)
    # Also try the notesanddefs link
    link = category_div.find("a", href=re.compile(r"notesanddefs\.html#(\d+)"))
    if link:
        href = link.get("href", "")
        match = re.search(r"#(\d+)", href)
        if match:
            return match.group(1)
    return None


def extract_section_name(question_div):
    """Extract section name from a question div."""
    return question_div.get("sectiontitle", "").strip()


def parse_field_data(field_div):
    """
    Parse a field data div and return a list of subfield dicts.

    Each dict has: subfield_name, value, numeric_value, note, date_info
    """
    if field_div is None:
        return []

    subfields = []

    # Find all category_data divs (direct and nested)
    data_divs = field_div.find_all("div", class_="category_data")

    for data_div in data_divs:
        # Skip if this div itself contains a "country comparison" span
        comparison_span = data_div.find("span", class_="category", string=re.compile(r"country comparison"))
        if comparison_span:
            continue

        # Skip attachment divs (images, maps)
        if "attachment" in data_div.get("class", []):
            continue

        # Check if it's a note
        if "note" in data_div.get("class", []):
            note_text = clean_text(data_div.get_text())
            # Remove "note:" prefix
            note_text = re.sub(r"^note:\s*", "", note_text, flags=re.IGNORECASE)
            subfields.append({
                "subfield_name": "note",
                "value": note_text,
                "numeric_value": None,
                "note": "",
                "date_info": "",
            })
            continue

        subfield_name_el = data_div.find("span", class_="subfield-name")
        group_el = data_div.find("span", class_="subfield-group")
        number_el = data_div.find("span", class_="subfield-number")
        note_el = data_div.find("span", class_="subfield-note")
        date_el = data_div.find("span", class_="subfield-date")

        subfield_name = ""
        if group_el:
            subfield_name = clean_text(group_el.get_text()).rstrip(":")
        if subfield_name_el:
            prefix = subfield_name
            name_part = clean_text(subfield_name_el.get_text()).rstrip(":")
            if prefix:
                subfield_name = f"{prefix} - {name_part}"
            else:
                subfield_name = name_part

        # For grouped subfields with multiple subfield-name spans
        if "grouped_subfield" in data_div.get("class", []):
            # Parse all name/number pairs in this grouped div
            all_names = data_div.find_all("span", class_="subfield-name")
            all_numbers = data_div.find_all("span", class_="subfield-number")
            all_dates = data_div.find_all("span", class_="subfield-date")
            group_prefix = ""
            if group_el:
                group_prefix = clean_text(group_el.get_text()).rstrip(":")

            if len(all_names) > 1:
                # Multiple subfields in one div
                for i, name_span in enumerate(all_names):
                    sf_name = clean_text(name_span.get_text()).rstrip(":")
                    if group_prefix:
                        sf_name = f"{group_prefix} - {sf_name}"
                    sf_number = clean_text(all_numbers[i].get_text()) if i < len(all_numbers) else ""
                    sf_date = clean_text(all_dates[i].get_text()) if i < len(all_dates) else ""

                    numeric_val = parse_numeric(sf_number)
                    subfields.append({
                        "subfield_name": sf_name,
                        "value": sf_number,
                        "numeric_value": numeric_val,
                        "note": "",
                        "date_info": sf_date.strip("()"),
                    })
                continue

        # Standard field handling
        value = ""
        note = ""
        date_info = ""

        if number_el:
            value = clean_text(number_el.get_text())
        if note_el:
            note = clean_text(note_el.get_text())

        # If no number, check for text content
        if not value and not note:
            # Get the text content, excluding nested spans we've already extracted
            # and excluding child category_data divs
            text_parts = []
            for child in data_div.children:
                if isinstance(child, Tag):
                    if "category_data" in child.get("class", []):
                        continue
                    if child.name == "span" and any(
                        c in child.get("class", [])
                        for c in ["subfield-name", "subfield-group", "subfield-date"]
                    ):
                        continue
                    text_parts.append(clean_text(child.get_text()))
                else:
                    text_parts.append(clean_text(str(child)))
            value = clean_text(" ".join(text_parts))
            # Remove leading subfield name if present
            if subfield_name and value.startswith(subfield_name):
                value = value[len(subfield_name):].strip().lstrip(":")

        if date_el:
            date_info = clean_text(date_el.get_text()).strip("()")

        # Combine number and note for full value
        if value and note:
            full_value = f"{value} {note}".strip()
        elif note:
            full_value = note
        else:
            full_value = value

        numeric_val = parse_numeric(value) if value else parse_numeric(note)

        if full_value or subfield_name:
            subfields.append({
                "subfield_name": subfield_name,
                "value": full_value,
                "numeric_value": numeric_val,
                "note": note,
                "date_info": date_info,
            })

    # If no subfields found, try getting the raw text from the field div
    if not subfields:
        text = clean_text(field_div.get_text())
        if text:
            subfields.append({
                "subfield_name": "",
                "value": text,
                "numeric_value": None,
                "note": "",
                "date_info": "",
            })

    return subfields


def parse_numeric(text):
    """Try to parse a numeric value from text. Return float or None."""
    if not text:
        return None
    # Remove commas, $, %, and common units
    cleaned = text.strip()
    cleaned = re.sub(r"[\$,%]", "", cleaned)
    cleaned = re.sub(r"\s*(sq km|km|nm|m|years|per 1,000|of GDP|million|billion|trillion).*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace(",", "").strip()
    # Handle negative numbers
    cleaned = re.sub(r"^\((\d)", r"-\1", cleaned)
    cleaned = cleaned.rstrip(")")
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def get_full_field_text(field_div):
    """Get the full text content of a field div, cleaned up."""
    if field_div is None:
        return ""
    # Remove image modals and comparison links
    for modal in field_div.find_all("div", class_="modal"):
        modal.decompose()
    for comp in field_div.find_all("span", class_="category", string=re.compile(r"country comparison")):
        parent = comp.parent
        if parent:
            parent.decompose()
    text = clean_text(field_div.get_text())
    return text


def parse_country_page(filepath):
    """
    Parse a country HTML page and return:
    - country_info: dict with name, region
    - sections_data: list of (section, field_name, field_id, full_text, subfields)
    """
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        soup = BeautifulSoup(f, "lxml")

    name, region = parse_country_name_and_region(soup)
    country_info = {"name": name, "region": region}

    sections_data = []
    current_section = ""

    # Find all question divs (section headers) and category divs (field headers)
    # Walk through the document structure
    question_divs = soup.find_all("div", class_="question")
    section_map = {}
    for q in question_divs:
        section_name = extract_section_name(q)
        if section_name:
            # Find the parent li section
            parent_li = q.find_parent("li")
            if parent_li:
                section_id = parent_li.get("id", "")
                section_map[section_id.replace("-category-section-anchor", "")] = section_name

    # Find all field anchor divs
    field_anchors = soup.find_all("div", id=re.compile(r"^field-anchor-"))

    for anchor in field_anchors:
        # Determine section from the anchor id
        anchor_id = anchor.get("id", "")
        # field-anchor-SECTION-FIELD -> extract section
        parts = anchor_id.replace("field-anchor-", "").split("-")

        # Find section from parent li
        parent_li = anchor.find_parent("li")
        if parent_li:
            li_id = parent_li.get("id", "")
            # li id is like "geography-category-section"
            section_key = li_id.replace("-category-section", "")
            current_section = section_map.get(section_key, section_key.replace("-", " ").title())
        elif parts:
            # Fallback: derive from anchor id
            # Try to find section from the anchor id pattern
            for sec_key, sec_name in section_map.items():
                if anchor_id.startswith(f"field-anchor-{sec_key.replace(' ', '-').lower()}"):
                    current_section = sec_name
                    break

        field_name = extract_field_name_from_anchor(anchor)
        field_id = extract_field_id_from_anchor(anchor)

        if not field_name:
            continue

        # Find the corresponding field data div
        # It should be the next sibling div with id starting with "field-"
        field_div = anchor.find_next_sibling("div", id=re.compile(r"^field-"))
        if field_div is None:
            continue

        # Make a copy so decomposing modals doesn't affect parsing
        field_div_copy = BeautifulSoup(str(field_div), "lxml").find("div", id=re.compile(r"^field-"))
        full_text = get_full_field_text(field_div_copy) if field_div_copy else ""

        subfields = parse_field_data(field_div)

        sections_data.append((current_section, field_name, field_id, full_text, subfields))

    return country_info, sections_data


def create_database(db_path, data_dir):
    """Create the SQLite database and populate it from the factbook HTML files."""
    db = sqlite_utils.Database(db_path, recreate=True)

    # Create tables with explicit schema and foreign keys
    db.execute("""
        CREATE TABLE countries (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            region TEXT NOT NULL
        )
    """)

    db.execute("""
        CREATE TABLE fields (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL
        )
    """)

    db.execute("""
        CREATE TABLE facts (
            id INTEGER PRIMARY KEY,
            country_code TEXT NOT NULL REFERENCES countries(code),
            field_id TEXT NOT NULL REFERENCES fields(id),
            section TEXT NOT NULL,
            value TEXT NOT NULL
        )
    """)

    db.execute("""
        CREATE TABLE field_details (
            id INTEGER PRIMARY KEY,
            country_code TEXT NOT NULL REFERENCES countries(code),
            field_id TEXT NOT NULL REFERENCES fields(id),
            section TEXT NOT NULL,
            subfield_name TEXT NOT NULL,
            value TEXT NOT NULL,
            numeric_value REAL,
            note TEXT NOT NULL DEFAULT '',
            date_info TEXT NOT NULL DEFAULT ''
        )
    """)

    country_files = get_country_files(data_dir)
    fact_id = 1
    detail_id = 1

    facts_batch = []
    details_batch = []
    countries_batch = []
    fields_seen = {}

    for code, filepath in country_files:
        country_info, sections_data = parse_country_page(filepath)

        countries_batch.append(
            {"code": code, "name": country_info["name"], "region": country_info["region"]}
        )

        for section, field_name, field_id, full_text, subfields in sections_data:
            if not field_id:
                continue

            # Track unique fields
            if field_id not in fields_seen:
                fields_seen[field_id] = field_name

            if full_text:
                facts_batch.append(
                    {
                        "id": fact_id,
                        "country_code": code,
                        "field_id": field_id,
                        "section": section,
                        "value": full_text,
                    }
                )
                fact_id += 1

            for sf in subfields:
                details_batch.append(
                    {
                        "id": detail_id,
                        "country_code": code,
                        "field_id": field_id,
                        "section": section,
                        "subfield_name": sf["subfield_name"],
                        "value": sf["value"],
                        "numeric_value": sf["numeric_value"],
                        "note": sf["note"],
                        "date_info": sf["date_info"],
                    }
                )
                detail_id += 1

    # Batch insert - fields first (referenced by facts and field_details)
    fields_batch = [{"id": fid, "name": fname} for fid, fname in fields_seen.items()]
    if fields_batch:
        db["fields"].insert_all(fields_batch)
    if countries_batch:
        db["countries"].insert_all(countries_batch)
    if facts_batch:
        db["facts"].insert_all(facts_batch)
    if details_batch:
        db["field_details"].insert_all(details_batch)

    # Add indexes for common queries
    db["facts"].create_index(["country_code"], if_not_exists=True)
    db["facts"].create_index(["field_id"], if_not_exists=True)
    db["facts"].create_index(["section"], if_not_exists=True)
    db["facts"].create_index(["country_code", "field_id"], if_not_exists=True)
    db["field_details"].create_index(["country_code"], if_not_exists=True)
    db["field_details"].create_index(["field_id"], if_not_exists=True)
    db["field_details"].create_index(["country_code", "field_id"], if_not_exists=True)

    # Enable FTS on facts (include field name via join-able field_id)
    db["facts"].enable_fts(["value", "section"], create_triggers=True)

    # Create views for interesting data perspectives
    _create_views(db)

    return db


def _create_views(db):
    """Create SQL views that provide interesting perspectives on the data."""

    db.execute("""
        CREATE VIEW IF NOT EXISTS country_population AS
        SELECT
            c.code,
            c.name,
            c.region,
            fd.value AS population_text,
            fd.numeric_value AS population,
            fd.date_info
        FROM countries c
        JOIN field_details fd ON fd.country_code = c.code
        JOIN fields fl ON fl.id = fd.field_id
        WHERE fl.name = 'Population'
            AND fd.subfield_name = ''
            AND fd.numeric_value IS NOT NULL
        ORDER BY fd.numeric_value DESC
    """)

    db.execute("""
        CREATE VIEW IF NOT EXISTS country_area AS
        SELECT
            c.code,
            c.name,
            c.region,
            total.numeric_value AS total_sq_km,
            land.numeric_value AS land_sq_km,
            water.numeric_value AS water_sq_km
        FROM countries c
        JOIN fields fl ON fl.name = 'Area'
        LEFT JOIN field_details total
            ON total.country_code = c.code
            AND total.field_id = fl.id
            AND total.subfield_name = 'total'
        LEFT JOIN field_details land
            ON land.country_code = c.code
            AND land.field_id = fl.id
            AND land.subfield_name = 'land'
        LEFT JOIN field_details water
            ON water.country_code = c.code
            AND water.field_id = fl.id
            AND water.subfield_name = 'water'
        WHERE total.numeric_value IS NOT NULL
        ORDER BY total.numeric_value DESC
    """)

    db.execute("""
        CREATE VIEW IF NOT EXISTS country_gdp AS
        SELECT
            c.code,
            c.name,
            c.region,
            fd.value AS gdp_text,
            fd.numeric_value AS gdp_value,
            fd.date_info
        FROM countries c
        JOIN field_details fd ON fd.country_code = c.code
        JOIN fields fl ON fl.id = fd.field_id
        WHERE fl.name = 'GDP (purchasing power parity) - real'
            AND fd.subfield_name = ''
            AND fd.numeric_value IS NOT NULL
        ORDER BY fd.numeric_value DESC
    """)

    db.execute("""
        CREATE VIEW IF NOT EXISTS country_population_growth AS
        SELECT
            c.code,
            c.name,
            c.region,
            fd.value AS growth_rate_text,
            fd.numeric_value AS growth_rate_pct,
            fd.date_info
        FROM countries c
        JOIN field_details fd ON fd.country_code = c.code
        JOIN fields fl ON fl.id = fd.field_id
        WHERE fl.name = 'Population growth rate'
            AND fd.numeric_value IS NOT NULL
        ORDER BY fd.numeric_value DESC
    """)

    db.execute("""
        CREATE VIEW IF NOT EXISTS country_life_expectancy AS
        SELECT
            c.code,
            c.name,
            c.region,
            total.numeric_value AS total_years,
            male.numeric_value AS male_years,
            female.numeric_value AS female_years,
            total.date_info
        FROM countries c
        JOIN fields fl ON fl.name = 'Life expectancy at birth'
        JOIN field_details total
            ON total.country_code = c.code
            AND total.field_id = fl.id
            AND total.subfield_name = 'total population'
        LEFT JOIN field_details male
            ON male.country_code = c.code
            AND male.field_id = fl.id
            AND male.subfield_name = 'male'
        LEFT JOIN field_details female
            ON female.country_code = c.code
            AND female.field_id = fl.id
            AND female.subfield_name = 'female'
        WHERE total.numeric_value IS NOT NULL
        ORDER BY total.numeric_value DESC
    """)

    db.execute("""
        CREATE VIEW IF NOT EXISTS country_languages AS
        SELECT
            c.code,
            c.name,
            c.region,
            f.value AS languages
        FROM countries c
        JOIN facts f ON f.country_code = c.code
        JOIN fields fl ON fl.id = f.field_id
        WHERE fl.name = 'Languages'
        ORDER BY c.name
    """)

    db.execute("""
        CREATE VIEW IF NOT EXISTS country_religions AS
        SELECT
            c.code,
            c.name,
            c.region,
            f.value AS religions
        FROM countries c
        JOIN facts f ON f.country_code = c.code
        JOIN fields fl ON fl.id = f.field_id
        WHERE fl.name = 'Religions'
        ORDER BY c.name
    """)

    db.execute("""
        CREATE VIEW IF NOT EXISTS country_government_type AS
        SELECT
            c.code,
            c.name,
            c.region,
            f.value AS government_type
        FROM countries c
        JOIN facts f ON f.country_code = c.code
        JOIN fields fl ON fl.id = f.field_id
        WHERE fl.name = 'Government type'
        ORDER BY c.name
    """)

    db.execute("""
        CREATE VIEW IF NOT EXISTS country_internet_users AS
        SELECT
            c.code,
            c.name,
            c.region,
            total.value AS total_users_text,
            total.numeric_value AS total_users,
            pct.value AS pct_text,
            pct.numeric_value AS pct_of_population,
            total.date_info
        FROM countries c
        JOIN fields fl ON fl.name = 'Internet users'
        JOIN field_details total
            ON total.country_code = c.code
            AND total.field_id = fl.id
            AND total.subfield_name = 'total'
        LEFT JOIN field_details pct
            ON pct.country_code = c.code
            AND pct.field_id = fl.id
            AND pct.subfield_name = 'percent of population'
        WHERE total.numeric_value IS NOT NULL
        ORDER BY total.numeric_value DESC
    """)

    db.execute("""
        CREATE VIEW IF NOT EXISTS country_military_expenditures AS
        SELECT
            c.code,
            c.name,
            c.region,
            fd.value AS expenditure_text,
            fd.numeric_value AS pct_of_gdp,
            fd.date_info
        FROM countries c
        JOIN field_details fd ON fd.country_code = c.code
        JOIN fields fl ON fl.id = fd.field_id
        WHERE fl.name = 'Military expenditures'
            AND fd.numeric_value IS NOT NULL
            AND fd.date_info != ''
        ORDER BY fd.numeric_value DESC
    """)

    db.execute("""
        CREATE VIEW IF NOT EXISTS country_unemployment AS
        SELECT
            c.code,
            c.name,
            c.region,
            fd.value AS unemployment_text,
            fd.numeric_value AS unemployment_pct,
            fd.date_info
        FROM countries c
        JOIN field_details fd ON fd.country_code = c.code
        JOIN fields fl ON fl.id = fd.field_id
        WHERE fl.name = 'Unemployment rate'
            AND fd.numeric_value IS NOT NULL
            AND fd.date_info != ''
        ORDER BY fd.numeric_value DESC
    """)

    db.execute("""
        CREATE VIEW IF NOT EXISTS country_electricity_sources AS
        SELECT
            c.code,
            c.name,
            c.region,
            fd.subfield_name AS source,
            fd.value AS value_text,
            fd.numeric_value AS pct,
            fd.date_info
        FROM countries c
        JOIN field_details fd ON fd.country_code = c.code
        JOIN fields fl ON fl.id = fd.field_id
        WHERE fl.name = 'Electricity - production by source'
            AND fd.subfield_name != ''
            AND fd.numeric_value IS NOT NULL
        ORDER BY c.name, fd.subfield_name
    """)

    db.execute("""
        CREATE VIEW IF NOT EXISTS country_climate AS
        SELECT
            c.code,
            c.name,
            c.region,
            f.value AS climate
        FROM countries c
        JOIN facts f ON f.country_code = c.code
        JOIN fields fl ON fl.id = f.field_id
        WHERE fl.name = 'Climate'
        ORDER BY c.name
    """)

    db.execute("""
        CREATE VIEW IF NOT EXISTS country_natural_resources AS
        SELECT
            c.code,
            c.name,
            c.region,
            f.value AS natural_resources
        FROM countries c
        JOIN facts f ON f.country_code = c.code
        JOIN fields fl ON fl.id = f.field_id
        WHERE fl.name = 'Natural resources'
        ORDER BY c.name
    """)

    db.execute("""
        CREATE VIEW IF NOT EXISTS countries_by_region AS
        SELECT
            region,
            COUNT(*) AS country_count,
            GROUP_CONCAT(name, ', ') AS countries
        FROM countries
        WHERE region != ''
        GROUP BY region
        ORDER BY country_count DESC
    """)

    db.execute("""
        CREATE VIEW IF NOT EXISTS field_coverage AS
        SELECT
            fl.name AS field_name,
            f.section,
            COUNT(DISTINCT f.country_code) AS countries_with_data,
            f.field_id
        FROM facts f
        JOIN fields fl ON fl.id = f.field_id
        GROUP BY fl.name, f.section
        ORDER BY countries_with_data DESC
    """)


def main():
    parser = argparse.ArgumentParser(description="Extract CIA World Factbook 2020 to SQLite")
    parser.add_argument("--output", "-o", default="factbook.db", help="Output database path")
    parser.add_argument("--data-dir", "-d", default=".", help="Path to factbook data directory")
    args = parser.parse_args()

    db = create_database(args.output, args.data_dir)

    countries_count = db.execute("SELECT COUNT(*) FROM countries").fetchone()[0]
    fields_count = db.execute("SELECT COUNT(*) FROM fields").fetchone()[0]
    facts_count = db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    details_count = db.execute("SELECT COUNT(*) FROM field_details").fetchone()[0]

    print(f"Created {args.output}:")
    print(f"  {countries_count} countries")
    print(f"  {fields_count} fields")
    print(f"  {facts_count} facts")
    print(f"  {details_count} field details")


if __name__ == "__main__":
    main()
