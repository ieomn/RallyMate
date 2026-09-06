from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "模型训练微调与部署实施方案.md"
OUTPUT = ROOT / "docs" / "RallyMate模型训练微调与部署实施方案.docx"
QA_DIR = ROOT / "docs" / "_qa"
DIAGRAM = QA_DIR / "deployment-architecture.png"
DOCUMENT_TOOL_SCRIPTS = os.environ.get("RALLYMATE_DOCUMENT_TOOL_SCRIPTS")
if not DOCUMENT_TOOL_SCRIPTS:
    raise RuntimeError(
        "Set RALLYMATE_DOCUMENT_TOOL_SCRIPTS to the directory containing "
        "table_geometry.py before building the DOCX artifact."
    )
SKILL_SCRIPTS = Path(DOCUMENT_TOOL_SCRIPTS).expanduser().resolve()
sys.path.insert(0, str(SKILL_SCRIPTS))
from table_geometry import apply_table_geometry, column_widths_from_weights  # noqa: E402


BLUE = "2E74B5"
DARK_BLUE = "1F4D78"
INK = "25364A"
MUTED = "667085"
LIGHT_FILL = "F2F4F7"
CALLOUT_FILL = "EEF5FB"
WHITE = "FFFFFF"
CJK_FONT = "Microsoft YaHei"
LATIN_FONT = "Calibri"
CODE_FONT = "Consolas"


def set_run_font(
    run,
    *,
    name: str = LATIN_FONT,
    east_asia: str = CJK_FONT,
    size: float | None = None,
    color: str | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
) -> None:
    run.font.name = name
    run._element.get_or_add_rPr().get_or_add_rFonts()
    run._element.rPr.rFonts.set(qn("w:ascii"), name)
    run._element.rPr.rFonts.set(qn("w:hAnsi"), name)
    run._element.rPr.rFonts.set(qn("w:eastAsia"), east_asia)
    if size is not None:
        run.font.size = Pt(size)
    if color is not None:
        run.font.color.rgb = RGBColor.from_string(color)
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic


def shade(element, fill: str) -> None:
    properties = element.get_or_add_pPr() if hasattr(element, "get_or_add_pPr") else element
    shading = properties.find(qn("w:shd"))
    if shading is None:
        shading = OxmlElement("w:shd")
        properties.append(shading)
    shading.set(qn("w:fill"), fill)


def set_cell_fill(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shading = tc_pr.find(qn("w:shd"))
    if shading is None:
        shading = OxmlElement("w:shd")
        tc_pr.append(shading)
    shading.set(qn("w:fill"), fill)


def set_repeat_table_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    marker = OxmlElement("w:tblHeader")
    marker.set(qn("w:val"), "true")
    tr_pr.append(marker)


def paragraph_bottom_border(paragraph, color: str = BLUE, size: str = "12") -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    borders = p_pr.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        p_pr.append(borders)
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), size)
    bottom.set(qn("w:space"), "6")
    bottom.set(qn("w:color"), color)
    borders.append(bottom)


