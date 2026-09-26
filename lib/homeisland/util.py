"""Small helpers shared by the CLI, the collector and the checks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable, List, Optional, Sequence

USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

_COLORS = {
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "dim": "2",
    "bold": "1",
}


def color(text: str, name: str) -> str:
    if not USE_COLOR:
        return text
    return "\033[%sm%s\033[0m" % (_COLORS[name], text)


def info(msg: str) -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    print(color("warning: ", "yellow") + msg, file=sys.stderr)


def error(msg: str) -> None:
    print(color("error: ", "red") + msg, file=sys.stderr)


class HomeIslandError(Exception):
    """An error with a message that is safe and useful to show to the user."""


def run(
    cmd: Sequence[str],
    check: bool = True,
    capture: bool = False,
    timeout: Optional[float] = None,
    env: Optional[dict] = None,
    cwd: Optional[str] = None,
    input_text: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """Run a command without a shell."""
    try:
        return subprocess.run(
            list(cmd),
            check=check,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            universal_newlines=True,
            timeout=timeout,
            env=env,
            cwd=cwd,
            input=input_text,
        )
    except FileNotFoundError:
        raise HomeIslandError("command not found: %s" % cmd[0])
    except subprocess.CalledProcessError as exc:
        detail = ""
        if capture and exc.stderr:
            detail = ": " + exc.stderr.strip().splitlines()[-1]
        raise HomeIslandError("command failed (%d): %s%s" % (exc.returncode, " ".join(cmd), detail))


def try_output(cmd: Sequence[str], timeout: float = 10) -> Optional[str]:
    """Return stdout of a command, or None if it is missing, fails or times out."""
    if not shutil.which(cmd[0]):
        return None
    try:
        res = subprocess.run(
            list(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    return res.stdout


def read_text(path: str, default: Optional[str] = None) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return default


def write_atomic(path: str, content: str, mode: int = 0o644) -> bool:
    """Write a file atomically. Returns True if the content changed."""
    old = read_text(path)
    if old == content and os.path.exists(path):
        if (os.stat(path).st_mode & 0o777) != mode:
            os.chmod(path, mode)
        return False
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return True


def write_json_atomic(path: str, data, mode: int = 0o644) -> bool:
    return write_atomic(path, json.dumps(data, indent=2, sort_keys=True) + "\n", mode)


def parse_env_file(text: str) -> dict:
    """Parse a KEY=VALUE file. No variable expansion, optional quotes."""
    result = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        result[key] = value
    return result


def load_env_file(path: str) -> dict:
    text = read_text(path)
    if text is None:
        return {}
    return parse_env_file(text)


def human_bytes(num: Optional[float]) -> str:
    if num is None:
        return "-"
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024 or unit == "TB":
            if unit == "B":
                return "%d %s" % (value, unit)
            return "%.1f %s" % (value, unit)
        value /= 1024
    return "%.1f TB" % value


def human_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return "%dd %02dh" % (days, hours)
    if hours:
        return "%dh %02dm" % (hours, minutes)
    return "%dm" % minutes


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def require_root(action: str) -> None:
    # HOMEISLAND_DEV=1 allows running against a scratch config as a normal user (development only).
    if not is_root() and os.environ.get("HOMEISLAND_DEV") != "1":
        raise HomeIslandError("%s requires root privileges; run it with sudo" % action)


def confirm(question: str, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise HomeIslandError("%s (refusing to continue without a terminal; pass --yes)" % question)
    answer = input("%s [y/N] " % question).strip().lower()
    return answer in ("y", "yes")


def table(rows: Iterable[Sequence[str]], headers: Optional[Sequence[str]] = None) -> str:
    rows = [list(map(str, r)) for r in rows]
    if headers:
        rows.insert(0, list(headers))
    if not rows:
        return ""
    widths = [max(len(r[i]) for r in rows if i < len(r)) for i in range(max(len(r) for r in rows))]
    lines: List[str] = []
    for idx, r in enumerate(rows):
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)).rstrip())
        if headers and idx == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)
