"""Read and write the typed XML parameter map used by the CLI."""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
import re
import xml.etree.ElementTree as ET


def _decode_value(element: ET.Element):
    if element.tag in {"object", "settings", "parameter"}:
        result = {}
        for entry in element:
            if entry.tag != "entry" or not entry.get("name"):
                raise ValueError("XML objects may contain only named entry elements")
            name = entry.attrib["name"]
            if name in result:
                raise ValueError(f"Duplicate XML entry: {name}")
            if len(entry) != 1:
                raise ValueError(f"XML entry {name} must contain exactly one typed value")
            result[name] = _decode_value(entry[0])
        return result
    if element.tag == "array":
        result = []
        for item in element:
            if item.tag != "item" or len(item) != 1:
                raise ValueError("XML arrays may contain only item elements with one typed value")
            result.append(_decode_value(item[0]))
        return result
    if element.tag == "string":
        return element.text or ""
    if element.tag == "number":
        text = (element.text or "").strip()
        try:
            return int(text) if re.fullmatch(r"[+-]?\d+", text) else float(text)
        except ValueError as error:
            raise ValueError(f"Invalid XML number: {text}") from error
    if element.tag == "boolean":
        text = (element.text or "").strip().casefold()
        if text not in {"true", "false"}:
            raise ValueError(f"Invalid XML boolean: {text}")
        return text == "true"
    if element.tag == "null":
        if element.text and element.text.strip():
            raise ValueError("XML null elements must be empty")
        return None
    raise ValueError(f"Unsupported XML value element: {element.tag}")


def _encode_value(parent: ET.Element, value) -> ET.Element:
    if value is None:
        return ET.SubElement(parent, "null")
    if isinstance(value, bool):
        element = ET.SubElement(parent, "boolean")
        element.text = "true" if value else "false"
        return element
    if isinstance(value, str):
        element = ET.SubElement(parent, "string")
        element.text = value
        return element
    if isinstance(value, int):
        element = ET.SubElement(parent, "number")
        element.text = str(value)
        return element
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("XML parameter maps cannot contain non-finite numbers")
        element = ET.SubElement(parent, "number")
        element.text = repr(value)
        return element
    if isinstance(value, Mapping):
        element = ET.SubElement(parent, "object")
        for name, item in value.items():
            entry = ET.SubElement(element, "entry", {"name": str(name)})
            _encode_value(entry, item)
        return element
    if isinstance(value, (list, tuple)):
        element = ET.SubElement(parent, "array")
        for item in value:
            item_element = ET.SubElement(element, "item")
            _encode_value(item_element, item)
        return element
    raise TypeError(f"Unsupported XML parameter-map value: {type(value).__name__}")


