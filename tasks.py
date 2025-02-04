# coding: utf-8

import codecs
import csv
import importlib
import os
import re
from datetime import date, timedelta
from inspect import getsource
from io import StringIO
from urllib.parse import urlsplit

import lxml.html
import requests
from invoke import task
from opencivicdata.divisions import Division
from unidecode import unidecode

# Map Standard Geographical Classification codes to the OCD identifiers of provinces and territories.
province_or_territory_abbreviation_memo = {}
# Map OpenCivicData Division Identifier to Census type name.
ocdid_to_type_name_map = {}
ocd_division_csv = os.path.join(os.path.abspath(os.path.dirname(__file__)), "country-ca.csv")


def module_names():
    """
    Returns all module names.
    """
    for module_name in os.listdir("."):
        if os.path.isfile(os.path.join(module_name, "__init__.py")):
            yield module_name


def modules_and_module_names_and_classes():
    """
    Returns modules, module names, and person scraper classes.
    """
    for module_name in module_names():
        module = importlib.import_module("{}.people".format(module_name))
        class_name = next(key for key in module.__dict__.keys() if "PersonScraper" in key)
        yield (module, module_name, module.__dict__[class_name])


def csv_dict_reader(url, encoding="utf-8"):
    """
    Reads a remote CSV file.
    """
    response = requests.get(url)
    response.encoding = encoding
    return csv.DictReader(StringIO(response.text))


def slug(name):
    """
    Slugifies a division name.
    """
    return unidecode(
        str(name)
        .lower()
        .translate(
            {
                ord(" "): "_",
                ord("'"): "_",
                ord("-"): "_",  # dash
                ord("—"): "_",  # m-dash
                ord("–"): "_",  # n-dash
                ord("."): None,
            }
        )
    )


def province_or_territory_abbreviation(code):
    if not province_or_territory_abbreviation_memo:
        for division in Division.all("ca", from_csv=ocd_division_csv):
            if division._type in ("province", "territory"):
                province_or_territory_abbreviation_memo[division.attrs["sgc"]] = type_id(division.id)
    return province_or_territory_abbreviation_memo[type_id(code)[:2]]


def type_id(id):
    """
    Returns an OCD identifier's type ID.
    """
    return id.rsplit(":", 1)[1]


def get_definition(division_id, aggregation=False):
    """
    Returns the expected configuration for a given division.
    """
    if not ocdid_to_type_name_map:
        # Map census division type codes to names.
        census_division_type_names = {}
        document = lxml.html.fromstring(
            requests.get("https://www12.statcan.gc.ca/census-recensement/2016/ref/dict/tab/t1_4-eng.cfm").content
        )
        for text in document.xpath("//table//th[@headers]/text()"):
            code, name = text.split(" – ", 1)
            census_division_type_names[code] = name.split(" / ", 1)[0]

        # Map census subdivision type codes to names.
        census_subdivision_type_names = {}
        document = lxml.html.fromstring(
            requests.get("https://www12.statcan.gc.ca/census-recensement/2016/ref/dict/tab/t1_5-eng.cfm").content
        )
        for text in document.xpath("//table//th[@headers]/text()"):
            code, name = text.split(" – ", 1)  # non-breaking space
            census_subdivision_type_names[code] = name.split(" / ", 1)[0]

        # Map OCD identifiers to census types.
        for division in Division.all("ca", from_csv=ocd_division_csv):
            if division._type == "cd":
                ocdid_to_type_name_map[division.id] = census_division_type_names[division.attrs["classification"]]
            elif division._type == "csd":
                ocdid_to_type_name_map[division.id] = census_subdivision_type_names[division.attrs["classification"]]

    division = Division.get(division_id, from_csv=ocd_division_csv)
    ocd_type_id = type_id(division.id)

    expected = {}
    vowels = ("A", "À", "E", "É", "I", "Î", "O", "Ô", "U")

    # Determine the module name, name and classification.
    if division._type == "country":
        expected["module_name"] = "ca"
        expected["name"] = "Parliament of Canada"

    elif division._type in ("province", "territory"):
        pattern = "ca_{}_municipalities" if aggregation else "ca_{}"
        expected["module_name"] = pattern.format(ocd_type_id)
        if aggregation:
            expected["name"] = "{} Municipalities".format(division.name)
        elif ocd_type_id in ("nl", "ns"):
            expected["name"] = "{} House of Assembly".format(division.name)
        elif ocd_type_id == "qc":
            expected["name"] = "Assemblée nationale du Québec"
        else:
            expected["name"] = "Legislative Assembly of {}".format(division.name)

    elif division._type == "cd":
        expected["module_name"] = "ca_{}_{}".format(
            province_or_territory_abbreviation(division.id), slug(division.name)
        )
        name_infix = ocdid_to_type_name_map[division.id]
        if name_infix == "Regional municipality":
            name_infix = "Regional"
        expected["name"] = "{} {} Council".format(division.name, name_infix)

    elif division._type == "csd":
        expected["module_name"] = "ca_{}_{}".format(
            province_or_territory_abbreviation(division.id), slug(division.name)
        )
        if ocd_type_id[:2] == "24":
            if division.name[0] in vowels:
                expected["name"] = "Conseil municipal d'{}".format(division.name)
            else:
                expected["name"] = "Conseil municipal de {}".format(division.name)
        else:
            name_infix = ocdid_to_type_name_map[division.id]
            if name_infix in ("Municipality", "Specialized municipality"):
                name_infix = "Municipal"
            elif name_infix == "District municipality":
                name_infix = "District"
            elif name_infix == "Regional municipality":
                name_infix = "Regional"
            expected["name"] = "{} {} Council".format(division.name, name_infix)

    elif division._type == "arrondissement":
        expected["module_name"] = "ca_{}_{}_{}".format(
            province_or_territory_abbreviation(division.parent.id), slug(division.parent.name), slug(division.name)
        )
        if division.name[0] in vowels:
            expected["name"] = "Conseil d'arrondissement d'{}".format(division.name)
        elif division.name[:3] == "Le ":
            expected["name"] = "Conseil d'arrondissement du {}".format(division.name[3:])
        else:
            expected["name"] = "Conseil d'arrondissement de {}".format(division.name)

    else:
        raise Exception("{}: Unrecognized OCD type {}".format(division.id, division._type))

    # Determine the class name.
    class_name_parts = re.split("[ -]", re.sub("[—–]", "-", re.sub("['.]", "", division.name)))
    expected["class_name"] = unidecode(
        str("".join(word if re.match("[A-Z]", word) else word.capitalize() for word in class_name_parts))
    )
    if aggregation:
        expected["class_name"] += "Municipalities"

    # Determine the url.
    expected["url"] = division.attrs["url"]

    # Determine the division name.
    expected["division_name"] = division.name

    return expected


