# yogalayout: handover

State as of 2026-10-31. Companion document: `pixflex/docs/HANDOVER.md` (the image-layout library
built on this binding).

## What it is
nanobind binding of Yoga 3.2.1's **public C API** with RAII ownership. MIT. Yoga is a git
submodule (`extern/yoga`, pinned to tag v3.2.1); only `extern/yoga/yoga` is built.

## Layout
| Path | What |
|---|---|
| `src/yogalayout/_core.cpp` | the whole binding (~730 lines): Config, Node, TreeLock, callbacks, enums |
| `src/yogalayout/__init__.py` | Pythonic layer: `Node(config, *, children, measure, **style)`, string enums (spelling-tolerant: `nowrap`/`no-wrap`/`NO_WRAP`), CSS shorthands (`padding=(v, h)`, `margin_inline`, `gap`, insets, `position=`) |
| `src/yogalayout/_core.pyi` | stubs; generated with `python -m nanobind.stubgen`, then **hand-edited** (`parent -> Node \| None`, `_tree_id`). Re-check after regenerating |
| `tests/test_basic.py` | homepage example numbers, lifetimes |
| `tests/test_callbacks.py`, `test_threading.py`, `test_stateful.py`, `test_leaks.py` | callback safety, per-tree locking, Hypothesis state machine, memory growth |
| `tests/conformance/` | **generated** by `tools/gen_conformance.py` from Yoga's own `tests/generated/*.cpp`; regenerate after bumping the submodule (519 tests, 29 upstream skips) |
| `.github/workflows/ci.yml` | matrix tests, free-threaded job, ASan/UBSan job, sdist job. **Never run yet** |

## Design decisions (and why)
- **Lifetimes.** A parent holds strong Python refs to its children (`std::vector<nb::object>`
  mirroring Yoga's list); a child reaches its parent only through a **weakref** (`parent_ref`). No
  reference cycles; `tp_traverse`/`tp_clear` let the GC collect cycles made by user closures.
  Existing Python bindings for Yoga lack keep-alive and silently drop children.
- **Yoga asserts abort the process**, so every precondition is checked first and raised as
  `ValueError`/`IndexError` (parent already set, cycles, measure+children, bad index, ...).
- **Callbacks** never let a C++ exception unwind through Yoga: errors are stored per thread
  (`t_pending`) and rethrown by the call that triggered them, keeping their Python type.
  Measure results are validated (NaN/negative -> `ValueError`); a NaN baseline would abort inside
  Yoga so it is rejected too.
- **Thread safety.** One recursive `TreeLock` per tree, shared by all its nodes; every method takes
  it (`TreeGuard`; blocking releases the GIL; the node's tree pointer is re-checked after locking
  because reparenting can move it). Writers use `Writer`, which marks the tree busy: changing a
  tree from inside one of its own callbacks raises `RuntimeError` (this fixed a real
  use-after-free). Reparenting moves a whole subtree to the new lock; detaching gives each
  subtree a fresh one. `Config` is frozen once a node uses it.
- **Free-threading.** `FREE_THREADED` is passed to nanobind only on Python >= 3.14 (needs
  `PyUnstable` weakref semantics); 3.13t keeps the GIL. abi3 wheels for GIL builds, a separate
  `cp314t` build for free-threaded.
- **Do not look up a Python object by C++ pointer and incref it** (`nb::find` + borrow). On 3.14t
  that resurrected objects whose destructor was waiting for the tree lock (double free, ~1 run in 3).
  Use the weakref (`parentObject()`).

## Yoga facts learned the hard way
- `margin: auto` does **not** centre an over-constrained absolute child (stays at 0,0). Centre
  through the parent's `justify_content`/`align_items`.
- Absolute insets are measured from the parent's border box, ignoring padding.
- Use `point_scale_factor=1` (via `Config`) for whole-pixel layouts.

## Dev setup
```
git clone --recurse-submodules <repo> && cd yogalayout
uv venv --python 3.14 && uv pip install --python .venv scikit-build-core nanobind pytest hypothesis pyright ruff cmake
PATH=$PWD/.venv/bin:$PATH uv pip install --python .venv --no-build-isolation -e .
.venv/bin/python -m pytest -q          # 560 tests, ~20 s
```
- Free-threaded: `uv python install 3.14t; uv venv --python 3.14t .venv-ft`, same install, then
  `pytest` and `pytest --parallel-threads=4 tests/test_basic.py tests/conformance`.
- Sanitizers: build with `CMAKE_ARGS="-DCMAKE_CXX_FLAGS='-fsanitize=address,undefined -fno-omit-frame-pointer' -DCMAKE_BUILD_TYPE=Debug"`
  and run with `LD_PRELOAD="$(gcc -print-file-name=libasan.so):$(gcc -print-file-name=libubsan.so)" ASAN_OPTIONS=detect_leaks=0`.
  **Run threading code in a loop (25x) on 3.14t as well**; one green run proved nothing.
- Gotchas: `nb::python_error` is not move-assignable (hold in `unique_ptr`); `#Name` stringizing
  gives CamelCase (pass explicit lowercase names); exact-size roots skip the measure callback.

## CI status
**Green on all 9 jobs** (ubuntu/macos/windows x 3.10/3.14, free-threaded 3.14t, ASan/UBSan, sdist;
run 41192034218). The first run (41191844050) found three bugs, all fixed: macOS could not
translate `std::runtime_error` (now `nb::builtin_exception`), MSVC has no `ssize_t`
(now `Py_ssize_t`), and the RSS leak test is skipped under ASan (quarantine).

## Next steps (priority order)
1. ~~CI green~~ (done). `pixflex` still has no CI.
2. **First release** (wheels workflow `.github/workflows/wheels.yml` is written and verified: build-only
   run 41521217020 produced 28 wheels + sdist, all tested, `twine check --strict` passed). Remaining,
   needs the user's accounts:
   - pypi.org (and test.pypi.org): add a *pending trusted publisher* for project `yogalayout`: owner
     `patricksnape`, repo `yogalayout`, workflow `wheels.yml`, environment `pypi` (`testpypi`). This
     also reserves the name (`pixflex` likewise, for its own repo).
   - GitHub repo settings: create environments `pypi` and `testpypi` (optionally required reviewers on `pypi`).
   - TestPyPI dry run DONE 2026-11-02 (0.1.0.dev0, 29 files): installs and lays out on 3.14 (abi3 wheel, GIL) and 3.14t (cp314t wheel, GIL stays off). The `testpypi` environment has required reviewers, so approve the deployment in the Actions run. Note `0.1.0.dev0` is now used on TestPyPI; bump before re-uploading.
   - (was) Try TestPyPI first: Actions > Wheels > Run workflow > `publish: testpypi`. Then bump the version
     in `pyproject.toml` (currently `0.1.0.dev0`) and push tag `vX.Y.Z` (must equal the version).
   - No GPG: PyPI dropped PGP; trusted publishing uploads Sigstore attestations (PEP 740) automatically.
3. Optional ThreadSanitizer build on 3.14t (CPython itself is not TSan-instrumented, so expect noise).
4. Missing API surface (not needed by pixflex): `set_children`, config `context`/logging,
   cloning, `YGNodeSetNodeType`/text nodes, per-edge getters for computed layout values beyond
   margin/padding/border.
5. More lifetime tests: reparenting under GC pressure, `__init__.Node(children=...)` error paths.
