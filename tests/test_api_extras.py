"""set_children, logger, node flags, and lifetimes around them."""

import gc
import threading
import weakref

import pytest

from yogalayout import Node, NodeType


def test_set_children_replaces_and_detaches_old_ones():
    root = Node()
    a, b, c = Node(), Node(), Node()
    root.set_children([a, b])
    assert root.children == (a, b) and a.parent is root
    root.set_children([c, a])  # b leaves, a stays (moved), c joins
    assert root.children == (c, a)
    assert b.parent is None and c.parent is root and a.parent is root
    assert b._tree_id() != root._tree_id() and a._tree_id() == root._tree_id()
    root.set_children([])
    assert len(root) == 0 and a.parent is None


def test_set_children_is_atomic_when_validation_fails():
    root, a, b = Node(), Node(), Node()
    root.set_children([a])
    other = Node()
    other.insert_child(b)
    for bad in ([b], [a, a], [root], [Node(), root]):
        with pytest.raises(ValueError):
            root.set_children(bad)
        assert root.children == (a,) and a.parent is root  # nothing changed
    for junk in ("not a node", None, 3):
        with pytest.raises(TypeError):
            root.set_children([a, junk])
        with pytest.raises(TypeError):
            root.insert_child(junk)
    assert root.children == (a,)


def test_set_children_rejects_measure_nodes_and_descendants():
    leaf = Node(measure=lambda *_: (1, 1))
    with pytest.raises(ValueError, match="measure"):
        leaf.set_children([Node()])
    leaf.set_children([])  # empty is fine
    root = Node()
    child = Node(children=[root])
    with pytest.raises(ValueError, match="descendants"):
        root.set_children([child])


def test_set_children_accepts_generators_and_node_kwarg():
    kids = [Node(width=i + 1) for i in range(3)]
    root = Node(children=(k for k in kids), flex_direction="row")
    assert root.children == tuple(kids)
    root.calculate_layout()
    assert [k.layout.width for k in kids] == [1, 2, 3]


def test_set_children_in_a_callback_is_refused():
    root = Node()
    leaf = Node(measure=lambda *_: (root.set_children([]), (1, 1))[1])
    root.insert_child(leaf)
    with pytest.raises(RuntimeError, match="cannot modify a tree"):
        root.calculate_layout()


def test_set_children_across_threads_does_not_deadlock():
    nodes = [Node() for _ in range(6)]
    roots = [Node(), Node()]
    barrier = threading.Barrier(2)

    def work(i):
        barrier.wait(30)
        for _ in range(200):
            try:
                roots[i].set_children(nodes[:3] if i == 0 else nodes[3:])
            except ValueError:
                pass
            roots[i].set_children([])

    threads = [threading.Thread(target=work, args=(i,)) for i in range(2)]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    assert not any(t.is_alive() for t in threads)


def test_set_children_does_not_leak_or_resurrect():
    gc.collect()
    refs = []
    root = Node()
    for _ in range(20):
        root.set_children([Node() for _ in range(5)])
        refs += [weakref.ref(k) for k in root.children]
    root.set_children([])
    gc.collect()
    assert all(r() is None for r in refs)


def test_node_flags_round_trip():
    n = Node(node_type="text", always_forms_containing_block=True, is_reference_baseline=True)
    assert n.node_type is NodeType.TEXT
    assert n.always_forms_containing_block and n.is_reference_baseline
    assert not n.has_baseline
    n.set_baseline(lambda w, h: 1.0)
    assert n.has_baseline
    n.node_type = NodeType.DEFAULT
    assert n.node_type is NodeType.DEFAULT
