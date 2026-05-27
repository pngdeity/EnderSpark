#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from OCP import IFSelect, Interface, STEPCAFControl, STEPControl, TCollection, TDocStd, TDF, XCAFDoc
from OCP.BRep import BRep_Builder
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepGProp import BRepGProp
from OCP.Bnd import Bnd_Box
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS_Compound

PRESENTATION_TOKENS = [
    "COLOUR_RGB",
    "PRESENTATION_STYLE_ASSIGNMENT",
    "STYLED_ITEM",
    "PRESENTATION_LAYER_ASSIGNMENT",
    "MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION",
]
PMI_TOKENS = [
    "TEXT_LITERAL",
    "TEXT_STYLE",
    "DRAUGHTING",
    "ANNOTATION",
    "DIMENSION",
    "GEOMETRIC_TOLERANCE",
    "DATUM",
]
EXTERNAL_TOKENS = [
    "EXTERNAL",
    "APPLIED_DOCUMENT_REFERENCE",
    "DOCUMENT_FILE",
    "DOCUMENT",
]
ASSEMBLY_TOKENS = [
    "NEXT_ASSEMBLY_USAGE_OCCURRENCE",
    "ASSEMBLY_COMPONENT_USAGE",
    "PRODUCT_DEFINITION_ASSEMBLY",
    "CONFIGURATION_DESIGN",
]
UNIT_TOKENS = [
    "GLOBAL_UNIT_ASSIGNED_CONTEXT",
    "GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT",
    "UNCERTAINTY_MEASURE_WITH_UNIT",
]


def init_step_settings() -> None:
    STEPControl.STEPControl_Controller.Init_s()
    Interface.Interface_Static.SetCVal_s("write.step.schema", "AP242DIS")


def read_header_text(path: Path) -> str:
    header_lines = []
    with path.open("r", errors="ignore") as handle:
        for line in handle:
            header_lines.append(line)
            if line.strip() == "DATA;":
                break
    return "".join(header_lines)


def extract_header_value(header_text: str, keyword: str) -> str | None:
    if keyword.upper() == "FILE_NAME":
        pattern = rf"{keyword}\s*\(\s*'([^']+)'"
    else:
        pattern = rf"{keyword}\s*\(\(\s*'([^']+)'"
    match = re.search(pattern, header_text, flags=re.IGNORECASE)
    return match.group(1) if match else None


def scan_token_counts(text: str, tokens: list[str]) -> dict[str, int]:
    return {token: text.count(token) for token in tokens}


def review_step_file(path: Path) -> dict[str, object]:
    header_text = read_header_text(path)
    full_text = path.read_text(errors="ignore")
    presentation_counts = scan_token_counts(full_text, PRESENTATION_TOKENS)
    pmi_counts = scan_token_counts(full_text, PMI_TOKENS)
    external_counts = scan_token_counts(full_text, EXTERNAL_TOKENS)
    assembly_counts = scan_token_counts(full_text, ASSEMBLY_TOKENS)
    unit_counts = scan_token_counts(full_text, UNIT_TOKENS)
    return {
        "file_description": extract_header_value(header_text, "FILE_DESCRIPTION"),
        "file_schema": extract_header_value(header_text, "FILE_SCHEMA"),
        "file_name": extract_header_value(header_text, "FILE_NAME"),
        "presentation_tokens": presentation_counts,
        "pmi_tokens": pmi_counts,
        "external_tokens": external_counts,
        "assembly_tokens": assembly_counts,
        "unit_tokens": unit_counts,
        "has_presentation": any(presentation_counts.values()),
        "has_pmi": any(pmi_counts.values()),
        "has_external_refs": any(external_counts.values()),
        "has_assembly_usage": any(assembly_counts.values()),
        "has_unit_context": any(unit_counts.values()),
    }


def load_xde_document(path: Path) -> TDocStd.TDocStd_Document:
    reader = STEPCAFControl.STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetLayerMode(True)
    reader.SetNameMode(True)
    reader.SetMatMode(True)
    status = reader.ReadFile(str(path))
    if status != IFSelect.IFSelect_RetDone:
        raise RuntimeError(f"Failed to read STEP file: {path}")
    doc = TDocStd.TDocStd_Document(TCollection.TCollection_ExtendedString("doc"))
    if not reader.Transfer(doc):
        raise RuntimeError(f"Failed to transfer STEP file to XDE: {path}")
    return doc


def write_ap242(path: Path, doc: TDocStd.TDocStd_Document) -> None:
    writer = STEPCAFControl.STEPCAFControl_Writer()
    writer.SetColorMode(True)
    writer.SetLayerMode(True)
    writer.SetNameMode(True)
    writer.SetMaterialMode(True)
    writer.SetPropsMode(True)
    writer.SetSHUOMode(True)
    writer.SetDimTolMode(True)
    if not writer.Transfer(doc):
        raise RuntimeError("Failed to transfer XDE document to STEP writer.")
    status = writer.Write(str(path))
    if status != IFSelect.IFSelect_RetDone:
        raise RuntimeError(f"Failed to write AP242 file: {path}")