def add_page_field(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    prefix = paragraph.add_run("第 ")
    set_run_font(prefix, size=9, color=MUTED)
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    value = OxmlElement("w:t")
    value.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run = paragraph.add_run()
    run._r.extend([begin, instruction, separate, value, end])
    set_run_font(run, size=9, color=MUTED)
    suffix = paragraph.add_run(" 页")
    set_run_font(suffix, size=9, color=MUTED)


def configure_document(document: Document) -> None:
    section = document.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(1)
    section.bottom_margin = Inches(1)
    section.left_margin = Inches(1)
    section.right_margin = Inches(1)
    section.header_distance = Inches(0.492)
    section.footer_distance = Inches(0.492)

    styles = document.styles
    normal = styles["Normal"]
    normal.font.name = LATIN_FONT
    normal._element.rPr.rFonts.set(qn("w:ascii"), LATIN_FONT)
    normal._element.rPr.rFonts.set(qn("w:hAnsi"), LATIN_FONT)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), CJK_FONT)
    normal.font.size = Pt(11)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.10

    title = styles["Title"]
    title.font.name = LATIN_FONT
    title._element.rPr.rFonts.set(qn("w:eastAsia"), CJK_FONT)
    title.font.size = Pt(23)
    title.font.bold = True
    title.font.color.rgb = RGBColor.from_string(INK)
    title.paragraph_format.space_before = Pt(12)
    title.paragraph_format.space_after = Pt(4)

    subtitle = styles["Subtitle"]
    subtitle.font.name = LATIN_FONT
    subtitle._element.rPr.rFonts.set(qn("w:eastAsia"), CJK_FONT)
    subtitle.font.size = Pt(14)
    subtitle.font.color.rgb = RGBColor.from_string(MUTED)
    subtitle.paragraph_format.space_after = Pt(14)

    heading_tokens = {
        "Heading 1": (16, BLUE, 16, 8),
        "Heading 2": (13, BLUE, 12, 6),
        "Heading 3": (12, DARK_BLUE, 8, 4),
    }
    for style_name, (size, color, before, after) in heading_tokens.items():
        style = styles[style_name]
        style.font.name = LATIN_FONT
        style._element.rPr.rFonts.set(qn("w:eastAsia"), CJK_FONT)
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor.from_string(color)
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True

    for style_name in ("List Bullet", "List Number"):
        style = styles[style_name]
        style.font.name = LATIN_FONT
        style._element.rPr.rFonts.set(qn("w:eastAsia"), CJK_FONT)
        style.font.size = Pt(11)
        style.paragraph_format.left_indent = Inches(0.5)
        style.paragraph_format.first_line_indent = Inches(-0.25)
        style.paragraph_format.space_after = Pt(8)
        style.paragraph_format.line_spacing = 1.167

    header = section.header
    header_paragraph = header.paragraphs[0]
    header_paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    left = header_paragraph.add_run("RallyMate 视觉感知")
    set_run_font(left, size=8.5, color=MUTED, bold=True)
    right = header_paragraph.add_run("    模型训练与部署实施方案")
    set_run_font(right, size=8.5, color=MUTED)
    header_paragraph.paragraph_format.space_after = Pt(0)

    footer = section.footer
    add_page_field(footer.paragraphs[0])

    document.core_properties.title = "RallyMate 模型训练、微调与部署实施方案"
    document.core_properties.subject = "第一阶段视觉感知的训练、模型治理与部署"
    document.core_properties.author = "RallyMate 项目组"
    document.core_properties.keywords = "网球, 目标检测, Pose, 模型训练, 部署"


def add_numbering(document: Document, num_format: str, level_text: str) -> int:
    numbering = document.part.numbering_part.element
    abstract_ids = [
        int(node.get(qn("w:abstractNumId")))
        for node in numbering.findall(qn("w:abstractNum"))
    ]
    num_ids = [
        int(node.get(qn("w:numId"))) for node in numbering.findall(qn("w:num"))
    ]
    abstract_id = max(abstract_ids, default=0) + 1
    num_id = max(num_ids, default=0) + 1

    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    multi = OxmlElement("w:multiLevelType")
    multi.set(qn("w:val"), "singleLevel")
    abstract.append(multi)
    level = OxmlElement("w:lvl")
    level.set(qn("w:ilvl"), "0")
    start = OxmlElement("w:start")
    start.set(qn("w:val"), "1")
    fmt = OxmlElement("w:numFmt")
    fmt.set(qn("w:val"), num_format)
    text = OxmlElement("w:lvlText")
    text.set(qn("w:val"), level_text)
    justify = OxmlElement("w:lvlJc")
    justify.set(qn("w:val"), "left")
    p_pr = OxmlElement("w:pPr")
    tabs = OxmlElement("w:tabs")
    tab = OxmlElement("w:tab")
    tab.set(qn("w:val"), "num")
    tab.set(qn("w:pos"), "720")
    tabs.append(tab)
    indent = OxmlElement("w:ind")
    indent.set(qn("w:left"), "720")
    indent.set(qn("w:hanging"), "360")
    spacing = OxmlElement("w:spacing")
    spacing.set(qn("w:after"), "160")
    spacing.set(qn("w:line"), "280")
    spacing.set(qn("w:lineRule"), "auto")
    p_pr.extend([tabs, indent, spacing])
    level.extend([start, fmt, text, justify, p_pr])
    abstract.append(level)
    numbering.append(abstract)

    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(num_id))
    abstract_ref = OxmlElement("w:abstractNumId")
    abstract_ref.set(qn("w:val"), str(abstract_id))
    num.append(abstract_ref)
    numbering.append(num)
    return num_id


