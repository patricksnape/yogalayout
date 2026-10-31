"""Callbacks must never corrupt Yoga's state or unwind C++ exceptions through its frames."""

import gc
import math
import weakref

import pytest

from yogalayout import Config, Node


def tree_with_measure(measure):
    root = Node(width=100, height=100)
    leaf = Node(measure=measure)
    sibling = Node(width=5, height=5)
    root.insert_child(leaf)
    root.insert_child(sibling)
    return root, leaf, sibling


def test_measure_cannot_remove_children():
    def measure(*_):
        root.remove_all_children()
        return (1, 1)

    root, leaf, _ = tree_with_measure(measure)
    with pytest.raises(RuntimeError, match="cannot modify a tree"):
        root.calculate_layout()
    assert len(root) == 2 and leaf.parent is root


ACTIONS = {
    "style": lambda root, leaf, sibling: setattr(sibling, "width", 7),
    "padding": lambda root, leaf, sibling: root.set_padding(0, 3),
    "nested_layout": lambda root, leaf, sibling: root.calculate_layout(),
    "insert": lambda root, leaf, sibling: root.insert_child(Node()),
    "unset_measure": lambda root, leaf, sibling: leaf.set_measure(None),
}


@pytest.mark.parametrize("action", ACTIONS.values(), ids=ACTIONS.keys())
def test_measure_cannot_change_style_or_nest_layout(action):
    def measure(*_):
        action(root, leaf, sibling)
        return (1, 1)

    root, leaf, sibling = tree_with_measure(measure)
    with pytest.raises(RuntimeError, match="cannot modify a tree"):
        root.calculate_layout()
    root.remove_all_children()  # the tree is usable again afterwards
    root.calculate_layout()


def test_measure_can_read_the_tree():
    seen = []

    def measure(*_):
        seen.append((root.layout.width, len(root), leaf.parent is root, sibling.width.value))
        return (1, 1)

    root, leaf, sibling = tree_with_measure(measure)
    root.calculate_layout()
    assert seen and all(s[1:] == (2, True, 5.0) for s in seen)


def test_layout_of_another_tree_inside_a_callback():
    other = Node(width=10, height=20)

    def measure(*_):
        other.calculate_layout()
        return (other.layout.width, other.layout.height)

    root = Node()
    leaf = Node(measure=measure)
    root.insert_child(leaf)
    root.calculate_layout()
    assert tuple(leaf.layout)[2:] == (10, 20)


@pytest.mark.parametrize("bad", [math.nan, -1.0])
def test_measure_invalid_size_is_a_value_error(bad):
    root, *_ = tree_with_measure(lambda *_: (bad, 1))
    with pytest.raises(ValueError, match="invalid width"):
        root.calculate_layout()


def test_baseline_nan_is_a_value_error_not_an_abort():
    root = Node(flex_direction="row", align_items="baseline", width=50, height=50)
    child = Node(width=10, height=10)
    child.set_baseline(lambda w, h: math.nan)
    root.insert_child(child)
    root.insert_child(Node(width=10, height=10))
    with pytest.raises(ValueError, match="NaN"):
        root.calculate_layout()


def test_baseline_wrong_type_is_a_type_error():
    root = Node(flex_direction="row", align_items="baseline", width=50, height=50)
    child = Node(width=10, height=10)
    child.set_baseline(lambda w, h: "high")
    root.insert_child(child)
    root.insert_child(Node(width=10, height=10))
    with pytest.raises(TypeError):
        root.calculate_layout()


def test_measure_wrong_shape_and_type():
    root, *_ = tree_with_measure(lambda *_: (1, 2, 3))
    with pytest.raises(ValueError, match="width, height"):
        root.calculate_layout()
    root, *_ = tree_with_measure(lambda *_: "nope")
    with pytest.raises(TypeError):
        root.calculate_layout()


def test_original_exception_type_is_preserved():
    class Boom(Exception):
        pass

    def measure(*_):
        raise Boom("x")

    root, *_ = tree_with_measure(measure)
    with pytest.raises(Boom):
        root.calculate_layout()
    # and the next layout works
    root.remove_all_children()
    root.calculate_layout()


def test_dirtied_error_is_raised_by_the_setter_that_triggered_it():
    node = Node(measure=lambda *_: (1, 1))
    root = Node(width=10, height=10)
    root.insert_child(node)
    root.calculate_layout()

    def dirtied(n):
        raise KeyError("dirtied")

    node.set_dirtied(dirtied)
    with pytest.raises(KeyError):
        node.mark_dirty()
    node.set_dirtied(None)


def test_dirtied_cannot_mutate_the_tree():
    node = Node(measure=lambda *_: (1, 1))
    root = Node(width=10, height=10)
    root.insert_child(node)
    root.calculate_layout()
    node.set_dirtied(lambda n: setattr(n, "width", 3))
    with pytest.raises(RuntimeError, match="cannot modify a tree"):
        node.mark_dirty()
    node.set_dirtied(None)


def test_measure_closure_cycles_are_collected():
    gc.collect()

    def build():
        root = Node(width=10, height=10)
        leaf = Node()
        leaf.set_measure(lambda *_: (1 + 0 * len(leaf), 1))  # closure refers to its own node
        root.insert_child(leaf)
        root.calculate_layout()
        return weakref.ref(root), weakref.ref(leaf)

    refs = [build() for _ in range(20)]
    gc.collect()
    assert all(r() is None and lf() is None for r, lf in refs)


def test_config_is_frozen_once_nodes_use_it():
    cfg = Config(point_scale_factor=1)
    cfg.point_scale_factor = 2  # fine: nobody uses it yet
    Node(cfg)
    with pytest.raises(RuntimeError, match="in use"):
        cfg.point_scale_factor = 1
    with pytest.raises(RuntimeError, match="in use"):
        cfg.use_web_defaults = True
