"""The pipeline's ffmpeg children run out of reach of a console Ctrl-C.

Windows sends a console Ctrl-C to every process on the console, POSIX to the
whole foreground process group. Before 2026-10-16 that killed the ffmpeg
decoding a row's audio for librosa (ingest/features.py) or classify
(ml/src/predict.py), and a concurrent run, which lets the stage finish its
batch, parked those rows at <stage>_stage_error with an error blaming the
audio. Reproduced in review with GenerateConsoleCtrlEvent(CTRL_C_EVENT); here
the subprocess call is mocked and its kwargs checked.
"""
import ast
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import features

PREDICT = Path(__file__).resolve().parents[2] / "ml" / "src" / "predict.py"
ON_WINDOWS = hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP")


def assert_detached(kwargs, platform):
    if platform == "win32":
        assert kwargs.get("creationflags") == subprocess.CREATE_NEW_PROCESS_GROUP, kwargs
        assert "start_new_session" not in kwargs
    else:
        assert kwargs.get("start_new_session") is True, kwargs
        assert "creationflags" not in kwargs


@pytest.fixture
def runs(monkeypatch):
    """Every subprocess.run call, as (args, kwargs); nothing is started."""
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout=b"\x00\x00" * 4, returncode=0)
    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_librosas_ffmpeg_gets_a_process_group_of_its_own(runs, monkeypatch, platform):
    if platform == "win32" and not ON_WINDOWS:
        pytest.skip("CREATE_NEW_PROCESS_GROUP exists on Windows only")
    monkeypatch.setattr(features, "_FFMPEG", "ffmpeg")
    monkeypatch.setattr(features.sys, "platform", platform)
    wav = features._to_wav("track.m4a")
    os.remove(wav)
    (args, kwargs), = runs
    assert args[0][0] == "ffmpeg" and kwargs["check"] is True
    assert_detached(kwargs, platform)


def predict_loader(platform):
    """ml/src/predict.py's _load_audio_ffmpeg, from its source: importing the
    module loads torch, Lightning and transformers (47 s, measured
    2026-10-16), and the function needs none of them."""
    tree = ast.parse(PREDICT.read_text(encoding="utf-8"))
    (fn,) = [n for n in tree.body
             if isinstance(n, ast.FunctionDef) and n.name == "_load_audio_ffmpeg"]
    namespace = {"np": np, "sys": SimpleNamespace(platform=platform)}
    exec(compile(ast.Module([fn], type_ignores=[]), str(PREDICT), "exec"), namespace)
    return namespace["_load_audio_ffmpeg"]


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_classifys_ffmpeg_gets_a_process_group_of_its_own(runs, platform):
    pytest.importorskip("imageio_ffmpeg")
    if platform == "win32" and not ON_WINDOWS:
        pytest.skip("CREATE_NEW_PROCESS_GROUP exists on Windows only")
    pcm = predict_loader(platform)("track.m4a", 24000)
    (args, kwargs), = runs
    assert kwargs["capture_output"] is True and kwargs["check"] is True
    assert_detached(kwargs, platform)
    assert pcm.shape == (4,)
