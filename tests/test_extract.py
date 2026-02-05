import os
import tempfile

import pytest
from bs4 import BeautifulSoup

from extract import (
    clean_text,
    create_database,
    extract_field_id_from_anchor,
    extract_field_name_from_anchor,
    extract_section_name,
    get_country_files,
    get_full_field_text,
    parse_country_name_and_region,
    parse_country_page,
    parse_field_data,
    parse_numeric,
)

DATA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --- Unit tests for helper functions ---


class TestCleanText:
    def test_basic(self):
        assert clean_text("  hello   world  ") == "hello world"

    def test_newlines(self):
        assert clean_text("hello\n\n  world") == "hello world"

    def test_none(self):
        assert clean_text(None) == ""

    def test_tabs_and_spaces(self):
        assert clean_text("\t  foo \t bar  ") == "foo bar"


class TestParseNumeric:
    def test_integer(self):
        assert parse_numeric("3,074,579") == 3074579.0

    def test_float(self):
        assert parse_numeric("17.6%") == 17.6

    def test_with_units(self):
        assert parse_numeric("28,748 sq km") == 28748.0

    def test_dollar(self):
        assert parse_numeric("$5,000") == 5000.0

    def test_none(self):
        assert parse_numeric(None) is None

    def test_empty(self):
        assert parse_numeric("") is None

    def test_non_numeric(self):
        assert parse_numeric("not a number") is None

    def test_negative(self):
        assert parse_numeric("-2.5%") == -2.5

    def test_with_million(self):
        assert parse_numeric("1.104 million") == 1.104


class TestParseCountryNameAndRegion:
    def test_standard_title(self):
        html = "<html><head><title>Europe :: Albania — The World Factbook</title></head></html>"
        soup = BeautifulSoup(html, "lxml")
        name, region = parse_country_name_and_region(soup)
        assert name == "Albania"
        assert region == "Europe"

    def test_multi_word_region(self):
        html = "<html><head><title>East Asia/Southeast Asia :: China — The World Factbook</title></head></html>"
        soup = BeautifulSoup(html, "lxml")
        name, region = parse_country_name_and_region(soup)
        assert name == "China"
        assert region == "East Asia/Southeast Asia"

    def test_no_region(self):
        html = "<html><head><title>World — The World Factbook</title></head></html>"
        soup = BeautifulSoup(html, "lxml")
        name, region = parse_country_name_and_region(soup)
        assert name == "World"


class TestExtractSectionName:
    def test_section_name(self):
        html = "<div class='question' sectiontitle='Geography'>Geography :: Albania</div>"
        soup = BeautifulSoup(html, "lxml")
        div = soup.find("div", class_="question")
        assert extract_section_name(div) == "Geography"


class TestExtractFieldNameFromAnchor:
    def test_field_name(self):
        html = """
        <div class="category" id="field-anchor-geography-area">
            <span class="btn-tooltip definition">
                <a href="../docs/notesanddefs.html#279">Area</a>:
            </span>
        </div>
        """
        soup = BeautifulSoup(html, "lxml")
        div = soup.find("div", class_="category")
        assert extract_field_name_from_anchor(div) == "Area"


class TestExtractFieldIdFromAnchor:
    def test_field_id(self):
        html = """
        <div class="category" id="field-anchor-geography-area">
            <span class="btn-tooltip definition">
                <a href="../docs/notesanddefs.html#279">Area</a>:
            </span>
            <span class="field-listing-link">
                <a href="../fields/279.html#AL">listing</a>
            </span>
        </div>
        """
        soup = BeautifulSoup(html, "lxml")
        div = soup.find("div", class_="category")
        assert extract_field_id_from_anchor(div) == "279"


