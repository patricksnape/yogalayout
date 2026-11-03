"""The README's Python snippets must run, in order (they are documentation, so they must not rot)."""

import re
from pathlib import Path

README = Path(__file__).parent.parent / "README.md"


def test_readme_snippets_run():
    blocks = re.findall(r"```python\n(.*?)```", README.read_text(), re.DOTALL)
    assert len(blocks) >= 3
    namespace: dict = {}
    for i, code in enumerate(blocks):
        exec(compile(code, f"README.md (python block {i})", "exec"), namespace)
