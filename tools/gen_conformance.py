"""Translate Yoga's generated C++ layout tests (extern/yoga/tests/generated) into pytest modules.

The C++ tests are MIT licensed (Meta Platforms, Inc.). Anything the translator does not recognise
is skipped and reported, never guessed. Run: python tools/gen_conformance.py
"""

import re
import sys
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "extern/yoga/tests/generated"
OUT = ROOT / "tests/conformance"

ENUM_PROPS = {  # C function suffix -> (python attribute, enum type)
    "Direction": ("direction", "Direction"),
    "FlexDirection": ("flex_direction", "FlexDirection"),
    "JustifyContent": ("justify_content", "Justify"),
    "AlignContent": ("align_content", "Align"),
    "AlignItems": ("align_items", "Align"),
    "AlignSelf": ("align_self", "Align"),
    "PositionType": ("position_type", "PositionType"),
    "FlexWrap": ("flex_wrap", "Wrap"),
    "Overflow": ("overflow", "Overflow"),
    "Display": ("display", "Display"),
    "BoxSizing": ("box_sizing", "BoxSizing"),
}
FLOAT_PROPS = {
    "Flex": "flex",
    "FlexGrow": "flex_grow",
    "FlexShrink": "flex_shrink",
    "AspectRatio": "aspect_ratio",
}
LENGTH_PROPS = {
    "Width": "width",
    "Height": "height",
    "MinWidth": "min_width",
    "MinHeight": "min_height",
    "MaxWidth": "max_width",
    "MaxHeight": "max_height",
    "FlexBasis": "flex_basis",
}
EDGE_PROPS = {"Padding": "padding", "Margin": "margin", "Position": "position", "Border": "border"}
CONFIG_SETTERS = {"UseWebDefaults": "use_web_defaults", "PointScaleFactor": "point_scale_factor"}
LAYOUT = {"Left": "left", "Top": "top", "Width": "width", "Height": "height"}

NUM = r"-?\d+(?:\.\d+)?(?:e[-+]?\d+)?f?|YGUndefined|NAN"


class Skip(Exception):
    pass


def _raise(msg: str) -> NoReturn:
    raise Skip(msg)


def num(tok: str) -> str:
    tok = tok.strip()
    if tok in ("YGUndefined", "NAN"):
        return "None"
    if not re.fullmatch(NUM, tok):
        _raise(f"number {tok!r}")
    return tok.rstrip("f")


def enum(tok: str, kind: str) -> str:
    m = re.fullmatch("YG" + kind + r"(\w+)", tok.strip())
    if not m:
        _raise(f"enum {tok!r} for {kind}")
    return f"{kind}.{re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', m.group(1)).upper()}"


def pct(val: str) -> str:
    return f'"{val.rstrip("f")}%"'


