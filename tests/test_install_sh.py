"""Tests for scripts/install.sh."""

import json
import subprocess
from pathlib import Path

INSTALL = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"


def _fake(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _bin_dir(tmp_path: Path) -> tuple[Path, Path]:
    """Create a fake toolchain and a separate tool bin directory."""
    tools = tmp_path / "tools"
    tools.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake(tools / "uv", "#!/bin/sh\nexit 0\n")
    _fake(tools / "uname", "#!/bin/sh\necho Darwin\n")
    _fake(tools / "gh", "#!/bin/sh\nexit 0\n")
    _fake(
        bin_dir / "anki-tools",
        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$MAN_LOG"\n',
    )
    return tools, bin_dir


def _run(
    tmp_path: Path, bin_dir: Path, *, path: str
) -> subprocess.CompletedProcess[str]:
    wheel = tmp_path / "y.whl"
    wheel.write_text("wheel", encoding="utf-8")
    release = tmp_path / "release.json"
    release.write_text(
        json.dumps({"browser_download_url": wheel.as_uri()}), encoding="utf-8"
    )
    return subprocess.run(
        ["sh", str(INSTALL)],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            "PATH": path,
            "HOME": str(tmp_path),
            "UV_TOOL_BIN_DIR": str(bin_dir),
            "MAN_LOG": str(tmp_path / "log"),
            "ANKI_TOOLS_RELEASE_URL": release.as_uri(),
        },
    )


def test_installer_runs_the_man_page_installer(tmp_path: Path) -> None:
    tools, bin_dir = _bin_dir(tmp_path)
    result = _run(tmp_path, bin_dir, path=f"{tools}:{bin_dir}:/usr/bin:/bin")
    assert result.returncode == 0, result.stderr
    assert "--install-man-pages" in (tmp_path / "log").read_text()


def test_installer_prints_the_path_fix(tmp_path: Path) -> None:
    tools, bin_dir = _bin_dir(tmp_path)
    result = _run(tmp_path, bin_dir, path=f"{tools}:/usr/bin:/bin")
    assert result.returncode == 0, result.stderr
    assert "add" in result.stderr
    assert "uv tool update-shell" in result.stderr
