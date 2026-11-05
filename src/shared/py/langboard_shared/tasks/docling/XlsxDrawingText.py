"""Retain worksheet shape text through Docling's public document model."""

from pathlib import Path, PurePosixPath
from posixpath import normpath
from zipfile import ZipFile
from docling_core.types.doc import BoundingBox, CoordOrigin, DocItemLabel, GroupLabel, ProvenanceItem
from lxml import etree as ET


NS = {
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/package/2006/relationships",
    "x": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}
MAX_XML_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024


def append_xlsx_drawing_text(document, source: Path) -> None:
    """Add only related text boxes; never rasterize, modify the XLSX or call a model."""
    groups = [group for group in document.groups if group.label == GroupLabel.SHEET]
    sheets = {group.name: (index + 1, group) for index, group in enumerate(groups)}
    touched = set()
    total = 0
    with ZipFile(source) as archive:
        def read_xml(part):
            nonlocal total
            info = archive.getinfo(part)
            total += info.file_size
            if info.file_size > MAX_XML_BYTES or total > MAX_TOTAL_BYTES:
                raise ValueError("XLSX drawing XML exceeds processing limit")
            value = archive.read(info)
            root = ET.fromstring(value, parser=ET.XMLParser(resolve_entities=False, load_dtd=False, no_network=True))
            if root.getroottree().docinfo.doctype:
                raise ValueError("XLSX drawing XML must not declare entities")
            return root

        def relationships(part):
            path = PurePosixPath(part)
            rels = str(path.parent / "_rels" / (path.name + ".rels"))
            if rels not in archive.namelist():
                return {}
            result = {}
            for rel in read_xml(rels):
                if rel.get("TargetMode") == "External":
                    continue
                target = rel.get("Target", "")
                resolved = normpath(target.lstrip("/") if target.startswith("/") else str(path.parent / target))
                if not resolved.startswith("xl/") or "\\" in resolved:
                    raise ValueError("XLSX relationship leaves the workbook")
                result[rel.get("Id")] = resolved
            return result

        workbook = read_xml("xl/workbook.xml")
        workbook_rels = relationships("xl/workbook.xml")
        for sheet in workbook.findall("s:sheets/s:sheet", NS):
            owner = sheets.get(sheet.get("name"))
            part = workbook_rels.get(sheet.get("{" + NS["r"] + "}id"))
            if owner is None or part is None:
                continue
            page_no, parent = owner
            sheet_rels = relationships(part)
            for drawing in read_xml(part).findall("s:drawing", NS):
                target = sheet_rels.get(drawing.get("{" + NS["r"] + "}id"))
                if target is None:
                    continue
                for anchor in read_xml(target):
                    start = anchor.find("x:from", NS)
                    end = anchor.find("x:to", NS)
                    col = int(start.findtext("x:col", "0", NS)) if start is not None else 0
                    row = int(start.findtext("x:row", "0", NS)) if start is not None else 0
                    right = int(end.findtext("x:col", str(col), NS)) + 1 if end is not None else col + (start is not None)
                    bottom = int(end.findtext("x:row", str(row), NS)) + 1 if end is not None else row + (start is not None)
                    if min(col, row) < 0 or (start is not None and (right <= col or bottom <= row)):
                        raise ValueError("Invalid XLSX drawing anchor")
                    for shape in anchor.iter("{" + NS["x"] + "}sp"):
                        body = shape.find("x:txBody", NS)
                        if body is None:
                            continue
                        paragraphs = []
                        for paragraph in body.findall("a:p", NS):
                            paragraphs.append("".join(
                                "\n" if node.tag == "{" + NS["a"] + "}br" else node.text or ""
                                for node in paragraph.iter()
                                if node.tag in {"{" + NS["a"] + "}t", "{" + NS["a"] + "}br"}
                            ))
                        text = "\n".join(paragraphs)
                        if not text.strip():
                            continue
                        document.add_text(
                            label=DocItemLabel.TEXT, text=text, parent=parent, content_layer=parent.content_layer,
                            prov=ProvenanceItem(
                                page_no=page_no, charspan=(0, len(text)),
                                bbox=BoundingBox(l=col, t=row, r=right, b=bottom, coord_origin=CoordOrigin.TOPLEFT),
                            ),
                        )
                        touched.add(page_no)
                        page = document.pages[page_no]
                        page.size.width = max(page.size.width, right)
                        page.size.height = max(page.size.height, bottom)
    for page_no in touched:
        parent = groups[page_no - 1]
        parent.children.sort(key=lambda ref: next(
            (prov.bbox.t for prov in getattr(ref.resolve(document), "prov", []) if prov.page_no == page_no),
            float("inf"),
        ))