def line(ln: str) -> str | None:
    ln = ln.strip()
    if not ln or ln.startswith("//") or re.match(r"YG(Node|Config)Free(Recursive)?\(", ln):
        return None
    if m := re.fullmatch(r"YGConfigRef (\w+) = YGConfigNew\(\);", ln):
        return f"{m[1]} = Config()"
    if m := re.fullmatch(r"YGNodeRef (\w+) = YGNodeNewWithConfig\((\w+)\);", ln):
        return f"{m[1]} = Node({m[2]})"
    if m := re.fullmatch(r"YGConfigSet(\w+)\((\w+), (.+)\);", ln):
        if m[1] in CONFIG_SETTERS:
            val = {"true": "True", "false": "False"}.get(m[3]) or num(m[3])
            return f"{m[2]}.{CONFIG_SETTERS[m[1]]} = {val}"
        if m[1] == "Errata":
            return f"{m[2]}.errata = {enum(m[3], 'Errata')}"
        _raise(f"config setter {m[1]}")
    if m := re.fullmatch(r"YGNodeInsertChild\((\w+), (\w+), (\d+)\);", ln):
        return f"{m[1]}.insert_child({m[2]}, {m[3]})"
    if m := re.fullmatch(r"YGNodeCalculateLayout\((\w+), (\S+), (\S+), (\w+)\);", ln):
        return f"{m[1]}.calculate_layout({num(m[2])}, {num(m[3])}, {enum(m[4], 'Direction')})"
    if m := re.fullmatch(rf"YGNodeStyleSet(\w+?)(Percent)?\((\w+), ({NUM})\);", ln):
        name, mode, node, val = m.groups()
        if name in LENGTH_PROPS:
            return f"{node}.{LENGTH_PROPS[name]} = {pct(val) if mode else num(val)}"
        if name in FLOAT_PROPS and not mode:
            return f"{node}.{FLOAT_PROPS[name]} = {num(val)}"
    if m := re.fullmatch(r"YGNodeStyleSet(\w+?)Auto\((\w+)\);", ln):
        if m[1] in LENGTH_PROPS:
            return f'{m[2]}.{LENGTH_PROPS[m[1]]} = "auto"'
    if m := re.fullmatch(r"YGNodeStyleSet(\w+)\((\w+), (YG\w+)\);", ln):
        if m[1] in ENUM_PROPS:
            attr, kind = ENUM_PROPS[m[1]]
            return f"{m[2]}.{attr} = {enum(m[3], kind)}"
    if m := re.fullmatch(
        rf"YGNodeStyleSet(\w+?)(Percent|Auto)?\((\w+), (YGEdge\w+)(?:, ({NUM}))?\);", ln
    ):
        name, mode, node, e, val = m.groups()
        if name in EDGE_PROPS:
            v = '"auto"' if mode == "Auto" else pct(val) if mode == "Percent" else num(val)
            return f"{node}.set_{EDGE_PROPS[name]}({enum(e, 'Edge')}, {v})"
    if m := re.fullmatch(rf"YGNodeStyleSetGap(Percent)?\((\w+), (YGGutter\w+), ({NUM})\);", ln):
        return f"{m[2]}.set_gap({enum(m[3], 'Gutter')}, {pct(m[4]) if m[1] else num(m[4])})"
    if m := re.fullmatch(rf"ASSERT_FLOAT_EQ\(({NUM}), YGNodeLayoutGet(\w+)\((\w+)\)\);", ln):
        if m[2] in LAYOUT:
            return f"assert {m[3]}.layout.{LAYOUT[m[2]]} == pytest.approx({num(m[1])}, abs=1e-4)"
    _raise(ln)


def convert(path: Path) -> tuple[list[str], list[tuple[str, str]]]:
    body, skipped = [], []
    for m in re.finditer(r"TEST\(YogaTest, (\w+)\) \{\n(.*?)\n\}\n", path.read_text(), re.S):
        name, code = m.groups()
        try:
            lines = [t for t in (line(ln) for ln in code.split("\n")) if t]
        except Skip as e:
            skipped.append((name, str(e)))
            continue
        body.append(f"def test_{name}():\n" + "\n".join("    " + t for t in lines) + "\n")
    return body, skipped


HEADER = '''"""Generated by tools/gen_conformance.py from Yoga's {src}; do not edit.

Yoga tests: Copyright (c) Meta Platforms, Inc. and affiliates; MIT license.
"""
# ruff: noqa: F401

import pytest

from yogalayout import (
    Align,
    BoxSizing,
    Config,
    Direction,
    Display,
    Edge,
    Errata,
    FlexDirection,
    Gutter,
    Justify,
    Node,
    Overflow,
    PositionType,
    Wrap,
)


'''


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("test_*.py"):
        old.unlink()
    total = skipped_total = 0
    for path in sorted(SRC.glob("*.cpp")):
        body, skipped = convert(path)
        total += len(body)
        skipped_total += len(skipped)
        for name, why in skipped:
            print(f"skip {path.stem}::{name}: {why}", file=sys.stderr)
        mod = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", re.sub(r"^YG|Test$", "", path.stem)).lower()
        if body:
            (OUT / f"test_{mod}.py").write_text(HEADER.format(src=path.name) + "\n\n".join(body))
    print(f"generated {total} tests, skipped {skipped_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