class TestParseFieldData:
    def test_numeric_subfields(self):
        html = """
        <div id="field-area">
            <div class='category_data subfield numeric'>
                <span class="subfield-name">total:</span>
                <span class="subfield-number">28,748 sq km</span>
                <span class="subfield-note"></span>
            </div>
            <div class='category_data subfield numeric'>
                <span class="subfield-name">land:</span>
                <span class="subfield-number">27,398 sq km</span>
                <span class="subfield-note"></span>
            </div>
        </div>
        """
        soup = BeautifulSoup(html, "lxml")
        field_div = soup.find("div", id="field-area")
        subfields = parse_field_data(field_div)
        assert len(subfields) == 2
        assert subfields[0]["subfield_name"] == "total"
        assert subfields[0]["value"] == "28,748 sq km"
        assert subfields[0]["numeric_value"] == 28748.0
        assert subfields[1]["subfield_name"] == "land"
        assert subfields[1]["numeric_value"] == 27398.0

    def test_text_field(self):
        html = """
        <div id="field-climate">
            <div class='category_data subfield text'>
                mild temperate; cool, cloudy, wet winters
            </div>
        </div>
        """
        soup = BeautifulSoup(html, "lxml")
        field_div = soup.find("div", id="field-climate")
        subfields = parse_field_data(field_div)
        assert len(subfields) == 1
        assert "mild temperate" in subfields[0]["value"]

    def test_note_field(self):
        html = """
        <div id="field-ethnic-groups">
            <div class='category_data subfield text'>
                Albanian 82.6%, Greek 0.9%
            </div>
            <div class="category_data note">
                <strong>note:</strong> data represent population by ethnic affiliation
            </div>
        </div>
        """
        soup = BeautifulSoup(html, "lxml")
        field_div = soup.find("div", id="field-ethnic-groups")
        subfields = parse_field_data(field_div)
        assert len(subfields) == 2
        assert subfields[1]["subfield_name"] == "note"
        assert "data represent" in subfields[1]["value"]

    def test_grouped_subfields(self):
        html = """
        <div id="field-land-use">
            <div class='category_data subfield grouped_subfield'>
                <span class='subfield-group'>agricultural:</span>
                <span class="subfield-name">arable land:</span>
                <span class="subfield-number">22.6%</span>
                <span class="subfield-date">(2016 est.)</span>
                /
                <span class="subfield-name">permanent crops:</span>
                <span class="subfield-number">3%</span>
                <span class="subfield-date">(2016 est.)</span>
            </div>
        </div>
        """
        soup = BeautifulSoup(html, "lxml")
        field_div = soup.find("div", id="field-land-use")
        subfields = parse_field_data(field_div)
        assert len(subfields) == 2
        assert subfields[0]["subfield_name"] == "agricultural - arable land"
        assert subfields[0]["numeric_value"] == 22.6
        assert subfields[1]["subfield_name"] == "agricultural - permanent crops"
        assert subfields[1]["numeric_value"] == 3.0

    def test_with_date(self):
        html = """
        <div id="field-population">
            <div class='category_data subfield numeric'>
                <span class="subfield-number">3,074,579</span>
                <span class="subfield-note"></span>
                <span class="subfield-date">(July 2020 est.)</span>
            </div>
        </div>
        """
        soup = BeautifulSoup(html, "lxml")
        field_div = soup.find("div", id="field-population")
        subfields = parse_field_data(field_div)
        assert len(subfields) == 1
        assert subfields[0]["numeric_value"] == 3074579.0
        assert subfields[0]["date_info"] == "July 2020 est."

    def test_country_comparison_skipped(self):
        html = """
        <div id="field-area">
            <div class='category_data subfield numeric'>
                <span class="subfield-name">total:</span>
                <span class="subfield-number">28,748 sq km</span>
            </div>
            <div>
                <span class='category'>country comparison to the world:</span>
                <span class='category_data'>
                    <a href="../fields/279rank.html#AL">144</a>
                </span>
            </div>
        </div>
        """
        soup = BeautifulSoup(html, "lxml")
        field_div = soup.find("div", id="field-area")
        subfields = parse_field_data(field_div)
        assert len(subfields) == 1
        assert subfields[0]["subfield_name"] == "total"

    def test_none_input(self):
        assert parse_field_data(None) == []

    def test_note_with_subfield_value(self):
        html = """
        <div id="field-hiv">
            <div class='category_data subfield numeric'>
                <span class="subfield-note">&lt;.1</span>
                <span class="subfield-date">(2019 est.)</span>
            </div>
        </div>
        """
        soup = BeautifulSoup(html, "lxml")
        field_div = soup.find("div", id="field-hiv")
        subfields = parse_field_data(field_div)
        assert len(subfields) == 1
        assert subfields[0]["value"] == "<.1"


# --- Integration tests using actual data files ---


class TestGetCountryFiles:
    def test_returns_files(self):
        files = get_country_files(DATA_DIR)
        assert len(files) > 200
        codes = [code for code, _ in files]
        assert "al" in codes
        assert "us" in codes

    def test_no_print_files(self):
        files = get_country_files(DATA_DIR)
        for code, filepath in files:
            assert "print_" not in os.path.basename(filepath)


class TestParseCountryPage:
    def test_albania(self):
        filepath = os.path.join(DATA_DIR, "geos", "al.html")
        country_info, sections_data = parse_country_page(filepath)
        assert country_info["name"] == "Albania"
        assert country_info["region"] == "Europe"

        sections = {s for s, _, _, _, _ in sections_data}
        assert "Introduction" in sections
        assert "Geography" in sections
        assert "People and Society" in sections
        assert "Economy" in sections

        # Check that specific fields exist
        field_names = {fn for _, fn, _, _, _ in sections_data}
        assert "Background" in field_names
        assert "Location" in field_names
        assert "Area" in field_names
        assert "Population" in field_names
        assert "Climate" in field_names

    def test_albania_area_subfields(self):
        filepath = os.path.join(DATA_DIR, "geos", "al.html")
        _, sections_data = parse_country_page(filepath)

        # Find Area field
        area_data = [
            (fn, sf) for _, fn, _, _, sf in sections_data if fn == "Area"
        ]
        assert len(area_data) == 1
        subfields = area_data[0][1]

        sf_names = [sf["subfield_name"] for sf in subfields]
        assert "total" in sf_names
        assert "land" in sf_names
        assert "water" in sf_names

        total_sf = next(sf for sf in subfields if sf["subfield_name"] == "total")
        assert total_sf["numeric_value"] == 28748.0

    def test_united_states(self):
        filepath = os.path.join(DATA_DIR, "geos", "us.html")
        country_info, sections_data = parse_country_page(filepath)
        assert country_info["name"] == "United States"

        # Check population exists
        pop_data = [sf for _, fn, _, _, sf in sections_data if fn == "Population"]
        assert len(pop_data) == 1
        pop_subfields = pop_data[0]
        assert any(sf["numeric_value"] and sf["numeric_value"] > 300000000 for sf in pop_subfields)


