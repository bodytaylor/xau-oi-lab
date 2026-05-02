# tests/test_pine_exporter.py
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from tests.conftest import SAMPLE_SESSION


def test_generate_pine_contains_open_price(sample_session):
    from pine_exporter import generate_pine_script
    code = generate_pine_script(sample_session)
    assert "4621.78" in code


def test_generate_pine_contains_all_sd_levels(sample_session):
    from pine_exporter import generate_pine_script
    code = generate_pine_script(sample_session)
    for level in ["4867.89", "4785.85", "4703.82", "4539.74", "4457.71", "4375.67"]:
        assert level in code, f"Missing level {level}"


def test_generate_pine_contains_version_5(sample_session):
    from pine_exporter import generate_pine_script
    code = generate_pine_script(sample_session)
    assert "//@version=5" in code


def test_generate_pine_contains_magnets_when_phase2_complete(sample_session):
    from pine_exporter import generate_pine_script
    code = generate_pine_script(sample_session)
    # sample_session has magnets [4700.0, 4500.0] and phase2_complete=True
    assert "4700" in code
    assert "4500" in code


def test_generate_pine_no_magnets_when_phase2_incomplete(sample_session):
    from pine_exporter import generate_pine_script
    sample_session["phase2_complete"] = False
    code = generate_pine_script(sample_session)
    # Magnets should not appear in script when Phase 2 not done
    assert "OI Magnet" not in code


def test_run_writes_file_and_returns_code(sample_session, tmp_path):
    from pine_exporter import run
    with patch("pine_exporter.EXPORTS_DIR", tmp_path):
        code = run(sample_session)
    expected_file = tmp_path / f"session_{sample_session['date']}.pine"
    assert expected_file.exists()
    assert expected_file.read_text() == code


def test_run_returns_string(sample_session, tmp_path):
    from pine_exporter import run
    with patch("pine_exporter.EXPORTS_DIR", tmp_path):
        code = run(sample_session)
    assert isinstance(code, str)
    assert len(code) > 100
