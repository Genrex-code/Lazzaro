"""Run the existing direct-function regression suite inside safety barriers.

Run in a fresh process: python docs/audit/probes/regression_probe.py
This is an audit harness, not a replacement for the application or its tests.
Only test_* functions without required arguments are called; main() is used
only in modules with no test_* functions, as in the baseline runner.

The working directory is temporary, Python network/subprocess operations are
blocked, and PersistentClient/SQLite cannot open the active repository Chroma.
Windows asyncio may create its internal loopback socketpair; that narrow
socketpair-only exception is not permission to contact a local HTTP endpoint.
Native extensions are not OS-sandboxed by Python audit hooks. Real Chroma tests
use their existing local ONNX cache; no model download is permitted.
"""

from __future__ import annotations

import ast
from collections import Counter
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import gc
import hashlib
import importlib
import inspect
import io
import json
import logging
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
import threading
import time
import traceback


ROOT = Path(__file__).resolve().parents[3]
ACTIVE_CHROMA = ROOT / "lazzaro_data"
ACTIVE_FILES = (
    ACTIVE_CHROMA / "644d5fee-fd11-4187-a03b-37c6b2ade6de/data_level0.bin",
    ACTIVE_CHROMA / "644d5fee-fd11-4187-a03b-37c6b2ade6de/length.bin",
    ACTIVE_CHROMA / "chroma.sqlite3",
)
TEST_ROOTS = ("ai_engine", "core", "memory", "terminal_client")
KNOWN_FAILURE: str | None = None
GUARD_COUNTS: Counter[str] = Counter()
GUARD_EVENTS: list[dict] = []
SOCKETPAIR_SCOPE = threading.local()
CURRENT_UNIT = "harness_setup"
PLATFORM_SOURCE = Path(os.__file__).resolve().parent / "platform.py"
PLATFORM_COMMANDS_SEEN: set[str] = set()


class AuditSafetyBarrier(PermissionError):
    """A test attempted an operation outside the offline audit scope."""


class DiscardText(io.TextIOBase):
    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        pass


@contextmanager
def silent_test_output():
    """Discard Python and native diagnostic streams; never persist raw output."""
    discard = DiscardText()
    descriptors = (os.dup(1), os.dup(2))
    try:
        with open(os.devnull, "wb") as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            with redirect_stdout(discard), redirect_stderr(discard):
                yield
    finally:
        os.dup2(descriptors[0], 1)
        os.dup2(descriptors[1], 2)
        os.close(descriptors[0])
        os.close(descriptors[1])


def protected(path) -> bool:
    if isinstance(path, int) or path is None:
        return False
    try:
        resolved = Path(os.fsdecode(path)).expanduser().resolve()
    except (TypeError, ValueError, OSError):
        return False
    return resolved == ACTIVE_CHROMA or ACTIVE_CHROMA in resolved.parents


def reject_protected(path, operation: str) -> None:
    if protected(path):
        GUARD_COUNTS[operation] += 1
        raise AuditSafetyBarrier("Active Chroma access blocked by audit harness")


def verified_platform_introspection() -> bool:
    """Recognize only the three already inspected Windows stdlib probes.

    Recognition affects reporting, never execution: each process stays blocked.
    A different caller, command or repetition remains an unexplained attempt.
    """
    if CURRENT_UNIT != "ai_engine.test_llm_fallbacks.__import__":
        return False
    frame = sys._getframe(1)
    while frame is not None:
        if (
            frame.f_code.co_name == "_syscmd_ver"
            and Path(frame.f_code.co_filename).resolve() == PLATFORM_SOURCE
        ):
            command = frame.f_locals.get("cmd")
            if command in {"ver", "command /c ver", "cmd /c ver"} and command not in PLATFORM_COMMANDS_SEEN:
                PLATFORM_COMMANDS_SEEN.add(command)
                return True
        frame = frame.f_back
    return False


def record_blocked_event(event: str, category: str = "unexplained_blocked_attempt") -> None:
    """Record call-site provenance, never subprocess arguments or network hosts."""
    frames = []
    frame = sys._getframe(2)
    while frame is not None and len(frames) < 8:
        path = Path(frame.f_code.co_filename)
        try:
            name = path.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            name = path.name
        frames.append({"file": name, "line": frame.f_lineno, "function": frame.f_code.co_name})
        frame = frame.f_back
    GUARD_EVENTS.append({"event": event, "category": category, "unit": CURRENT_UNIT, "frames": frames})


