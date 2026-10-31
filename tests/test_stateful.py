"""Random operation sequences against a model of the tree: the Python mirror must always agree
with Yoga, nodes must be freed once unreferenced, and no operation may crash or hang."""

import gc
import weakref

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from yogalayout import Config, Node

MEASURES = ["ok", "raise", "mutate", "nan"]


class TreeMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.cfg = Config(point_scale_factor=1)
        self.nodes: list[Node] = []  # strong references we hold
        self.refs: list[weakref.ref] = []

    def pick(self, data):
        return data.draw(st.sampled_from(self.nodes))

    @rule()
    def new_node(self):
        n = Node(self.cfg, width=10, height=10)
        self.nodes.append(n)
        self.refs.append(weakref.ref(n))

    @precondition(lambda self: len(self.nodes) >= 2)
    @rule(data=st.data())
    def insert(self, data):
        parent, child = self.pick(data), self.pick(data)
        try:
            parent.insert_child(child)
        except ValueError:
            pass  # cycle, already parented, or parent has a measure function

    @precondition(lambda self: self.nodes)
    @rule(data=st.data())
    def remove_one(self, data):
        parent = self.pick(data)
        if len(parent):
            parent.remove_child(parent.child(data.draw(st.integers(0, len(parent) - 1))))

    @precondition(lambda self: self.nodes)
    @rule(data=st.data())
    def remove_all(self, data):
        self.pick(data).remove_all_children()

    @precondition(lambda self: self.nodes)
    @rule(data=st.data())
    def drop(self, data):
        self.nodes.remove(self.pick(data))

    @precondition(lambda self: self.nodes)
    @rule(data=st.data(), kind=st.sampled_from(MEASURES))
    def set_measure(self, data, kind):
        node = self.pick(data)
        others = self.nodes

        def measure(*_):
            if kind == "raise":
                raise KeyError("boom")
            if kind == "mutate":
                others[0].remove_all_children()
            return (float("nan"), 1) if kind == "nan" else (3, 3)

        try:
            node.set_measure(measure)
        except ValueError:
            pass  # has children

    @precondition(lambda self: self.nodes)
    @rule(data=st.data())
    def layout(self, data):
        node = self.pick(data)
        try:
            node.calculate_layout(100, 100)
        except (KeyError, ValueError, RuntimeError):
            pass  # errors from the measure functions above, reported rather than crashing

    @rule()
    def collect(self):
        gc.collect()

    @invariant()
    def mirror_matches_yoga(self):
        for n in self.nodes:
            assert len(n.children) == len(n)
            for c in n.children:
                assert c.parent is n
            if n.parent is not None:
                assert n in n.parent.children

    def teardown(self):
        self.nodes.clear()
        gc.collect()
        assert all(r() is None for r in self.refs), "nodes leaked"


TestTreeMachine = TreeMachine.TestCase
TestTreeMachine.settings = settings(max_examples=50, stateful_step_count=30, deadline=None)