@task
def council_pages():
    """
    Prints scrapers' council page, or warns if it is missing or unneeded.
    """
    for (module, module_name, klass) in modules_and_module_names_and_classes():
        if klass.__bases__[0].__name__ == "CSVScraper":
            if hasattr(module, "COUNCIL_PAGE"):
                print("{:<60} Delete COUNCIL_PAGE".format(module_name))
        else:
            if hasattr(module, "COUNCIL_PAGE"):
                print("{:<60} {}".format(module_name, module.COUNCIL_PAGE))
            else:
                print("{:<60} Missing COUNCIL_PAGE".format(module_name))


@task
def csv_list():
    """
    Lists scrapers with CSV data.
    """
    for (module, module_name, klass) in modules_and_module_names_and_classes():
        if hasattr(klass, "csv_url"):
            print("{}: {}".format(module_name, klass.csv_url))


@task
def csv_stale():
    """
    Lists scrapers with stale manual CSV data.
    """
    for (module, module_name, klass) in modules_and_module_names_and_classes():
        if hasattr(klass, "updated_at") and klass.updated_at < date.today() - timedelta(days=365):
            print("{}: Created on {} by {}".format(module_name, klass.updated_at, klass.contact_person))


@task
def csv_error():
    """
    Notes corrections that CSV publishers should make.
    """
    for (module, module_name, klass) in modules_and_module_names_and_classes():
        if klass.__bases__[0].__name__ == "CSVScraper":
            if "_candidates" in module_name and hasattr(klass, "updated_at"):
                continue

            keys = klass.__dict__.keys() - {
                # Internal keys.
                "__module__",
                "__doc__",
                # Acceptable configuration.
                "csv_url",
                "filename",
                "locale",
                "many_posts_per_area",
                "unique_roles",
                "district_name_format_string",
                "other_names",
                # Required for manual CSVs.
                "updated_at",
                "contact_person",
            }

            if "encoding" in keys and klass.encoding in ("utf-8", "windows-1252"):
                keys -= {"encoding"}

            if keys:
                print("\n{}\n{}".format(module_name, klass.csv_url))

                extra_keys = keys - {"corrections", "encoding", "header_converter"}
                if extra_keys:
                    print("- Manually check the configuration of: {}".format(", ".join(extra_keys)))

                if "encoding" in keys:
                    print(
                        "- The CSV file should be encoded as 'utf-8' or 'windows-1252', not '{}'".format(
                            klass.encoding
                        )
                    )

                if "corrections" in keys:
                    for key, values in klass.corrections.items():
                        for actual, expected in values.items():
                            print("- Change '{}' to '{}' in {}".format(actual, expected, key))

                if "header_converter" in keys:
                    print("- Correct column headers according to:")
                    print(getsource(klass.header_converter))