def audit_hook(event: str, args: tuple) -> None:
    if event in {"socket.connect", "socket.sendto", "socket.sendmsg", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
        # On Windows socketpair() itself connects an ephemeral loopback pair.
        address = args[1] if event == "socket.connect" and len(args) > 1 else None
        local_pair = (
            event == "socket.connect"
            and getattr(SOCKETPAIR_SCOPE, "active", False)
            and isinstance(address, tuple)
            and address[0] in {"127.0.0.1", "::1"}
        )
        if local_pair:
            GUARD_COUNTS["internal_socketpair_connections"] += 1
            return
        GUARD_COUNTS["network_blocked"] += 1
        record_blocked_event(event)
        raise AuditSafetyBarrier("Network operation blocked by audit harness")
    if event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.posix_spawnp"}:
        GUARD_COUNTS["subprocess_blocked"] += 1
        category = "unexplained_blocked_attempt"
        if event == "subprocess.Popen" and verified_platform_introspection():
            GUARD_COUNTS["blocked_platform_introspection"] += 1
            category = "blocked_platform_introspection"
        record_blocked_event(event, category)
        raise AuditSafetyBarrier("Subprocess blocked while running tests")
    if event == "sqlite3.connect" and args:
        reject_protected(args[0], "active_sqlite_blocked")
    if event == "open" and args:
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            reject_protected(args[0], "active_write_blocked")
    if event in {"os.remove", "os.rmdir", "os.mkdir", "os.rename", "os.replace"}:
        for path in args[:2]:
            reject_protected(path, "active_mutation_blocked")
    if event == "import" and args and args[0] == "main":
        GUARD_COUNTS["main_import_blocked"] += 1
        raise AuditSafetyBarrier("Importing main is outside this regression probe")


def install_barriers() -> None:
    original_socketpair = socket.socketpair

    def internal_socketpair(*args, **kwargs):
        previous = getattr(SOCKETPAIR_SCOPE, "active", False)
        SOCKETPAIR_SCOPE.active = True
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            SOCKETPAIR_SCOPE.active = previous

    socket.socketpair = internal_socketpair
    sys.addaudithook(audit_hook)
    try:
        sys.audit("socket.connect", None, ("203.0.113.1", 9))
    except AuditSafetyBarrier:
        pass
    else:
        raise AssertionError("Network barrier self-check failed")
    try:
        reject_protected(ACTIVE_CHROMA, "self_check")
    except AuditSafetyBarrier:
        pass
    else:
        raise AssertionError("Active path barrier self-check failed")
    GUARD_COUNTS.clear()
    GUARD_EVENTS.clear()


def protect_persistent_client() -> None:
    import chromadb

    original = chromadb.PersistentClient

    def guarded_persistent_client(*args, **kwargs):
        path = args[0] if args else kwargs.get("path", "./chroma")
        reject_protected(path, "active_persistent_client_blocked")
        GUARD_COUNTS["isolated_persistent_clients"] += 1
        return original(*args, **kwargs)

    chromadb.PersistentClient = guarded_persistent_client


def hashes() -> dict[str, str]:
    result = {}
    for path in ACTIVE_FILES:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        result[path.relative_to(ROOT).as_posix()] = digest.hexdigest()
    return result


def safe_exception(exc: BaseException) -> dict:
    frames = []
    for frame in traceback.extract_tb(exc.__traceback__):
        path = Path(frame.filename)
        try:
            relative = path.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            continue
        frames.append({"file": relative, "line": frame.lineno, "function": frame.name})
    return {"error_type": type(exc).__name__, "frames": frames[-8:]}


def known_failure_matches(name: str, exc: BaseException) -> bool:
    if KNOWN_FAILURE is None or name != KNOWN_FAILURE or not isinstance(exc, AssertionError):
        return False
    for frame in traceback.extract_tb(exc.__traceback__):
        if frame.name == "test_eliminacion" and frame.line == "assert client.contar() == 3":
            return True
    return False


def discover() -> list[tuple[str, list[str]]]:
    modules = []
    for package in TEST_ROOTS:
        for path in sorted((ROOT / package).glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            functions = [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
            tests = sorted(name for name in functions if name.startswith("test_"))
            if not tests and "main" in functions:
                tests = ["main"]
            if not tests:
                raise ValueError(f"No tests or main: {package}.{path.stem}")
            modules.append((f"{package}.{path.stem}", tests))
    return modules


def main() -> int:
    global CURRENT_UNIT
    started = time.monotonic()
    inventory = discover()
    before = hashes()
    original_cwd = Path.cwd()
    results = []
    import_failures = []
    fatal = None
    cleanup_error = None
    # Preserve logger behavior for tests attaching their own capture handlers,
    # but prevent core.logger from creating the repository's human log file.
    logging.getLogger().handlers[:] = [logging.NullHandler()]
    logging.getLogger().setLevel(logging.INFO)
    logging.lastResort = logging.NullHandler()
    # Do not load .env (temporary cwd) or retain inherited provider credentials.
    settings_tree = ast.parse((ROOT / "config/settings.py").read_text(encoding="utf-8-sig"))
    settings_fields = {
        node.target.id
        for cls in settings_tree.body if isinstance(cls, ast.ClassDef) and cls.name == "Settings"
        for node in cls.body if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    scrubbed = 0
    for name in list(os.environ):
        if name in settings_fields or any(token in name.upper() for token in ("API_KEY", "AUTH_TOKEN", "ACCESS_TOKEN", "SECRET", "PASSWORD")):
            os.environ.pop(name)
            scrubbed += 1
    os.environ["ANONYMIZED_TELEMETRY"] = "False"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    sys.path.insert(0, str(ROOT))
    temp = TemporaryDirectory(prefix="lazzaro-regression-audit-")
    try:
        os.chdir(temp.name)
        install_barriers()
        CURRENT_UNIT = "import_chromadb_for_path_barrier"
        with silent_test_output():
            protect_persistent_client()
        for module_name, names in inventory:
            CURRENT_UNIT = module_name + ".__import__"
            try:
                with silent_test_output():
                    module = importlib.import_module(module_name)
            except BaseException as exc:
                import_failures.append({"module": module_name, "unexecuted_tests": names, **safe_exception(exc)})
                print(json.dumps({"module": module_name, "status": "IMPORT_FAIL", "error_type": type(exc).__name__}), flush=True)
                continue
            module_counts = Counter()
            for name in names:
                qualified = f"{module_name}.{name}"
                CURRENT_UNIT = qualified
                try:
                    function = getattr(module, name)
                    if not inspect.isfunction(function):
                        raise TypeError("Discovered test is not a function")
                    inspect.signature(function).bind()
                    with silent_test_output():
                        result = function()
                        if inspect.isawaitable(result):
                            result.close()
                            raise TypeError("Async test needs an explicit runner")
                    status = "PASS"
                    detail = {}
                except BaseException as exc:
                    status = "KNOWN_XFAIL" if known_failure_matches(qualified, exc) else "FAIL"
                    detail = safe_exception(exc)
                results.append({"test": qualified, "status": status, **detail})
                module_counts[status] += 1
            print(json.dumps({"module": module_name, "counts": dict(module_counts)}), flush=True)
    except BaseException as exc:
        fatal = safe_exception(exc)
    finally:
        with silent_test_output():
            gc.collect()
            os.chdir(original_cwd)
            try:
                temp.cleanup()
            except BaseException as exc:
                cleanup_error = safe_exception(exc)
        after = hashes()

    totals = Counter(row["status"] for row in results)
    unchanged = before == after
    unexplained_blocked = {
        name: count for name, count in GUARD_COUNTS.items()
        if name.endswith("blocked") and name != "subprocess_blocked" and count
    }
    unexplained_subprocesses = GUARD_COUNTS["subprocess_blocked"] - GUARD_COUNTS["blocked_platform_introspection"]
    if unexplained_subprocesses:
        unexplained_blocked["subprocess_blocked"] = unexplained_subprocesses
    expected_test_units = sum(len(names) for _, names in inventory)
    report = {
        "expected_modules": len(inventory),
        "expected_test_units": expected_test_units,
        "executed_test_units": len(results),
        "pass": totals["PASS"],
        "known_xfail": totals["KNOWN_XFAIL"],
        "unexpected_fail": totals["FAIL"],
        "import_failures": import_failures,
        "failures": [row for row in results if row["status"] != "PASS"],
        "fatal": fatal,
        "cleanup_error": cleanup_error,
        "active_chroma_sha256_unchanged": unchanged,
        "active_files_checked": len(before),
        "barriers": dict(GUARD_COUNTS),
        "blocked_events": GUARD_EVENTS,
        "unexplained_blocked_attempts": unexplained_blocked,
        "inherited_configuration_variables_removed": scrubbed,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "limits": [
            "Existing test_* functions and main-only modules are separate units; main assertions are not individually counted.",
            "Temporary cwd; existing tests may create additional temporary directories.",
            "No outgoing Python sockets or child processes permitted; Windows internal socketpair only is allowed.",
            "Three verified platform._syscmd_ver introspection commands stay blocked; stdlib falls back, so OS introspection is not identical to the baseline environment.",
            "Audit hooks are not an OS firewall for native extensions; SDK calls are not exercised by these tests.",
            "Chroma semantic tests depend on the existing local embedding-model cache; downloads are blocked.",
            "No raw stdout, stderr, logs, exception messages, credentials or hashes are emitted.",
            "Hash equality cannot attribute concurrent activity by other processes; active runtime should remain stopped.",
        ],
    }
    report["suite_gate"] = "PASS" if (
        totals["PASS"] + totals["KNOWN_XFAIL"] == expected_test_units
        and totals["KNOWN_XFAIL"] == (1 if KNOWN_FAILURE is not None else 0)
        and not totals["FAIL"]
        and len(results) == expected_test_units
        and not import_failures and not fatal
    ) else "FAIL"
    report["isolation_gate"] = "PASS" if (
        unchanged and not unexplained_blocked and not cleanup_error and not fatal
    ) else "FAIL"
    report["gate"] = "PASS" if report["suite_gate"] == report["isolation_gate"] == "PASS" else "FAIL"
    print("REGRESSION_REPORT=" + json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["gate"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
