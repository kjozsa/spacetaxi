import subprocess
import sys

import pytest

import spacetaxi
from spacetaxi.cli import main


def test_import():
    assert spacetaxi.__version__


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "usage: spacetaxi" in capsys.readouterr().out


def test_no_args_prints_usage(capsys):
    assert main([]) == 0
    assert "usage: spacetaxi" in capsys.readouterr().out


def test_module_entry_point():
    result = subprocess.run(
        [sys.executable, "-m", "spacetaxi", "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "usage: spacetaxi" in result.stdout
