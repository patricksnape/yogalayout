import gc
import weakref

import pytest

from yogalayout import Config, Edge, Node


def homepage():
    cfg = Config()
    cfg.use_web_defaults = False
    cfg.point_scale_factor = 1
    root = Node(cfg)
    root.width, root.height = 250, 475
    root.set_padding(Edge.ALL, 10)
    col = Node(cfg)
    col.flex = 1
    root.insert_child(col)
    return cfg, root, col


def test_flex_children():
    cfg, root, col = homepage()
    a, b = Node(cfg), Node(cfg)
    a.flex, b.flex = 1, 2
    col.insert_child(a)
    col.insert_child(b)
    root.calculate_layout()
    assert a.layout.height + b.layout.height == 455
    assert (a.layout.top, b.layout.top) == (0, a.layout.height)
    assert b.layout.height == pytest.approx(2 * a.layout.height, abs=1)


def test_lifetime_child_survives_parent():
    _cfg, root, col = homepage()
    ref = weakref.ref(col)
    del root
    gc.collect()
    assert ref() is col and col.parent is None


def test_parent_keeps_children_alive():
    _cfg, root, col = homepage()
    ref = weakref.ref(col)
    del col
    gc.collect()
    child = ref()
    assert child is not None and root.children[0] is child
    assert child.parent is root


def test_reparent_and_cycle_errors():
    cfg, root, col = homepage()
    with pytest.raises(ValueError):
        Node(cfg).insert_child(col)  # already has a parent
    with pytest.raises(ValueError):
        col.insert_child(root)  # ancestor
    with pytest.raises(ValueError):
        col.insert_child(col)
    with pytest.raises(IndexError):
        col.insert_child(Node(cfg), 5)


def test_measure_exception_propagates():
    n = Node()

    def boom(*a):
        raise RuntimeError("boom")

    n.set_measure(boom)
    with pytest.raises(RuntimeError, match="boom"):
        n.calculate_layout()  # exact root sizes skip measure


def test_measure_size():
    n = Node()
    n.set_measure(lambda w, wm, h, hm: (30, 20))
    n.calculate_layout()
    assert (n.layout.width, n.layout.height) == (30, 20)


def test_cycle_via_closure_is_collected():
    n = Node()
    n.set_measure(lambda *a, _n=n: (1, 1))
    ref = weakref.ref(n)
    del n
    gc.collect()
    assert ref() is None


def test_style_kwargs_and_homepage():
    from yogalayout import Align, Justify

    cfg = Config(use_web_defaults=False, point_scale_factor=1)
    icons = [Node(cfg, width=40, height=40) for _ in range(4)]
    header = Node(cfg, height=60)
    a = Node(cfg, flex=1, margin_inline=10)
    b = Node(cfg, flex=2, margin_inline=10)
    bar = Node(
        cfg,
        position="absolute",
        width="100%",
        bottom=0,
        height=64,
        flex_direction="row",
        align_items="center",
        justify_content="space-around",
        children=icons,
    )
    root = Node(
        cfg,
        width=250,
        height=475,
        padding=10,
        children=[Node(cfg, flex=1, row_gap=10, children=[header, a, b, bar])],
    )
    root.calculate_layout()
    assert tuple(a.layout) == (10, 70, 210, 125)
    assert tuple(b.layout) == (10, 205, 210, 250)
    assert tuple(bar.layout) == (0, 391, 230, 64)
    assert [i.layout.left for i in icons] == [9, 66, 124, 181]
    assert bar.align_items == Align.CENTER and bar.justify_content == Justify.SPACE_AROUND


def test_style_errors_and_shorthands():
    n = Node(padding=(1, 2), margin=(1, 2, 3, 4), padding_top=7)
    assert n.get_padding(Edge.TOP).value == 7 and n.get_padding(Edge.HORIZONTAL).value == 2
    assert n.get_margin(Edge.RIGHT).value == 2
    with pytest.raises(TypeError):
        Node(bogus=1)
    with pytest.raises(ValueError, match="column-reverse"):
        Node(flex_direction="diagonal")
    with pytest.raises(ValueError):
        Node(min_width="auto")
    with pytest.raises(ValueError):
        Node(width="12px")


@pytest.mark.parametrize("spelling", ["nowrap", "no-wrap", "no_wrap", "NO_WRAP", "NoWrap"])
def test_enum_names_are_spelling_tolerant(spelling):
    from yogalayout import Wrap

    assert Node(flex_wrap=spelling).flex_wrap == Wrap.NO_WRAP
    assert Node(flex_wrap="wrap-reverse").flex_wrap == Wrap.WRAP_REVERSE
