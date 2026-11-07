"""Native Office conversion retains shape text without changing the uploaded file."""

from hashlib import sha256
from json import loads
from zipfile import ZIP_DEFLATED, ZipFile
import pytest


def workbook_with_shapes(tmp_path, *, drawing=None, target="../drawings/drawing1.xml"):
    from openpyxl import Workbook

    path = tmp_path / "shapes.xlsx"
    book = Workbook()
    book.active.title = "First"
    book.active["A1"] = "FIRST-CELL"
    book.create_sheet("Second")["A1"] = "SECOND-CELL"
    book.save(path)
    with ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    sheet = parts["xl/worksheets/sheet2.xml"].decode()
    parts["xl/worksheets/sheet2.xml"] = sheet.replace(
        "</worksheet>",
        '<drawing xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:id="drawing"/></worksheet>',
    ).encode()
    parts["xl/worksheets/_rels/sheet2.xml.rels"] = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="drawing" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" '
        f'Target="{target}"/></Relationships>'
    ).encode()
    parts["xl/drawings/drawing1.xml"] = (
        drawing
        or b"""<xdr:wsDr
        xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
        xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
      <xdr:twoCellAnchor><xdr:from><xdr:col>2</xdr:col><xdr:row>3</xdr:row></xdr:from>
        <xdr:to><xdr:col>5</xdr:col><xdr:row>7</xdr:row></xdr:to>
        <xdr:grpSp><xdr:sp><xdr:txBody><a:bodyPr/><a:lstStyle/>
          <a:p><a:r><a:t>SHAPE-ONLY </a:t></a:r><a:r><a:t>&amp; &#54620;&#44544;</a:t></a:r><a:br/>
            <a:fld><a:t>FIELD-TEXT</a:t></a:fld></a:p>
          <a:p><a:r><a:t>SECOND-PARAGRAPH</a:t></a:r></a:p>
        </xdr:txBody></xdr:sp></xdr:grpSp>
      </xdr:twoCellAnchor></xdr:wsDr>"""
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    return path


def test_native_converter_preserves_cell_text_and_adds_related_shape_with_sheet_provenance(tmp_path):
    pytest.importorskip("docling")
    from docling.document_converter import DocumentConverter
    from langboard_shared.tasks.docling.DoclingConverter import convert_document

    source = workbook_with_shapes(tmp_path)
    digest = sha256(source.read_bytes()).hexdigest()
    # Prove the gap in the pinned official parser before exercising the integration.
    native = DocumentConverter().convert(source).document.export_to_markdown()
    assert "FIRST-CELL" in native and "SECOND-CELL" in native
    assert "SHAPE-ONLY" not in native
    markdown, structural = tmp_path / "output.md", tmp_path / "output.json"
    convert_document(source, markdown, document_output=structural)
    text = markdown.read_text()
    assert "FIRST-CELL" in text and "SECOND-CELL" in text
    assert text.count("SHAPE-ONLY") == 1 and "FIELD-TEXT" in text and "SECOND-PARAGRAPH" in text
    shapes = [item for item in loads(structural.read_text())["texts"] if "SHAPE-ONLY" in item["text"]]
    assert len(shapes) == 1
    assert shapes[0]["text"] == "SHAPE-ONLY & 한글\nFIELD-TEXT\nSECOND-PARAGRAPH"
    assert shapes[0]["prov"][0]["page_no"] == 2
    assert shapes[0]["prov"][0]["bbox"] == {"l": 2.0, "t": 3.0, "r": 6.0, "b": 8.0, "coord_origin": "TOPLEFT"}
    assert sha256(source.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_rejects_dtd_including_utf16_without_expanding_entities(tmp_path, encoding):
    from docling_core.types.doc import DoclingDocument, GroupLabel, Size
    from langboard_shared.tasks.docling.XlsxDrawingText import append_xlsx_drawing_text

    source = workbook_with_shapes(
        tmp_path,
        drawing=(
            f'<?xml version="1.0" encoding="{encoding}"?><!DOCTYPE wsDr [<!ENTITY text "secret">]><wsDr>&text;</wsDr>'
        ).encode(encoding),
    )
    doc = DoclingDocument(name="probe")
    for page, name in enumerate(("First", "Second"), 1):
        doc.add_page(page_no=page, size=Size(width=0, height=0))
        doc.add_group(label=GroupLabel.SHEET, name=name)
    with pytest.raises(ValueError, match="must not declare entities"):
        append_xlsx_drawing_text(doc, source)


def test_rejects_relationship_escape_and_bounded_xml(tmp_path, monkeypatch):
    from docling_core.types.doc import DoclingDocument, GroupLabel, Size
    from langboard_shared.tasks.docling import XlsxDrawingText as module

    doc = DoclingDocument(name="probe")
    for page, name in enumerate(("First", "Second"), 1):
        doc.add_page(page_no=page, size=Size(width=0, height=0))
        doc.add_group(label=GroupLabel.SHEET, name=name)
    source = workbook_with_shapes(tmp_path, target="../../outside.xml")
    with pytest.raises(ValueError, match="leaves the workbook"):
        module.append_xlsx_drawing_text(doc, source)
    source = workbook_with_shapes(tmp_path)
    monkeypatch.setattr(module, "MAX_XML_BYTES", 10)
    with pytest.raises(ValueError, match="exceeds processing limit"):
        module.append_xlsx_drawing_text(doc, source)
