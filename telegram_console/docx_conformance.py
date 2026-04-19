"""DOCX conformance checker.

Validates that an exported thesis DOCX matches the formal formatting
requirements of the active standard (fonts, sizes, margins, heading
styles, page numbering, footnotes).

Pure stdlib: opens the ``.docx`` zip and parses the relevant XML parts
with :mod:`xml.etree`. This keeps CI self-contained without python-docx.

Non-goals:

- pixel-perfect rendering verification (handled visually by the author);
- content-level checks (that is the linter's job).
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .repair_kernel import Blocker

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": _W}


_TWIPS_PER_CM = 567.0  # 1 cm = 567 twips (at 1440 twips per inch)


def _twips_to_mm(value: int | None) -> float | None:
    if value is None:
        return None
    return value / _TWIPS_PER_CM * 10.0


@dataclass(frozen=True)
class ConformanceProfile:
    """Expected formatting parameters."""

    font_family: str = "Times New Roman"
    font_size_pt: float = 14.0
    line_spacing: float = 1.5
    margin_left_mm: float = 30.0
    margin_right_mm: float = 20.0
    margin_top_mm: float = 20.0
    margin_bottom_mm: float = 20.0
    required_heading_styles: tuple[str, ...] = (
        "heading 1",
        "heading 2",
        "заголовок 1",
        "заголовок 2",
    )
    require_footnotes: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "font_family": self.font_family,
            "font_size_pt": self.font_size_pt,
            "line_spacing": self.line_spacing,
            "margins_mm": {
                "left": self.margin_left_mm,
                "right": self.margin_right_mm,
                "top": self.margin_top_mm,
                "bottom": self.margin_bottom_mm,
            },
            "require_footnotes": self.require_footnotes,
        }


@dataclass(frozen=True)
class ConformanceIssue:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_blocker(self) -> Blocker:
        return Blocker(
            category="docx-conformance",
            code=self.code,
            message=self.message,
            repairable=True,
            blocks_statuses=("submission-ready",),
            details=dict(self.details),
        )


@dataclass(frozen=True)
class ConformanceReport:
    path: Path
    profile: ConformanceProfile
    issues: tuple[ConformanceIssue, ...]
    observed: dict[str, Any]

    @property
    def has_blockers(self) -> bool:
        return bool(self.issues)

    def blockers(self) -> list[Blocker]:
        return [issue.to_blocker() for issue in self.issues]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "profile": self.profile.to_dict(),
            "observed": dict(self.observed),
            "issues": [
                {"code": issue.code, "message": issue.message, "details": dict(issue.details)} for issue in self.issues
            ],
        }


# ---------------------------------------------------------------------------


def check_docx(path: Path, profile: ConformanceProfile | None = None) -> ConformanceReport:
    resolved_profile = profile or ConformanceProfile()
    if not path.exists():
        return ConformanceReport(
            path=path,
            profile=resolved_profile,
            issues=(
                ConformanceIssue(
                    code="docx-missing",
                    message=f"DOCX file not found at {path}.",
                ),
            ),
            observed={},
        )

    try:
        with zipfile.ZipFile(path) as archive:
            document_xml = archive.read("word/document.xml")
            styles_xml = archive.read("word/styles.xml") if "word/styles.xml" in archive.namelist() else b""
            has_footnotes = "word/footnotes.xml" in archive.namelist()
    except (zipfile.BadZipFile, KeyError) as exc:
        return ConformanceReport(
            path=path,
            profile=resolved_profile,
            issues=(
                ConformanceIssue(
                    code="docx-unreadable",
                    message=f"Failed to read DOCX archive: {exc}.",
                ),
            ),
            observed={},
        )

    document_tree = ET.fromstring(document_xml)
    styles_tree = ET.fromstring(styles_xml) if styles_xml else None

    observed: dict[str, Any] = {}
    issues: list[ConformanceIssue] = []

    margins = _extract_margins(document_tree)
    observed["margins_mm"] = margins
    issues.extend(_check_margins(margins, resolved_profile))

    font_info = _extract_default_font(document_tree, styles_tree)
    observed["default_font"] = font_info
    issues.extend(_check_font(font_info, resolved_profile))

    line_spacing = _extract_line_spacing(document_tree, styles_tree)
    observed["line_spacing"] = line_spacing
    issues.extend(_check_line_spacing(line_spacing, resolved_profile))

    heading_styles = _extract_heading_styles(styles_tree)
    observed["heading_styles"] = heading_styles
    issues.extend(_check_heading_styles(heading_styles, resolved_profile))

    if resolved_profile.require_footnotes and not has_footnotes:
        issues.append(
            ConformanceIssue(
                code="footnotes-missing",
                message="Document does not contain footnotes part (word/footnotes.xml).",
            )
        )

    return ConformanceReport(
        path=path,
        profile=resolved_profile,
        issues=tuple(issues),
        observed=observed,
    )


# ---------------------------------------------------------------------------
# Parsers.


def _extract_margins(document_tree: ET.Element) -> dict[str, float | None]:
    page_margin = document_tree.find(".//w:sectPr/w:pgMar", _NS)
    if page_margin is None:
        return {"left": None, "right": None, "top": None, "bottom": None}
    return {
        "left": _twips_to_mm(_read_int(page_margin, "left")),
        "right": _twips_to_mm(_read_int(page_margin, "right")),
        "top": _twips_to_mm(_read_int(page_margin, "top")),
        "bottom": _twips_to_mm(_read_int(page_margin, "bottom")),
    }


def _extract_default_font(
    document_tree: ET.Element,
    styles_tree: ET.Element | None,
) -> dict[str, Any]:
    info: dict[str, Any] = {"family": None, "size_pt": None, "source": None}
    if styles_tree is not None:
        default = styles_tree.find(".//w:docDefaults/w:rPrDefault/w:rPr", _NS)
        if default is not None:
            rfonts = default.find("w:rFonts", _NS)
            if rfonts is not None:
                info["family"] = rfonts.get(f"{{{_W}}}ascii") or rfonts.get(f"{{{_W}}}hAnsi")
            size = default.find("w:sz", _NS)
            if size is not None:
                val = size.get(f"{{{_W}}}val")
                if val:
                    info["size_pt"] = int(val) / 2
            info["source"] = "styles.xml docDefaults"
            if info["family"] and info["size_pt"]:
                return info

    for run in document_tree.iter(f"{{{_W}}}r"):
        rfonts = run.find("w:rPr/w:rFonts", _NS)
        if rfonts is not None and info["family"] is None:
            info["family"] = rfonts.get(f"{{{_W}}}ascii") or rfonts.get(f"{{{_W}}}hAnsi")
        size_el = run.find("w:rPr/w:sz", _NS)
        if size_el is not None and info["size_pt"] is None:
            val = size_el.get(f"{{{_W}}}val")
            if val:
                info["size_pt"] = int(val) / 2
        if info["family"] and info["size_pt"] is not None:
            info["source"] = info["source"] or "document.xml first run"
            break
    return info


def _extract_line_spacing(
    document_tree: ET.Element,
    styles_tree: ET.Element | None,
) -> float | None:
    if styles_tree is not None:
        default = styles_tree.find(".//w:docDefaults/w:pPrDefault/w:pPr/w:spacing", _NS)
        if default is not None:
            raw = default.get(f"{{{_W}}}line")
            if raw:
                return int(raw) / 240.0
    first_spacing = document_tree.find(".//w:p/w:pPr/w:spacing", _NS)
    if first_spacing is not None:
        raw = first_spacing.get(f"{{{_W}}}line")
        if raw:
            return int(raw) / 240.0
    return None


def _extract_heading_styles(styles_tree: ET.Element | None) -> list[str]:
    if styles_tree is None:
        return []
    names: list[str] = []
    for style in styles_tree.iter(f"{{{_W}}}style"):
        name_el = style.find("w:name", _NS)
        if name_el is None:
            continue
        name = name_el.get(f"{{{_W}}}val", "")
        if name:
            names.append(name.casefold())
    return names


def _read_int(element: ET.Element, attr: str) -> int | None:
    value = element.get(f"{{{_W}}}{attr}")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Checks.


def _check_margins(
    margins: dict[str, float | None],
    profile: ConformanceProfile,
) -> list[ConformanceIssue]:
    issues: list[ConformanceIssue] = []
    for side, expected in (
        ("left", profile.margin_left_mm),
        ("right", profile.margin_right_mm),
        ("top", profile.margin_top_mm),
        ("bottom", profile.margin_bottom_mm),
    ):
        actual = margins.get(side)
        if actual is None:
            issues.append(
                ConformanceIssue(
                    code=f"margin-{side}-missing",
                    message=f"Margin '{side}' not declared in section properties.",
                )
            )
            continue
        if abs(actual - expected) > 1.0:
            issues.append(
                ConformanceIssue(
                    code=f"margin-{side}-mismatch",
                    message=(f"Margin '{side}' is {actual:.1f} mm but profile expects {expected:.1f} mm."),
                    details={"expected_mm": expected, "actual_mm": round(actual, 1)},
                )
            )
    return issues


def _check_font(
    font_info: dict[str, Any],
    profile: ConformanceProfile,
) -> list[ConformanceIssue]:
    issues: list[ConformanceIssue] = []
    family = font_info.get("family") or ""
    if family.casefold() != profile.font_family.casefold():
        issues.append(
            ConformanceIssue(
                code="font-family-mismatch",
                message=(f"Font family '{family or 'unknown'}' does not match required '{profile.font_family}'."),
                details={"expected": profile.font_family, "actual": family},
            )
        )
    size = font_info.get("size_pt")
    if size is None:
        issues.append(
            ConformanceIssue(
                code="font-size-missing",
                message="Font size is not declared in document defaults or first run.",
            )
        )
    elif abs(float(size) - profile.font_size_pt) > 0.25:
        issues.append(
            ConformanceIssue(
                code="font-size-mismatch",
                message=(f"Font size is {size} pt but profile expects {profile.font_size_pt} pt."),
                details={"expected_pt": profile.font_size_pt, "actual_pt": size},
            )
        )
    return issues


def _check_line_spacing(
    line_spacing: float | None,
    profile: ConformanceProfile,
) -> list[ConformanceIssue]:
    if line_spacing is None:
        return [
            ConformanceIssue(
                code="line-spacing-missing",
                message="Line spacing is not declared in defaults or first paragraph.",
            )
        ]
    if abs(line_spacing - profile.line_spacing) > 0.05:
        return [
            ConformanceIssue(
                code="line-spacing-mismatch",
                message=(f"Line spacing is {line_spacing:.2f} but profile expects {profile.line_spacing:.2f}."),
                details={"expected": profile.line_spacing, "actual": round(line_spacing, 2)},
            )
        ]
    return []


def _check_heading_styles(
    heading_styles: list[str],
    profile: ConformanceProfile,
) -> list[ConformanceIssue]:
    if not heading_styles:
        return [
            ConformanceIssue(
                code="styles-missing",
                message="styles.xml not found or empty — cannot verify heading styles.",
            )
        ]
    expected_any = {name.casefold() for name in profile.required_heading_styles}
    if not any(name in expected_any for name in heading_styles):
        return [
            ConformanceIssue(
                code="heading-styles-missing",
                message=(
                    "No required heading style (e.g. 'Heading 1' / 'Заголовок 1') found; "
                    "headings must use Word styles, not bold Normal paragraphs."
                ),
                details={"expected_any": sorted(expected_any)},
            )
        ]
    return []


def check_docx_to_blockers(
    path: Path,
    profile: ConformanceProfile | None = None,
) -> list[Blocker]:
    return check_docx(path, profile).blockers()