@task
def tidy():
    """
    Checks that modules are configured correctly.
    """
    # Map OCD identifiers to styles of address.
    leader_styles = {}
    member_styles = {}
    for gid in range(3):
        reader = csv_dict_reader(
            "https://docs.google.com/spreadsheets/d/11qUKd5bHeG5KIzXYERtVgs3hKcd9yuZlt-tCTLBFRpI/pub?single=true&gid={}&output=csv".format(
                gid
            )
        )
        for row in reader:
            key = row["Identifier"]
            leader_styles[key] = row["Leader"]
            member_styles[key] = row["Member"]

    division_ids = set()
    jurisdiction_ids = set()
    for module_name in module_names():
        if module_name.endswith("_candidates") or module_name.endswith("_municipalities"):
            continue

        metadata = module_name_to_metadata(module_name)

        # Ensure division_id is unique.
        division_id = metadata["division_id"]
        if division_id in division_ids:
            print("{:<60} Duplicate division_id {}".format(module_name, division_id))
        else:
            division_ids.add(division_id)

        # Ensure jurisdiction_id is unique.
        jurisdiction_id = metadata["jurisdiction_id"]
        if jurisdiction_id in jurisdiction_ids:
            print("{:<60} Duplicate jurisdiction_id {}".format(module_name, jurisdiction_id))
        else:
            jurisdiction_ids.add(jurisdiction_id)

        expected = get_definition(division_id, bool(module_name.endswith("_municipalities")))

        # Ensure presence of url and styles of address.
        if division_id not in member_styles:
            print("{:<60} Missing member style of address: {}".format(module_name, division_id))
        if division_id not in leader_styles:
            print("{:<60} Missing leader style of address: {}".format(module_name, division_id))
        url = metadata["url"]
        if url and not expected["url"]:
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or parsed.path or parsed.query or parsed.fragment:
                print("{:<60} Check: {}".format(module_name, url))

        # Warn if the name or classification may be incorrect.
        name = metadata["name"]
        if name != expected["name"]:
            print("{:<60} Expected {}".format(name, expected["name"]))
        classification = metadata["classification"]
        if classification != "legislature":
            print("{:<60} Expected legislature".format(classification))

        # Name the classes correctly.
        class_name = metadata["class_name"]
        if class_name != expected["class_name"]:
            # @note This for-loop will only run if the class name in __init__.py is incorrect.
            for basename in os.listdir(module_name):
                if basename.endswith(".py"):
                    with codecs.open(os.path.join(module_name, basename), "r", "utf8") as f:
                        content = f.read()
                    with codecs.open(os.path.join(module_name, basename), "w", "utf8") as f:
                        content = content.replace(class_name + "(", expected["class_name"] + "(")
                        f.write(content)

        # Set the division_name and url appropriately.
        division_name = metadata["division_name"]
        if division_name != expected["division_name"] or (expected["url"] and url != expected["url"]):
            with codecs.open(os.path.join(module_name, "__init__.py"), "r", "utf8") as f:
                content = f.read()
            with codecs.open(os.path.join(module_name, "__init__.py"), "w", "utf8") as f:
                if division_name != expected["division_name"]:
                    content = content.replace("= " + division_name, "= " + expected["division_name"])
                if expected["url"] and url != expected["url"]:
                    content = content.replace(url, expected["url"])
                f.write(content)

        # Name the module correctly.
        if module_name != expected["module_name"]:
            print("{:<60} Expected {}".format(module_name, expected["module_name"]))


@task
def sources_and_assertions():
    """
    Checks that sources are attributed and assertions are made.
    """
    for module_name in module_names():
        path = os.path.join(module_name, "people.py")
        with codecs.open(path, "r", "utf-8") as f:
            content = f.read()

            source_count = content.count("add_source")
            request_count = content.count("lxmlize") + content.count("self.get(") + content.count("requests.get")
            if source_count < request_count:
                print("Expected {} sources after {} requests {}".format(source_count, request_count, path))

            if "CSVScraper" not in content and "assert len(" not in content:
                print("Expected an assertion like: assert len(councillors), 'No councillors found' {}".format(path))


@task
def validate_spreadsheet(url, identifier_header, geographic_name_header):
    """
    Validates the identifiers, geographic names and geographic types in a spreadsheet.
    """
    sgc_to_id = {}

    for division in Division.all("ca", from_csv=ocd_division_csv):
        sgc_to_id[division.attrs["sgc"]] = division.id

    reader = csv_dict_reader(url)
    for row in reader:
        identifier = row[identifier_header]

        if len(identifier) == 2:
            identifier = sgc_to_id[identifier]
        elif len(identifier) == 4:
            identifier = "ocd-division/country:ca/cd:{}".format(identifier)
        elif len(identifier) == 7:
            identifier = "ocd-division/country:ca/csd:{}".format(identifier)

        division = Division.get(identifier)
        if row[geographic_name_header] != division.name:
            print("{}: name: {} not {}".format(identifier, division.name, row[geographic_name_header]))


def module_name_to_metadata(module_name):
    """
    Copied from `reports.utils`.
    """
    module = importlib.import_module(module_name)
    for obj in module.__dict__.values():
        division_id = getattr(obj, "division_id", None)
        if division_id:
            return {
                "class_name": obj.__name__,
                "division_id": division_id,
                "division_name": getattr(obj, "division_name", None),
                "name": getattr(obj, "name", None),
                "url": getattr(obj, "url", None),
                "classification": getattr(obj, "classification", None),
                "jurisdiction_id": "{}/{}".format(
                    division_id.replace("ocd-division", "ocd-jurisdiction"),
                    getattr(obj, "classification", "legislature"),
                ),
            }
