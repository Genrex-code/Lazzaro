"""ROOT-031: fallo del destino de archivo no bloquea import ni runtime."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import logging
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

import core.logger as logger_module


@pytest.fixture
def isolated_logging(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = logging.getLogger()
    monkeypatch.setattr(logger_module, "_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(logger_module, "_LOG_FILE", tmp_path / "logs" / "lazzaro.log")
    monkeypatch.setattr(logger_module, "_FALLBACK_REPORTED", False)
    displaced: list[logging.Handler] | None = None
    old_level = root.level

    def isolate():
        nonlocal displaced
        # Pytest installs capture handlers after fixture setup, before the test body.
        displaced = root.handlers[:]
        for handler in displaced:
            root.removeHandler(handler)
        return root

    try:
        yield isolate
    finally:
        if displaced is not None:
            for handler in root.handlers[:]:
                root.removeHandler(handler)
                if handler not in displaced:
                    handler.close()
            root.setLevel(old_level)
            for handler in displaced:
                root.addHandler(handler)
        (tmp_path / "logs" / "lazzaro.log").unlink(missing_ok=True)


def _deny(*args, **kwargs):
    raise PermissionError("CANARY_SECRET_PATH")


def test_normal_topology_and_file_output(isolated_logging, tmp_path, capsys):
    root = isolated_logging()
    logger = logger_module.get_logger("root031.normal")
    assert logger.name == "root031.normal"
    assert [type(h) for h in root.handlers] == [logging.FileHandler, logging.StreamHandler]
    assert root.level == logging.INFO
    assert root.handlers[0].formatter._fmt == logger_module._LOG_FORMAT
    assert root.handlers[1].stream is sys.stdout
    assert root.handlers[0].baseFilename == str(tmp_path / "logs" / "lazzaro.log")
    logger.warning("normal-probe")
    assert "normal-probe" in (tmp_path / "logs" / "lazzaro.log").read_text(encoding="utf-8")
    assert "normal-probe" in capsys.readouterr().out
    assert logger_module.get_logger("root031.normal") is logger
    assert len(root.handlers) == 2


@pytest.mark.parametrize("failure", [PermissionError, OSError])
def test_directory_failure_falls_back_once(
    isolated_logging, monkeypatch, capsys, failure
):
    root = isolated_logging()
    monkeypatch.setattr(Path, "mkdir", lambda *a, **kw: (_ for _ in ()).throw(failure("CANARY_SECRET_PATH")))
    logger = logger_module.get_logger("root031.dir")
    assert [type(h) for h in root.handlers] == [logging.StreamHandler]
    assert root.handlers[0].stream is sys.stderr
    for name in ("root031.dir", "root031.other", "root031.dir"):
        logger_module.get_logger(name)
    assert len(root.handlers) == 1
    logger.warning("warning-probe")
    logger.error("error-probe")
    captured = capsys.readouterr().err
    assert captured.count("file logging unavailable") == 1
    assert "warning-probe" in captured and "error-probe" in captured
    assert "CANARY_SECRET_PATH" not in captured


@pytest.mark.parametrize("failure", [PermissionError, OSError])
def test_file_open_failure_falls_back(isolated_logging, monkeypatch, failure, capsys):
    root = isolated_logging()
    monkeypatch.setattr(logging, "FileHandler", lambda *a, **kw: (_ for _ in ()).throw(failure("CANARY_SECRET_PATH")))
    logger = logger_module.get_logger("root031.file")
    assert len(root.handlers) == 1
    assert type(root.handlers[0]) is logging.StreamHandler
    assert root.handlers[0].stream is sys.stderr
    logger.warning("still-running")
    assert "still-running" in capsys.readouterr().err


def test_partial_handler_is_detached_and_closed(isolated_logging, monkeypatch):
    root = isolated_logging()
    actual_basic_config = logging.basicConfig
    acquired = []
    actual_file_handler = logging.FileHandler

    def file_handler(*args, **kwargs):
        handler = actual_file_handler(*args, **kwargs)
        actual_close = handler.close

        def failed_flush_close():
            actual_close()
            raise OSError("failed flush")

        handler.close = failed_flush_close
        acquired.append(handler)
        return handler

    calls = 0

    def partial_basic_config(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            root.addHandler(kwargs["handlers"][0])
            raise OSError("CANARY_SECRET_PATH")
        actual_basic_config(**kwargs)

    monkeypatch.setattr(logging, "FileHandler", file_handler)
    monkeypatch.setattr(logging, "basicConfig", partial_basic_config)
    logger_module.get_logger("root031.partial")
    assert calls == 2
    assert len(acquired) == 1
    assert acquired[0] not in root.handlers
    assert acquired[0].stream is None
    assert len(root.handlers) == 1
    assert type(root.handlers[0]) is logging.StreamHandler


def test_broken_stderr_fallback_does_not_recurse(isolated_logging, monkeypatch):
    root = isolated_logging()
    monkeypatch.setattr(Path, "mkdir", _deny)
    monkeypatch.setattr(logging, "StreamHandler", _deny)
    assert logger_module.get_logger("root031.broken-stderr").name == "root031.broken-stderr"
    assert not root.handlers


def test_import_consumer_survives_denied_directory(isolated_logging, monkeypatch):
    root = isolated_logging()
    monkeypatch.setattr(Path, "mkdir", _deny)
    # Execute a fresh copy of the module to exercise its import-time setup.
    name = "_root031_import_probe"
    spec = importlib.util.spec_from_file_location(name, Path(logger_module.__file__))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.object(logging.Logger, "warning", side_effect=AssertionError("recursive logger")):
        spec.loader.exec_module(module)
        consumer = {}
        exec(
            "logger = get_logger('root031.consumer')\nready = logger.name == 'root031.consumer'",
            {"get_logger": module.get_logger},
            consumer,
        )
    assert consumer["ready"]
    assert len(root.handlers) == 1


@pytest.mark.parametrize("failure", [SystemExit, KeyboardInterrupt])
def test_control_flow_exceptions_not_swallowed(isolated_logging, monkeypatch, failure):
    root = isolated_logging()
    monkeypatch.setattr(Path, "mkdir", lambda *a, **kw: (_ for _ in ()).throw(failure()))
    with pytest.raises(failure):
        logger_module.get_logger("root031.interrupt")
    assert not root.handlers


def test_existing_root_handler_is_respected(isolated_logging, monkeypatch):
    root = isolated_logging()
    owned_elsewhere = logging.NullHandler()
    root.addHandler(owned_elsewhere)
    monkeypatch.setattr(Path, "mkdir", _deny)
    assert logger_module.get_logger("root031.preconfigured").name == "root031.preconfigured"
    assert root.handlers == [owned_elsewhere]


def test_concurrent_failure_installs_only_one_fallback(isolated_logging, monkeypatch, capsys):
    root = isolated_logging()
    calls = 0

    def denied(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "mkdir", denied)
    with ThreadPoolExecutor(max_workers=8) as pool:
        names = list(pool.map(logger_module.get_logger, [f"root031.worker.{i}" for i in range(24)]))
    assert len({logger.name for logger in names}) == 24
    assert calls == 1
    assert len(root.handlers) == 1
    assert capsys.readouterr().err.count("file logging unavailable") == 1


def test_concurrent_success_installs_only_one_normal_pair(isolated_logging):
    root = isolated_logging()
    with ThreadPoolExecutor(max_workers=8) as pool:
        names = list(pool.map(logger_module.get_logger, [f"root031.normal-worker.{i}" for i in range(24)]))
    assert len({logger.name for logger in names}) == 24
    assert [type(h) for h in root.handlers] == [logging.FileHandler, logging.StreamHandler]


def test_competing_root_configuration_does_not_leak_fallback(
    isolated_logging, monkeypatch
):
    root = isolated_logging()
    monkeypatch.setattr(Path, "mkdir", _deny)
    external = logging.NullHandler()
    fallback = []
    actual_stream_handler = logging.StreamHandler

    def stream_handler(*args, **kwargs):
        handler = actual_stream_handler(*args, **kwargs)
        fallback.append(handler)
        return handler

    def competing_basic_config(**kwargs):
        root.addHandler(external)

    monkeypatch.setattr(logging, "StreamHandler", stream_handler)
    monkeypatch.setattr(logging, "basicConfig", competing_basic_config)
    logger_module.get_logger("root031.competing")
    assert root.handlers == [external]
    assert len(fallback) == 1
    assert fallback[0] not in root.handlers
