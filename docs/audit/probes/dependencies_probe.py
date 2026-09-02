"""Offline BOOT/CONFIG dependency inventory; never import LAZZARO or providers.

Run from any directory with the Python interpreter under audit. This probe only
reads tracked Python source and distribution metadata, then invokes `pip check`
(an offline metadata check). It does not install packages or open Chroma. The
static boot graph is a conservative import graph, not proof that every branch
executes. Output contains dependency names/versions and source locations only.
"""

from __future__ import annotations

import ast
from collections import defaultdict
import importlib.metadata as metadata
import json
from pathlib import Path
import platform
import re
import subprocess
import sys

from packaging.requirements import Requirement


ROOT = Path(__file__).resolve().parents[3]
BASELINE = ROOT / "docs/baselines/pre-capa2-2026-08-29/evidence/pip-freeze.txt"
IMPORT_DISTRIBUTIONS = {
    "google.genai": "google-genai",
    "pydantic_settings": "pydantic-settings",
    "sklearn": "scikit-learn",
    "serial": "pyserial",
    "pyautogui": "PyAutoGUI",
}


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def tracked_python() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [
        ROOT / name
        for name in result.stdout.decode("utf-8").split("\0")
        if name and not name.startswith("docs/audit/")
    ]


def module_name(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def collect_imports(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    current = module_name(path).split(".")
    package = current if path.name == "__init__.py" else current[:-1]
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = package[: len(package) - node.level + 1] if node.level else []
            if node.module:
                prefix += node.module.split(".")
            parent = ".".join(prefix)
            if parent:
                found.append((parent, node.lineno))
            found.extend(
                (".".join([*prefix, alias.name]), node.lineno)
                for alias in node.names
                if alias.name != "*"
            )
    return found


def main() -> int:
    paths = tracked_python()
    modules = {module_name(path): path for path in paths}
    local_roots = {name.split(".")[0] for name in modules}
    imports = {name: collect_imports(path) for name, path in modules.items()}
    edges = defaultdict(set)
    for source, items in imports.items():
        for target, _line in items:
            parts = target.split(".")
            for count in range(1, len(parts) + 1):
                candidate = ".".join(parts[:count])
                if candidate in modules:
                    edges[source].add(candidate)
    boot_modules = set()
    pending = ["main"]
    while pending:
        node = pending.pop()
        if node not in boot_modules:
            boot_modules.add(node)
            pending.extend(edges[node] - boot_modules)

    baseline = {}
    for line in BASELINE.read_text(encoding="utf-8-sig").splitlines():
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s]+)", line.strip())
        if match:
            baseline[normalize(match[1])] = match[2]

    installed = {
        normalize(dist.metadata["Name"]): dist
        for dist in metadata.distributions()
        if dist.metadata.get("Name")
    }
    declared = []
    for number, raw in enumerate((ROOT / "requirements.txt").read_text(encoding="utf-8-sig").splitlines(), 1):
        raw = raw.split("#", 1)[0].strip()
        if not raw:
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)(.*)", raw)
        if not match:
            raise ValueError(f"Unsupported requirements syntax on line {number}")
        name, constraint = match.groups()
        canonical = normalize(name)
        dist = installed.get(canonical)
        declared.append({
            "name": name,
            "line": number,
            "constraint": constraint,
            "baseline": baseline.get(canonical),
            "installed": dist.version if dist else None,
            "requires_python": dist.metadata.get("Requires-Python") if dist else None,
        })

    distribution_map = metadata.packages_distributions()
    third_party = defaultdict(lambda: {"locations": set(), "boot_locations": set()})
    for source, items in imports.items():
        for target, line in items:
            top = target.split(".")[0]
            if top in sys.stdlib_module_names or top in local_roots or top == "__future__":
                continue
            if (modules[source].parent / f"{top}.py").is_file():
                continue  # Standalone scripts may use a sibling-module fallback.
            key = "google.genai" if target.startswith("google.genai") else top
            # `from google import genai` also creates a parent namespace node;
            # retain only the exact API mapping once its child import is known.
            if key == "google" and any(item.startswith("google.genai") for item, _ in items):
                continue
            location = f"{modules[source].relative_to(ROOT).as_posix()}:{line}"
            third_party[key]["locations"].add(location)
            if source in boot_modules:
                third_party[key]["boot_locations"].add(location)

    declared_names = {normalize(item["name"]) for item in declared}
    # Follow only active installed Requires-Dist metadata. This is not a fresh
    # resolver/install and says nothing about packages a future index may offer.
    active_closure = set()
    evaluated = set()
    dependency_queue = [(name, frozenset()) for name in declared_names]
    while dependency_queue:
        name, extras = dependency_queue.pop()
        if (name, extras) in evaluated:
            continue
        evaluated.add((name, extras))
        active_closure.add(name)
        dist = installed.get(name)
        if dist is None:
            continue
        for raw in dist.requires or []:
            requirement = Requirement(raw)
            if requirement.marker is None or any(
                requirement.marker.evaluate({"extra": extra})
                for extra in extras or {""}
            ):
                dependency_queue.append((normalize(requirement.name), frozenset(requirement.extras)))

    import_inventory = []
    for name, locations in sorted(third_party.items()):
        distributions = (
            [IMPORT_DISTRIBUTIONS[name]] if name in IMPORT_DISTRIBUTIONS
            else distribution_map.get(name, [])
        )
        import_inventory.append({
            "import": name,
            "distributions": distributions,
            "declared": any(normalize(dist) in declared_names for dist in distributions),
            "in_active_dependency_closure": any(normalize(dist) in active_closure for dist in distributions),
            "locations": sorted(locations["locations"]),
            "boot_locations": sorted(locations["boot_locations"]),
        })

    pip_check = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    # Do not print raw subprocess diagnostics: they can include locations or
    # source URLs in a different environment. Exact incompatibilities are
    # inspectable with a separate local `python -m pip check` if this gate fails.
    pip_status = "PASS" if pip_check.returncode == 0 else "FAIL (diagnostics omitted)"
    output = {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "pip": installed["pip"].version if "pip" in installed else None,
        "tracked_python_files": len(paths),
        "static_boot_modules": sorted(boot_modules),
        "declared": declared,
        "imports": import_inventory,
        "baseline_distribution_count": len(baseline),
        "installed_distribution_count": len(installed),
        "active_dependency_closure_count": len(active_closure),
        "active_dependency_closure_missing": sorted(active_closure - set(installed)),
        "baseline_outside_active_dependency_closure_count": len(set(baseline) - active_closure),
        "baseline_missing": sorted(set(baseline) - set(installed)),
        "baseline_version_changes": [
            {"name": name, "baseline": baseline[name], "installed": installed[name].version}
            for name in sorted(set(baseline) & set(installed))
            if baseline[name] != installed[name].version
        ],
        "installed_not_in_freeze": [
            {"name": name, "installed": installed[name].version}
            for name in sorted(set(installed) - set(baseline))
        ],
        "pip_check": {"exit_code": pip_check.returncode, "result": pip_status},
        "limits": [
            "Static graph includes conditional/function imports and does not execute them.",
            "AST cannot prove dynamically imported or pickled-model dependencies.",
            "Installed version equality and pip check do not prove a clean install or API compatibility.",
            "No LAZZARO, provider SDK or Chroma module is imported by this probe.",
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if pip_check.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