def build_compound(doc: TDocStd.TDocStd_Document) -> tuple[TopoDS_Compound, int]:
    shape_tool = XCAFDoc.XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    labels = TDF.TDF_LabelSequence()
    shape_tool.GetFreeShapes(labels)
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for index in range(1, labels.Length() + 1):
        builder.Add(compound, shape_tool.GetShape_s(labels.Value(index)))
    return compound, labels.Length()


def count_subshapes(shape, shape_type) -> int:
    explorer = TopExp_Explorer(shape, shape_type)
    count = 0
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def compute_shape_stats(doc: TDocStd.TDocStd_Document) -> dict[str, object]:
    compound, free_shape_count = build_compound(doc)
    bbox = Bnd_Box()
    BRepBndLib.Add_s(compound, bbox)
    bbox_values = None if bbox.IsVoid() else bbox.Get()
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(compound, props)
    color_tool = XCAFDoc.XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    color_labels = TDF.TDF_LabelSequence()
    color_tool.GetColors(color_labels)
    layer_tool = XCAFDoc.XCAFDoc_DocumentTool.LayerTool_s(doc.Main())
    layer_labels = TDF.TDF_LabelSequence()
    layer_tool.GetLayerLabels(layer_labels)
    return {
        "free_shapes": free_shape_count,
        "solids": count_subshapes(compound, TopAbs_SOLID),
        "faces": count_subshapes(compound, TopAbs_FACE),
        "edges": count_subshapes(compound, TopAbs_EDGE),
        "vertices": count_subshapes(compound, TopAbs_VERTEX),
        "bbox": bbox_values,
        "volume": props.Mass(),
        "colors": color_labels.Length(),
        "layers": layer_labels.Length(),
    }


def diff_stats(source: dict[str, object], converted: dict[str, object]) -> dict[str, object]:
    def delta(a, b):
        if a is None or b is None:
            return None
        return b - a

    def rel_delta(a, b):
        if a in (None, 0) or b is None:
            return None
        return (b - a) / a

    bbox_delta = None
    if source["bbox"] and converted["bbox"]:
        bbox_delta = [b - a for a, b in zip(source["bbox"], converted["bbox"])]

    return {
        "volume_delta": delta(source["volume"], converted["volume"]),
        "volume_rel_delta": rel_delta(source["volume"], converted["volume"]),
        "faces_delta": delta(source["faces"], converted["faces"]),
        "edges_delta": delta(source["edges"], converted["edges"]),
        "vertices_delta": delta(source["vertices"], converted["vertices"]),
        "solids_delta": delta(source["solids"], converted["solids"]),
        "colors_delta": delta(source["colors"], converted["colors"]),
        "layers_delta": delta(source["layers"], converted["layers"]),
        "bbox_delta": bbox_delta,
    }


def build_warnings(source: dict[str, object], converted: dict[str, object], diff: dict[str, object]) -> list[str]:
    warnings = []
    if source["colors"] and not converted["colors"]:
        warnings.append("color_data_missing")
    if source["layers"] and not converted["layers"]:
        warnings.append("layer_data_missing")
    if source["faces"] != converted["faces"]:
        warnings.append("face_count_changed")
    if source["edges"] != converted["edges"]:
        warnings.append("edge_count_changed")
    if source["solids"] != converted["solids"]:
        warnings.append("solid_count_changed")
    if diff["volume_rel_delta"] is not None and abs(diff["volume_rel_delta"]) > 1e-6:
        warnings.append("volume_changed")
    return warnings


def find_step_files(input_dir: Path, output_dir: Path) -> list[Path]:
    files = []
    for path in sorted(input_dir.iterdir()):
        if path.is_dir() and path.resolve() == output_dir.resolve():
            continue
        if path.is_file() and path.suffix.lower() in {".step", ".stp"}:
            files.append(path)
    return files


def convert_and_validate(input_dir: Path, output_dir: Path) -> dict[str, object]:
    init_step_settings()
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": "AP242DIS",
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "files": {},
    }
    for step_path in find_step_files(input_dir, output_dir):
        review = review_step_file(step_path)
        source_doc = load_xde_document(step_path)
        source_stats = compute_shape_stats(source_doc)
        output_path = output_dir / step_path.name
        write_ap242(output_path, source_doc)
        converted_doc = load_xde_document(output_path)
        converted_stats = compute_shape_stats(converted_doc)
        diff = diff_stats(source_stats, converted_stats)
        warnings = build_warnings(source_stats, converted_stats, diff)
        report["files"][step_path.name] = {
            "review": review,
            "source_stats": source_stats,
            "converted_stats": converted_stats,
            "diff": diff,
            "warnings": warnings,
        }
    return report


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[2]
    default_input = repo_root / "Parts" / "Printed parts"
    default_output = default_input / "AP242"
    parser = argparse.ArgumentParser(
        description="Convert STEP AP214 files to AP242 while preserving XDE data.",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=default_input,
        help="Directory containing STEP files to convert.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=default_output,
        help="Directory to write AP242 STEP files and reports.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    if not input_dir.exists():
        print(f"Input directory not found: {input_dir}", file=sys.stderr)
        return 2
    report = convert_and_validate(input_dir, output_dir)
    report_path = output_dir / "ap242_conversion_report.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"Wrote AP242 files to {output_dir}")
    print(f"Wrote report to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