class TestCreateDatabase:
    @pytest.fixture
    def db(self, tmp_path):
        db_path = str(tmp_path / "test_factbook.db")
        return create_database(db_path, DATA_DIR)

    def test_countries_table(self, db):
        count = db.execute("SELECT COUNT(*) FROM countries").fetchone()[0]
        assert count == 268

    def test_countries_have_names(self, db):
        rows = db.execute(
            "SELECT name, region FROM countries WHERE code = 'al'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "Albania"
        assert rows[0][1] == "Europe"

    def test_facts_table_populated(self, db):
        count = db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        assert count > 30000

    def test_field_details_table_populated(self, db):
        count = db.execute("SELECT COUNT(*) FROM field_details").fetchone()[0]
        assert count > 60000

    def test_all_sections_present(self, db):
        sections = [
            row[0]
            for row in db.execute(
                "SELECT DISTINCT section FROM facts ORDER BY section"
            ).fetchall()
        ]
        assert "Introduction" in sections
        assert "Geography" in sections
        assert "People and Society" in sections
        assert "Economy" in sections
        assert "Energy" in sections
        assert "Communications" in sections
        assert "Government" in sections
        assert "Military and Security" in sections
        assert "Transportation" in sections
        assert "Transnational Issues" in sections

    def test_fields_table(self, db):
        count = db.execute("SELECT COUNT(*) FROM fields").fetchone()[0]
        assert count == 175
        # Check a specific field
        row = db.execute(
            "SELECT name FROM fields WHERE id = '279'"
        ).fetchone()
        assert row is not None
        assert row[0] == "Area"

    def test_field_details_numeric_values(self, db):
        rows = db.execute(
            "SELECT fd.numeric_value FROM field_details fd "
            "JOIN fields fl ON fl.id = fd.field_id "
            "WHERE fd.country_code = 'al' AND fl.name = 'Area' AND fd.subfield_name = 'total'"
        ).fetchall()
        assert len(rows) == 1
        assert float(rows[0][0]) == 28748.0

    def test_facts_fts(self, db):
        # Full-text search should work
        rows = db.execute(
            "SELECT f.country_code FROM facts f "
            "WHERE f.rowid IN (SELECT rowid FROM facts_fts WHERE facts_fts MATCH 'petroleum') "
            "LIMIT 5"
        ).fetchall()
        assert len(rows) > 0

    def test_indexes_exist(self, db):
        indexes = [
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_%'"
            ).fetchall()
        ]
        assert "idx_facts_country_code" in indexes
        assert "idx_facts_field_id" in indexes
        assert "idx_field_details_country_code" in indexes

    def test_foreign_keys_valid(self, db):
        # PRAGMA foreign_key_check returns empty list when all FKs are valid
        violations = db.execute("PRAGMA foreign_key_check").fetchall()
        assert violations == []

    def test_facts_foreign_keys_declared(self, db):
        # Verify FK declarations exist on facts table
        fks = db.execute("PRAGMA foreign_key_list(facts)").fetchall()
        fk_tables = {row[2] for row in fks}
        assert "countries" in fk_tables
        assert "fields" in fk_tables

    def test_field_details_foreign_keys_declared(self, db):
        # Verify FK declarations exist on field_details table
        fks = db.execute("PRAGMA foreign_key_list(field_details)").fetchall()
        fk_tables = {row[2] for row in fks}
        assert "countries" in fk_tables
        assert "fields" in fk_tables

    def test_multiple_countries_have_population(self, db):
        count = db.execute(
            "SELECT COUNT(DISTINCT f.country_code) FROM facts f "
            "JOIN fields fl ON fl.id = f.field_id "
            "WHERE fl.name = 'Population'"
        ).fetchone()[0]
        assert count > 200

    def test_background_field_has_text(self, db):
        row = db.execute(
            "SELECT f.value FROM facts f "
            "JOIN fields fl ON fl.id = f.field_id "
            "WHERE f.country_code = 'al' AND fl.name = 'Background'"
        ).fetchone()
        assert row is not None
        assert "Albania" in row[0]
        assert len(row[0]) > 100
