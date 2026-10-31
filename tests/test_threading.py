"""Per-tree locking: independent trees run in parallel, one tree is serialised, nothing deadlocks."""

import gc
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor

import pytest

from yogalayout import Config, Edge, Node

TIMEOUT = 60


def build(n_children=8):
    cfg = Config(point_scale_factor=1)
    root = Node(cfg, width=400, height=300, flex_direction="row", gap=3)
    for i in range(n_children):
        root.insert_child(Node(cfg, flex_grow=i + 1, height=20))
    return root


def run(fn, n_threads):
    barrier = threading.Barrier(n_threads)

    def wrapped(i):
        barrier.wait(TIMEOUT)
        return fn(i)

    with ThreadPoolExecutor(n_threads) as pool:
        futures = [pool.submit(wrapped, i) for i in range(n_threads)]
        return [f.result(TIMEOUT) for f in futures]


def test_tree_nodes_share_one_lock_and_reparenting_moves_it():
    a, b = Node(), Node()
    c = Node()
    assert len({a._tree_id(), b._tree_id()}) == 2
    a.insert_child(c)
    assert a._tree_id() == c._tree_id()
    b.insert_child(a)  # whole subtree moves
    assert {a._tree_id(), b._tree_id(), c._tree_id()} == {b._tree_id()}
    b.remove_child(a)
    assert a._tree_id() == c._tree_id() != b._tree_id()
    a.remove_all_children()
    assert a._tree_id() != c._tree_id()


def test_independent_trees_in_parallel_match_serial():
    expected = []
    root = build()
    root.calculate_layout()
    expected = [tuple(c.layout) for c in root.children]

    def work(i):
        r = build()
        for _ in range(200):
            r.calculate_layout()
        return [tuple(c.layout) for c in r.children]

    assert all(res == expected for res in run(work, 8))


def test_one_shared_tree_under_concurrent_use_stays_consistent():
    root = build(4)
    stop = time.monotonic() + 1.0

    def work(i):
        while time.monotonic() < stop:
            if i % 3 == 0:
                child = Node(width=5, height=5)
                root.insert_child(child)
                root.calculate_layout()
                root.remove_child(child)
            elif i % 3 == 1:
                root.calculate_layout(300 + i, None)
                _ = [tuple(c.layout) for c in root.children]
            else:
                root.width = 200 + i
                root.set_padding(Edge.ALL, i)
                assert 4 <= len(root) <= 4 + 2  # two threads insert one extra child at a time
        return True

    assert all(run(work, 6))
    assert len(root) == 4
    assert all(c.parent is root for c in root.children)


def test_opposite_inserts_do_not_deadlock():
    for _ in range(50):
        a, b = Node(), Node()

        def work(i, a=a, b=b):
            try:
                (a if i == 0 else b).insert_child(b if i == 0 else a)
                return "ok"
            except ValueError:
                return "refused"  # cycle: exactly one of the two wins

        results = run(work, 2)
        assert sorted(results) == ["ok", "refused"]


def test_callback_error_goes_to_the_thread_that_triggered_it():
    inside = threading.Barrier(2)

    def failing(i):
        r = Node()

        def measure(*_):
            inside.wait(TIMEOUT)  # both layouts are inside a callback at the same time
            if i == 0:
                raise KeyError("thread 0")
            return (1, 1)

        r.insert_child(Node(measure=measure))
        try:
            r.calculate_layout()
        except KeyError as e:
            return ("KeyError", str(e))
        return ("ok", None)

    results = run(failing, 2)
    assert results == [("KeyError", "'thread 0'"), ("ok", None)]


def test_blocked_thread_does_not_hold_the_gil_against_a_callback():
    """A waits for the tree lock; B (the holder) needs the GIL inside its measure callback."""
    root = Node()
    started = threading.Event()

    def measure(*_):
        started.set()
        time.sleep(0.2)  # lets the other thread try to take the lock while we hold it
        return (1, 1)

    root.insert_child(Node(measure=measure))

    def work(i):
        if i == 0:
            root.calculate_layout()
        else:
            started.wait(TIMEOUT)
            root.calculate_layout()  # blocks until thread 0 is done
        return True

    assert all(run(work, 2))


def test_dropping_a_parent_while_a_child_is_in_use():
    for _ in range(100):
        root = build(3)
        child = root.children[0]
        ref = weakref.ref(root)
        holder = [root]
        del root

        def work(i, child=child, holder=holder):
            if i == 0:
                holder.clear()  # drops the last reference to the parent
                gc.collect()
            else:
                for _ in range(50):
                    child.width = 3
                    child.calculate_layout()
                    child.parent  # noqa: B018 - races with the parent being destroyed
            return True

        assert all(run(work, 2))
        gc.collect()
        assert ref() is None
        assert child.parent is None


@pytest.mark.parametrize("n", [4])
def test_shared_config_across_threads(n):
    cfg = Config(point_scale_factor=1)

    def work(i):
        r = Node(cfg, width=10 * (i + 1), height=10)
        r.insert_child(Node(cfg, flex_grow=1))
        r.calculate_layout()
        return r.children[0].layout.width

    assert run(work, n) == [10.0, 20.0, 30.0, 40.0]
