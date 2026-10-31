"""Building and dropping trees must release the Yoga nodes too (no C++ memory growth)."""

import gc
import sys

import pytest

from yogalayout import Config, Node

resource = pytest.importorskip("resource")  # not available on Windows


def rss_kib() -> int:
    kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return kib // 1024 if sys.platform == "darwin" else kib


def churn(rounds: int) -> None:
    cfg = Config(point_scale_factor=1)
    for _ in range(rounds):
        root = Node(cfg, width=100, height=100)
        for _ in range(20):
            child = Node(cfg, flex_grow=1)
            root.insert_child(child)
        leaf = Node(cfg)
        leaf.set_measure(lambda *_: (2, 2))  # callback without a cycle
        root.insert_child(leaf)
        root.calculate_layout()


def churn_with_cycles(rounds: int) -> None:
    for _ in range(rounds):
        root = Node(width=10, height=10)
        leaf = Node()
        leaf.set_measure(lambda *_, me=leaf: (1 + 0 * len(me), 1))  # closure refers to its own node
        root.insert_child(leaf)
        root.calculate_layout()


@pytest.mark.parametrize("fn", [churn, churn_with_cycles])
def test_memory_stays_flat(fn):
    fn(2000)  # warm up allocator pools
    gc.collect()
    before = rss_kib()
    fn(20000)
    gc.collect()
    growth_mib = (rss_kib() - before) / 1024
    assert growth_mib < 20, f"RSS grew by {growth_mib:.1f} MiB over 20000 trees"
