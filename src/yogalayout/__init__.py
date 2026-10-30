"""RAII Python bindings for the Yoga flexbox layout engine."""

from collections.abc import Callable, Iterable
from typing import Any

from . import _core
from ._core import (
    UNDEFINED,
    YOGA_VERSION,
    Align,
    BoxSizing,
    Direction,
    Display,
    Edge,
    Errata,
    ExperimentalFeature,
    FlexDirection,
    Gutter,
    Justify,
    Layout,
    MeasureMode,
    Overflow,
    PositionType,
    Unit,
    Value,
    Wrap,
)

_ENUM_PROPS: dict[str, Any] = {
    "direction": Direction,
    "flex_direction": FlexDirection,
    "justify_content": Justify,
    "align_content": Align,
    "align_items": Align,
    "align_self": Align,
    "position_type": PositionType,
    "flex_wrap": Wrap,
    "overflow": Overflow,
    "display": Display,
    "box_sizing": BoxSizing,
}
_ALIASES = {"position": "position_type", "wrap": "flex_wrap"}
_PLAIN = {
    "flex",
    "flex_grow",
    "flex_shrink",
    "aspect_ratio",
    "flex_basis",
    "width",
    "height",
    "min_width",
    "min_height",
    "max_width",
    "max_height",
}
_EDGES = {
    "": Edge.ALL,
    "top": Edge.TOP,
    "bottom": Edge.BOTTOM,
    "left": Edge.LEFT,
    "right": Edge.RIGHT,
    "start": Edge.START,
    "end": Edge.END,
    "horizontal": Edge.HORIZONTAL,
    "vertical": Edge.VERTICAL,
    "inline": Edge.HORIZONTAL,
    "block": Edge.VERTICAL,
}
_GUTTERS = {"gap": Gutter.ALL, "row_gap": Gutter.ROW, "column_gap": Gutter.COLUMN}
_INSETS = {"top", "bottom", "left", "right", "start", "end"}


def _enum(kind: Any, value: Any) -> Any:
    if isinstance(value, kind):
        return value
    try:
        return kind[str(value).upper().replace("-", "_")]
    except KeyError:
        names = ", ".join(m.lower() for m in kind.__members__)
        raise ValueError(f"invalid {kind.__name__} {value!r}; expected one of: {names}") from None


def _shorthand(setter: Callable[[Edge, Any], None], value: Any) -> None:
    """CSS-style box shorthand: v | (vertical, horizontal) | (top, right, bottom, left)."""
    if not isinstance(value, (tuple, list)):
        setter(Edge.ALL, value)
    elif len(value) == 2:
        setter(Edge.VERTICAL, value[0])
        setter(Edge.HORIZONTAL, value[1])
    elif len(value) == 4:
        for edge, v in zip((Edge.TOP, Edge.RIGHT, Edge.BOTTOM, Edge.LEFT), value):
            setter(edge, v)
    else:
        raise ValueError("box shorthand needs 1, 2 or 4 values")


class Config(_core.Config):
    """Yoga configuration; keyword arguments set the matching properties."""

    def __init__(
        self,
        *,
        use_web_defaults: bool | None = None,
        point_scale_factor: float | None = None,
        errata: Errata | None = None,
    ) -> None:
        super().__init__()
        if use_web_defaults is not None:
            self.use_web_defaults = use_web_defaults
        if point_scale_factor is not None:
            self.point_scale_factor = point_scale_factor
        if errata is not None:
            self.errata = errata


class Node(_core.Node):
    """A Yoga node. Keyword arguments are style properties (see :meth:`style`)."""

    def __init__(
        self,
        config: Config | None = None,
        *,
        children: Iterable["Node"] = (),
        measure: Callable[..., tuple[float, float]] | None = None,
        **style: Any,
    ) -> None:
        super().__init__(config)
        self.style(**style)
        for child in children:
            self.insert_child(child)
        if measure is not None:
            self.set_measure(measure)

    def style(self, **kw: Any) -> "Node":
        """Set style properties. Values may be numbers, "N%", "auto", None or enum names.

        Besides the plain properties (width, flex_grow, ...), accepts ``padding``/``margin``/
        ``border`` with CSS shorthands and ``_top``/``_horizontal``/``_inline``... suffixes,
        insets (``top``, ``left``, ...), and ``gap``/``row_gap``/``column_gap``.
        """
        for key, value in kw.items():
            key = _ALIASES.get(key, key)
            if key in _ENUM_PROPS:
                setattr(self, key, _enum(_ENUM_PROPS[key], value))
            elif key in _PLAIN:
                setattr(self, key, value)
            elif key in _GUTTERS:
                self.set_gap(_GUTTERS[key], value)
            elif key in _INSETS:
                self.set_position(_EDGES[key], value)
            else:
                base, _, suffix = key.partition("_")
                setter = {
                    "padding": self.set_padding,
                    "margin": self.set_margin,
                    "border": self.set_border,
                }.get(base)
                if setter is None or (suffix and suffix not in _EDGES):
                    raise TypeError(f"unknown style property {key!r}")
                if suffix:
                    setter(_EDGES[suffix], value)
                else:
                    _shorthand(setter, value)
        return self


__all__ = [
    "UNDEFINED",
    "YOGA_VERSION",
    "Align",
    "BoxSizing",
    "Config",
    "Direction",
    "Display",
    "Edge",
    "Errata",
    "ExperimentalFeature",
    "FlexDirection",
    "Gutter",
    "Justify",
    "Layout",
    "MeasureMode",
    "Node",
    "Overflow",
    "PositionType",
    "Unit",
    "Value",
    "Wrap",
]