def attach_numbering(paragraph, num_id: int) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    num_pr = p_pr.find(qn("w:numPr"))
    if num_pr is None:
        num_pr = OxmlElement("w:numPr")
        p_pr.append(num_pr)
    level = OxmlElement("w:ilvl")
    level.set(qn("w:val"), "0")
    number = OxmlElement("w:numId")
    number.set(qn("w:val"), str(num_id))
    num_pr.extend([level, number])


INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`)")


def add_inline(paragraph, text: str, *, size: float | None = None) -> None:
    position = 0
    for match in INLINE.finditer(text):
        if match.start() > position:
            run = paragraph.add_run(text[position : match.start()])
            set_run_font(run, size=size)
        token = match.group(0)
        if token.startswith("**"):
            run = paragraph.add_run(token[2:-2])
            set_run_font(run, size=size, bold=True, color=INK)
        else:
            run = paragraph.add_run(token[1:-1])
            set_run_font(
                run,
                name=CODE_FONT,
                east_asia=CJK_FONT,
                size=(size or 10.5) - 0.5,
                color=DARK_BLUE,
            )
        position = match.end()
    if position < len(text):
        run = paragraph.add_run(text[position:])
        set_run_font(run, size=size)


def make_architecture_diagram(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGB", (1700, 640), "white")
    draw = ImageDraw.Draw(canvas)
    font_path = r"C:\Windows\Fonts\msyh.ttc"
    bold_path = r"C:\Windows\Fonts\msyhbd.ttc"
    font = ImageFont.truetype(font_path, 27)
    bold = ImageFont.truetype(bold_path, 29)
    small = ImageFont.truetype(font_path, 22)

    boxes = [
        (45, 80, 270, 180, "上传与校验", "API"),
        (330, 80, 555, 180, "持久任务队列", "SQLite WAL"),
        (615, 80, 840, 180, "GPU Worker", "模型常驻"),
        (920, 20, 1190, 120, "目标检测", "人 / 球 / 球拍"),
        (920, 155, 1190, 255, "姿态估计", "人员 ROI / COCO-17"),
        (920, 290, 1190, 390, "固定场地", "一次标定 / 冻结"),
        (1255, 155, 1515, 270, "时空合并", "frame + time + ID"),
        (1255, 350, 1515, 465, "契约校验", "JSONL / summary"),
    ]
    colors = [
        ("EAF2F8", BLUE),
        ("F2F4F7", MUTED),
        ("FFF4D6", "9A6700"),
        ("E9F6EC", "2E7D32"),
        ("E9F6EC", "2E7D32"),
        ("E9F6EC", "2E7D32"),
        ("EAF2F8", BLUE),
        ("F3EAFE", "6941C6"),
    ]
    for box, (fill, outline) in zip(boxes, colors):
        x1, y1, x2, y2, title, subtitle = box
        draw.rounded_rectangle(
            (x1, y1, x2, y2),
            radius=18,
            fill=f"#{fill}",
            outline=f"#{outline}",
            width=3,
        )
        title_box = draw.textbbox((0, 0), title, font=bold)
        subtitle_box = draw.textbbox((0, 0), subtitle, font=small)
        draw.text(
            ((x1 + x2 - (title_box[2] - title_box[0])) / 2, y1 + 18),
            title,
            font=bold,
            fill="#243447",
        )
        draw.text(
            ((x1 + x2 - (subtitle_box[2] - subtitle_box[0])) / 2, y1 + 61),
            subtitle,
            font=small,
            fill="#667085",
        )

    def arrow(start, end):
        draw.line([start, end], fill="#667085", width=4)
        x, y = end
        draw.polygon([(x, y), (x - 14, y - 8), (x - 14, y + 8)], fill="#667085")

    arrow((270, 130), (330, 130))
    arrow((555, 130), (615, 130))
    arrow((840, 130), (920, 70))
    arrow((840, 130), (920, 205))
    arrow((840, 130), (920, 340))
    arrow((1190, 70), (1255, 205))
    arrow((1190, 205), (1255, 205))
    arrow((1190, 340), (1255, 230))
    arrow((1385, 270), (1385, 350))
    draw.text(
        (70, 510),
        "公开契约隔离模型实现：检测、Pose 和场地可独立升级，下一阶段只读取结构化观察结果。",
        font=font,
        fill="#243447",
    )
    canvas.save(path)


def add_title_block(document: Document) -> None:
    title = document.add_paragraph(style="Title")
    title.add_run("RallyMate 模型训练、微调与部署实施方案")
    subtitle = document.add_paragraph(style="Subtitle")
    subtitle.add_run("第一阶段视觉感知：从训练数据到单 GPU 生产交付")
    metadata = [
        ("版本", "1.0"),
        ("日期", "2026-07-31"),
        ("适用范围", "关键对象、身体姿态、固定场地及下游交付契约"),
        ("当前状态", "单机 GPU 试点链路已实现并通过真实视频集成测试"),
    ]
    for label, value in metadata:
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(2)
        paragraph.paragraph_format.line_spacing = 1.0
        label_run = paragraph.add_run(f"{label}：")
        set_run_font(label_run, size=10.5, bold=True, color=INK)
        value_run = paragraph.add_run(value)
        set_run_font(value_run, size=10.5, color=MUTED)
    rule = document.add_paragraph()
    rule.paragraph_format.space_before = Pt(5)
    rule.paragraph_format.space_after = Pt(12)
    paragraph_bottom_border(rule)

    callout = document.add_paragraph()
    shade(callout._p, CALLOUT_FILL)
    callout.paragraph_format.left_indent = Inches(0.18)
    callout.paragraph_format.right_indent = Inches(0.18)
    callout.paragraph_format.space_before = Pt(3)
    callout.paragraph_format.space_after = Pt(10)
    callout.paragraph_format.line_spacing = 1.12
    lead = callout.add_run("实施结论  ")
    set_run_font(lead, size=11, bold=True, color=BLUE)
    rest = callout.add_run(
        "当前代码已覆盖数据防泄漏、检测/Pose 微调、评测门禁、模型登记、"
        "异步 API、持久队列和常驻 GPU Worker；商业上线仍需自有标注数据、"
        "许可证决策、隐私运营和独立精度验收。"
    )
    set_run_font(rest, size=11, color=INK)


def table_weights(rows: list[list[str]]) -> list[float]:
    column_count = len(rows[0])
    lengths = []
    for index in range(column_count):
        max_length = max(len(row[index]) for row in rows)
        lengths.append(min(max(max_length, 6), 30))
    if column_count == 2:
        return [1.5, 5.0]
    if column_count == 3:
        return [1.4, 2.2, 2.9]
    return lengths


def add_table(document: Document, rows: list[list[str]]) -> None:
    if not rows:
        return
    column_count = len(rows[0])
    if any(len(row) != column_count for row in rows):
        return
    table = document.add_table(rows=len(rows), cols=column_count)
    table.style = "Table Grid"
    widths = column_widths_from_weights(table_weights(rows), 9360)
    for row_index, row_values in enumerate(rows):
        for column_index, value in enumerate(row_values):
            cell = table.cell(row_index, column_index)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_before = Pt(1)
            paragraph.paragraph_format.space_after = Pt(1)
            paragraph.paragraph_format.line_spacing = 1.08
            if column_index > 0 and len(value) < 18:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            add_inline(paragraph, value, size=9.2)
            if row_index == 0:
                set_cell_fill(cell, LIGHT_FILL)
                for run in paragraph.runs:
                    run.bold = True
                    run.font.color.rgb = RGBColor.from_string(INK)
    set_repeat_table_header(table.rows[0])
    apply_table_geometry(
        table,
        widths,
        table_width_dxa=9360,
        indent_dxa=120,
        cell_margins_dxa={"top": 80, "bottom": 80, "start": 120, "end": 120},
    )
    spacer = document.add_paragraph()
    spacer.paragraph_format.space_before = Pt(0)
    spacer.paragraph_format.space_after = Pt(2)


def parse_table(lines: list[str], start: int) -> tuple[list[list[str]], int] | None:
    if start + 1 >= len(lines) or "|" not in lines[start]:
        return None
    separator = lines[start + 1].strip()
    if not re.match(r"^\|?[\s:|-]+\|[\s:|-]+\|?$", separator):
        return None
    rows = []
    index = start
    while index < len(lines) and "|" in lines[index] and lines[index].strip():
        if index != start + 1:
            rows.append([cell.strip() for cell in lines[index].strip().strip("|").split("|")])
        index += 1
    return rows, index


def add_code_block(document: Document, code_lines: list[str], language: str) -> None:
    if language == "mermaid":
        make_architecture_diagram(DIAGRAM)
        paragraph = document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = paragraph.add_run()
        inline = run.add_picture(str(DIAGRAM), width=Inches(6.35))
        inline._inline.docPr.set(
            "descr",
            "RallyMate 从视频上传、持久队列、GPU 检测和姿态，到契约校验的部署架构图",
        )
        inline._inline.docPr.set("title", "RallyMate 第一阶段训练与部署链路")
        paragraph.paragraph_format.space_after = Pt(3)
        caption = document.add_paragraph()
        caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
        caption.paragraph_format.space_after = Pt(8)
        caption_run = caption.add_run("图 1  第一阶段训练与部署链路")
        set_run_font(caption_run, size=9, color=MUTED, italic=True)
        return
    for line_index, line in enumerate(code_lines):
        paragraph = document.add_paragraph()
        shade(paragraph._p, "F6F8FA")
        paragraph.paragraph_format.left_indent = Inches(0.18)
        paragraph.paragraph_format.right_indent = Inches(0.10)
        paragraph.paragraph_format.space_before = Pt(3 if line_index == 0 else 0)
        paragraph.paragraph_format.space_after = Pt(
            5 if line_index == len(code_lines) - 1 else 0
        )
        paragraph.paragraph_format.line_spacing = 1.0
        run = paragraph.add_run(line or " ")
        set_run_font(
            run,
            name=CODE_FONT,
            east_asia=CJK_FONT,
            size=8.3,
            color="263238",
        )


def build() -> Path:
    if not SOURCE.exists():
        raise FileNotFoundError(SOURCE)
    document = Document()
    configure_document(document)
    add_title_block(document)
    bullet_num_id = add_numbering(document, "bullet", "•")
    decimal_num_id = add_numbering(document, "decimal", "%1.")

    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    index = 4  # title and metadata are rendered by the masthead
    paragraph_buffer: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph_buffer:
            return
        text = " ".join(part.strip() for part in paragraph_buffer).strip()
        paragraph_buffer.clear()
        if not text:
            return
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.widow_control = True
        add_inline(paragraph, text)

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_paragraph()
            language = stripped[3:].strip()
            code_lines = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            add_code_block(document, code_lines, language)
            index += 1
            continue
        parsed_table = parse_table(lines, index)
        if parsed_table is not None:
            flush_paragraph()
            rows, index = parsed_table
            add_table(document, rows)
            continue
        heading = re.match(r"^(#{2,4})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            level = min(len(heading.group(1)) - 1, 3)
            document.add_paragraph(heading.group(2), style=f"Heading {level}")
            index += 1
            continue
        bullet = re.match(r"^-\s+(.+)$", stripped)
        if bullet:
            flush_paragraph()
            paragraph = document.add_paragraph(style="List Bullet")
            attach_numbering(paragraph, bullet_num_id)
            add_inline(paragraph, bullet.group(1))
            index += 1
            continue
        numbered = re.match(r"^\d+\.\s+(.+)$", stripped)
        if numbered:
            flush_paragraph()
            paragraph = document.add_paragraph(style="List Number")
            attach_numbering(paragraph, decimal_num_id)
            add_inline(paragraph, numbered.group(1))
            index += 1
            continue
        if not stripped:
            flush_paragraph()
        else:
            paragraph_buffer.append(stripped)
        index += 1
    flush_paragraph()

    # Prevent lonely headings and table rows where Word supports the flag.
    for paragraph in document.paragraphs:
        if paragraph.style.name.startswith("Heading"):
            paragraph.paragraph_format.keep_with_next = True
            paragraph.paragraph_format.keep_together = True

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    document.save(OUTPUT)
    return OUTPUT


if __name__ == "__main__":
    print(build())