def write_parameter_map(path: str | Path, mapping: Mapping) -> None:
    """Write a mapping as the project's typed XML configuration format."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element(
        "parameterMap",
        {"formatVersion": "1"},
    )
    _encode_value(root, mapping)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(destination, encoding="utf-8", xml_declaration=True)
    with destination.open("a", encoding="utf-8", newline="") as stream:
        stream.write("\n")


def _decode_phased_map(root: ET.Element) -> dict:
    """Definitions live once, inside their phase or Epic-class group."""
    if [child.tag for child in root] != ["settings", "pre", "during", "post"]:
        raise ValueError("Embedded relevantParameters requires settings, pre, during, and post in order")
    mapping = _decode_value(root[0])
    if {"parameters", "postoperative_measurements", "post_class_failures"} & mapping.keys():
        raise ValueError("Define parameters and measurements in their phase, not in settings")
    mapping["parameters"] = []
    measurement_positions = []
    post_class_failures = []
    phases = {"pre": "Pre-operative", "during": "During-operative", "post": "Post-operative"}
    for section in root[1:]:
        phase = phases[section.tag]
        for group in section:
            if group.tag == "parameter":
                definitions = [group]
            elif group.tag == "class":
                name = group.get("name", "")
                if not re.fullmatch(r"epic\.[^.]+", name):
                    raise ValueError(f"Invalid Epic class: {name}")
                if not len(group) or any(child.tag != "parameter" for child in group):
                    raise ValueError(f"Epic class {name} needs parameter definitions")
                failure = group.get("failure")
                if failure is not None:
                    if section.tag != "post" or failure != "nonempty":
                        raise ValueError("Class failure='nonempty' is supported only in post")
                    if name in post_class_failures:
                        raise ValueError(f"Duplicate post class failure: {name}")
                    post_class_failures.append(name)
                definitions = group
            else:
                raise ValueError(f"Unsupported definition in relevantParameters/{section.tag}: {group.tag}")
            for element in definitions:
                parameter = _decode_value(element)
                if "id" in parameter or "phase" in parameter:
                    raise ValueError("Parameter ID and phase belong to the XML structure, not typed entries")
                parameter_id = element.get("id")
                if not parameter_id:
                    raise ValueError("Parameter IDs must be present and unique")
                if group.tag == "class" and not any(
                    isinstance(field, str) and field.startswith(name + ".") and len(field) > len(name) + 1
                    for field in parameter.get("source_fields", [])
                ):
                    raise ValueError(f"Parameter {parameter_id} has no source field in {name}")
                has_measurement = "measurement" in parameter
                measurement = parameter.pop("measurement", None)
                position = element.get("measurementOrder")
                if has_measurement and (section.tag != "post" or not isinstance(measurement, dict)
                                        or "id" in measurement):
                    raise ValueError(f"Invalid post-operative measurement for {parameter_id}")
                if position is not None and (not has_measurement or not re.fullmatch(r"[1-9]\d*", position)):
                    raise ValueError(f"Invalid measurementOrder for {parameter_id}")
                if has_measurement:
                    measurement = {"id": parameter_id, **measurement}
                    if "source_fields" not in measurement and "derived_from" not in measurement:
                        measurement["source_fields"] = parameter.get("source_fields", [])
                    order = int(position) if position else None
                    if order is not None and any(existing == order for existing, _ in measurement_positions):
                        raise ValueError(f"Duplicate measurementOrder: {order}")
                    measurement_positions.append((order, measurement))
                if section.tag != "post" and "outcome_criteria" in parameter:
                    raise ValueError(f"Outcome criteria belong in post: {parameter_id}")
                if section.tag == "post":
                    parameter.setdefault("outcome_criteria", {"success": None, "failure": None})
                parameter["id"] = parameter_id
                parameter["phase"] = phase
                mapping["parameters"].append(parameter)
    mapping["postoperative_measurements"] = [
        measurement for _, measurement in sorted(measurement_positions,
                                                  key=lambda item: (item[0] is None, item[0] or 0))
    ]
    mapping["post_class_failures"] = post_class_failures
    return mapping


def load_parameter_map_element(root: ET.Element, *, validate_runtime: bool = True) -> dict:
    """Decode the catalog element, whether standalone or embedded in a run XML."""
    if root.tag == "pipelineConfiguration":
        root = root.find("preprocessing/relevantParameters")
        if root is None:
            raise ValueError("Pipeline XML is missing preprocessing/relevantParameters")
    if root.tag not in {"parameterMap", "relevantParameters"} or root.get("formatVersion") != "1":
        raise ValueError("Parameter map must have a parameterMap/relevantParameters element with formatVersion='1'")
    if root.tag == "relevantParameters":
        mapping = _decode_phased_map(root)
    else:
        if len(root) != 1 or root[0].tag != "object":
            raise ValueError("Standalone parameter map must contain exactly one object")
        mapping = _decode_value(root[0])
    if validate_runtime:
        _validate_runtime_map(mapping)
    return mapping


def load_parameter_map(path: str | Path, *, validate_runtime: bool = True) -> dict:
    """Load either a standalone parameter map or the catalog embedded in a run XML."""
    source = Path(path)
    try:
        root = ET.parse(source).getroot()
    except (ET.ParseError, OSError) as error:
        raise ValueError(f"Unable to read XML parameter map {source}: {error}") from error
    return load_parameter_map_element(root, validate_runtime=validate_runtime)


def _validate_runtime_map(mapping: Mapping) -> None:
    phases = ["Pre-operative", "During-operative", "Post-operative"]
    legacy_phases = ["Pre-operative", "Post-operative"]
    included = mapping.get("included_phases")
    catalog_phases = mapping.get("catalog_phases")
    if included not in (phases, legacy_phases) or catalog_phases not in (phases, legacy_phases):
        raise ValueError("Parameter map phases must be ordered Pre-operative, During-operative, Post-operative")
    if included != catalog_phases or set(included) & set(mapping.get("excluded_phases", [])):
        raise ValueError("Included, catalog, and excluded phases must not conflict")
    parameters = mapping.get("parameters")
    if not isinstance(parameters, list):
        raise ValueError("Parameter map is missing its parameters list")
    required = {
        "clinical_parameter", "role", "priority", "exact_field_names",
        "workbook_exact_field_entry", "workbook_row", "extraction_definition",
        "parameter_type", "unit", "value_range",
    }
    seen = set()
    for item in parameters:
        if not isinstance(item, dict) or item.get("phase") not in included:
            raise ValueError("Parameter map contains a parameter outside the supported phases")
        if not required.issubset(item):
            raise ValueError("Parameter map is missing required workbook catalog metadata")
        parameter_id = item.get("id")
        if not parameter_id or parameter_id in seen:
            raise ValueError("Parameter IDs must be present and unique")
        seen.add(parameter_id)


def to_xml(mapping: Mapping) -> str:
    """Serialize a mapping to XML text (used by fixtures and tooling)."""
    root = ET.Element(
        "parameterMap",
        {"formatVersion": "1"},
    )
    _encode_value(root, mapping)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"
