"""The Python samples in the README, the packaged guide and the skill type-check
the way an external consumer's code does: pyright against the installed package,
with ``reportArgumentType`` on. A sample that passes a bare ``int`` or ``str``
where the public API takes ``ChainId``, ``Amount`` or a role-typed address fails
here instead of teaching readers code their type checker rejects."""

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = [
    REPO_ROOT / "README.md",
    *sorted((REPO_ROOT / "src" / "ipor_fusion" / "guide").glob("*.md")),
    REPO_ROOT / "skills" / "ipor-deploy-vault" / "SKILL.md",
]
_PY_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)
# What a consumer's project gets by default, plus the one rule the SDK's
# nominal types rely on.
_CONSUMER_CONFIG = {
    "typeCheckingMode": "basic",
    "pythonVersion": "3.11",
    "reportArgumentType": "error",
}


def _write_samples(directory: Path) -> dict[str, Path]:
    """One module per document: its Python blocks in order, since a later
    block reuses the names an earlier one defines."""
    modules = {}
    for doc in DOCS:
        blocks = _PY_BLOCK.findall(doc.read_text(encoding="utf-8"))
        if not blocks:
            continue
        name = re.sub(r"\W", "_", str(doc.relative_to(REPO_ROOT)).lower())
        module = directory / f"{name}.py"
        module.write_text("\n".join(blocks), encoding="utf-8")
        modules[str(doc.relative_to(REPO_ROOT))] = module
    return modules


def test_doc_samples_type_check_as_a_consumer(tmp_path):
    modules = _write_samples(tmp_path)
    assert {"README.md", "src/ipor_fusion/guide/quickstart.md"} <= set(modules)
    (tmp_path / "pyrightconfig.json").write_text(json.dumps(_CONSUMER_CONFIG))
    result = subprocess.run(  # noqa: S603 - fixed argv, the test's own interpreter
        [
            sys.executable,
            "-m",
            "pyright",
            "--outputjson",
            "--pythonpath",
            sys.executable,
            "--project",
            str(tmp_path),
            *(str(module) for module in modules.values()),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    report = json.loads(result.stdout)
    errors = [
        f"{Path(d['file']).name}:{d['range']['start']['line'] + 1}: {d['message']}"
        for d in report["generalDiagnostics"]
        if d["severity"] == "error"
    ]
    assert not errors, "\n".join(errors)
    assert report["summary"]["filesAnalyzed"] == len(modules)
