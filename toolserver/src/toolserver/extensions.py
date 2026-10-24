"""Agent-written extensions: validate, install into the host, reload, and register as tools.

Nothing here is specific to a host. The host config (hosts/<host>.toml) says where in-host
files go and which named reload commands exist. Everything else comes from the agent's
manifest, `extension.json` in its workspace:

    {
      "name": "status_probe",                      # [a-z][a-z0-9_]{2,47}
      "reason": "why the agent wrote or changed it",
      "in_host": {"source": "plugin", "install_as": "status-probe"},   # optional
      "reload": "restart",                         # optional: a reload action from the host config
      "tools": [{
        "name": "get_status",
        "description": "...",
        "input_schema": {"type": "object", "properties": {...}},     # optional
        "run": ["python3", "tools/status.py"],                      # runs in the agent's sandbox
        "where": "sandbox",                          # optional; reserved for host-side helpers later
        "timeout_s": 30, "read_only": true, "depends_on": ["..."]
      }],
      "health_check": {"tool": "get_status", "args": {}}            # optional
    }

A tool runs inside the agent's container with the extension directory as cwd, its arguments
as JSON on stdin, and its stdout as the result. How it talks to the host (RCON, files, a
socket) is the agent's choice. If stdout is a JSON object with a "summary" string, that
line is what the event log and the UI show.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from mcp import types
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.tools.base import Tool

from .events import EventLog

NAME = re.compile(r"[a-z][a-z0-9_]{2,47}")
INSTALL_AS = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
MAX_SOURCE_BYTES = 64 * 1024
MAX_RESULT_CHARS = 20_000
LANGS = {".lua": "lua", ".py": "python", ".cs": "csharp", ".java": "java", ".js": "javascript",
         ".ts": "typescript", ".sh": "shell", ".rs": "rust", ".go": "go", ".rb": "ruby"}


# --------------------------------------------------------------------------- host config

@dataclass
class HostConfig:
    name: str = "none"
    window_title: str = ""
    process: str = ""                         # exe name of the host's program, e.g. "notepad.exe"
    capture: str = "window"                   # window | process | monitor (see capture.py)
    up_check: str | list[str] = "window"      # window | always | [argv]: up when it exits 0
    agent_ports: list[int] = field(default_factory=list)  # host ports the agent's sandbox may reach
    install_dir: Path | None = None          # where in-host extension files go
    reload_actions: dict[str, list[str]] = field(default_factory=dict)
    reload_timeout_s: int = 120

    @classmethod
    def load(cls, path: Path | None) -> "HostConfig":
        if path is None:
            return cls()
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        host, ext = raw.get("host", {}), raw.get("extensions", {})
        install = ext.get("install_dir")
        return cls(
            name=host.get("name", path.stem),
            window_title=host.get("window_title", ""),
            process=host.get("process", ""),
            capture=_one_of(host.get("capture", "window"), CAPTURE_MODES, "capture"),
            up_check=host.get("up_check", "window"),
            agent_ports=_ports(host.get("agent_ports", [])),
            install_dir=Path(os.path.expanduser(os.path.expandvars(install))) if install else None,
            reload_actions={k: [os.path.expandvars(a) for a in v] for k, v in ext.get("reload", {}).items()},
            reload_timeout_s=int(ext.get("reload_timeout_s", 120)),
        )


CAPTURE_MODES = ("window", "process", "monitor")


def _ports(value) -> list[int]:
    if not isinstance(value, list) or not all(isinstance(p, int) and 0 < p < 65536 for p in value):
        raise ValueError(f"host config: [host] agent_ports must be a list of port numbers, not {value!r}")
    return value


def _one_of(value: str, allowed: tuple[str, ...], key: str) -> str:
    if value not in allowed:
        raise ValueError(f"host config: [host] {key} must be one of {', '.join(allowed)}, not {value!r}")
    return value


# --------------------------------------------------------------------------- where tools run

class Executor(Protocol):
    async def run(self, argv: list[str], cwd: str, stdin: str, timeout_s: float) -> tuple[int, str, str]: ...


class ContainerExecutor:
    """Runs a tool inside the agent's container, where the agent wrote it."""

    def __init__(self, container: str, workdir: str = "/home/agent/work") -> None:
        self.container, self.workdir = container, workdir

    async def run(self, argv, cwd, stdin, timeout_s):
        wd = f"{self.workdir}/{cwd}".rstrip("/")
        return await _exec(["docker", "exec", "-i", "-w", wd, self.container, *argv], None, stdin, timeout_s)


class LocalExecutor:
    """Runs a tool on this machine, in the workspace. For tests and container-less runs."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    async def run(self, argv, cwd, stdin, timeout_s):
        return await _exec(argv, self.workspace / cwd, stdin, timeout_s)


async def _exec(argv: list[str], cwd: Path | None, stdin: str, timeout_s: float) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(*argv, cwd=cwd, stdin=asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin.encode()), timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "", f"timed out after {timeout_s:g} s"
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


# --------------------------------------------------------------------------- manifests

@dataclass
class ToolSpec:
    name: str
    description: str
    run: list[str]
    input_schema: dict
    timeout_s: float
    read_only: bool
    depends_on: list[str]


@dataclass
class Manifest:
    name: str
    reason: str
    dir_rel: str                       # extension directory, relative to the workspace
    in_host: dict | None
    reload: str | None
    tools: list[ToolSpec]
    health_check: dict | None


def parse_manifest(workspace: Path, manifest_rel: str, host: HostConfig, builtin: set[str]) -> Manifest:
    path = _inside(workspace, manifest_rel)
    if path is None or not path.is_file():
        raise ValueError(f"no manifest at {manifest_rel!r} (path is relative to your workspace)")
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{manifest_rel} is not valid JSON: {e}") from None
    problems: list[str] = []
    if not isinstance(m, dict):
        raise ValueError(f"{manifest_rel} must be a JSON object")
    name = m.get("name", "")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        problems.append("name: lowercase letters, digits and _, 3-48 chars, starting with a letter")
    reason = m.get("reason", "")
    if not isinstance(reason, str) or not reason.strip():
        problems.append("reason: say why you wrote or changed this extension")
    ext_dir = path.parent
    in_host = m.get("in_host")
    if in_host is not None:
        if host.install_dir is None:
            problems.append("in_host: this host has no install_dir, so nothing can be installed into it")
        elif not isinstance(in_host, dict) or not isinstance(in_host.get("source"), str) \
                or not INSTALL_AS.fullmatch(str(in_host.get("install_as", ""))):
            problems.append('in_host: needs "source" (a directory next to the manifest) and "install_as" (a folder name)')
        elif (src := _inside(ext_dir, in_host["source"])) is None or not src.is_dir():
            problems.append(f"in_host.source: no directory {in_host['source']!r} next to the manifest")
    reload = m.get("reload")
    if reload is not None and reload not in host.reload_actions:
        problems.append(f"reload: {reload!r} is not one of this host's reload actions {sorted(host.reload_actions)}")
    tools: list[ToolSpec] = []
    raw_tools = m.get("tools", [])
    if not isinstance(raw_tools, list) or not raw_tools:
        problems.append("tools: a non-empty list")
        raw_tools = []
    seen: set[str] = set()
    for i, t in enumerate(raw_tools):
        where = f"tools[{i}]"
        if not isinstance(t, dict):
            problems.append(f"{where}: an object"); continue
        tname = t.get("name", "")
        if not isinstance(tname, str) or not NAME.fullmatch(tname):
            problems.append(f"{where}.name: lowercase letters, digits and _, 3-48 chars")
        elif tname in builtin:
            problems.append(f"{where}.name: {tname!r} is a built-in tool")
        elif tname in seen:
            problems.append(f"{where}.name: {tname!r} appears twice")
        seen.add(tname)
        run = t.get("run")
        if not (isinstance(run, list) and run and all(isinstance(a, str) and a for a in run)):
            problems.append(f'{where}.run: a non-empty list of strings, e.g. ["python3", "query.py"]')
        schema = t.get("input_schema", {"type": "object", "properties": {}})
        if not isinstance(schema, dict) or schema.get("type", "object") != "object":
            problems.append(f'{where}.input_schema: a JSON Schema object with "type": "object"')
        if t.get("where", "sandbox") != "sandbox":
            problems.append(f'{where}.where: only "sandbox" is supported (your container); host-side helpers come later')
        desc = t.get("description", "")
        if not isinstance(desc, str) or not desc.strip():
            problems.append(f"{where}.description: say what it returns")
        if not problems or all(not p.startswith(where) for p in problems):
            tools.append(ToolSpec(name=tname, description=desc, run=run, input_schema={"type": "object", **schema},
                                  timeout_s=float(t.get("timeout_s", 30)), read_only=bool(t.get("read_only", True)),
                                  depends_on=[d for d in t.get("depends_on", []) if isinstance(d, str)]))
    hc = m.get("health_check")
    if hc is not None and (not isinstance(hc, dict) or hc.get("tool") not in seen):
        problems.append('health_check: {"tool": <one of this extension\'s tools>, "args": {...}}')
    if problems:
        raise ValueError(f"{manifest_rel} was not loaded; fix these and call load_extension again:\n- " + "\n- ".join(problems))
    return Manifest(name=name, reason=reason, dir_rel=ext_dir.relative_to(workspace).as_posix(), in_host=in_host,
                    reload=reload, tools=tools, health_check=hc)


def _inside(root: Path, rel: str) -> Path | None:
    p = (root / rel).resolve()
    return p if p == root.resolve() or root.resolve() in p.parents else None


# --------------------------------------------------------------------------- the library

class _RawTool(Tool):
    """A tool with the agent's own argument schema; arguments go to the agent's program untouched."""

    async def run(self, arguments, context=None, convert_result=False):
        result = await self.fn(arguments)
        return self.fn_metadata.convert_result(result) if convert_result else result


@dataclass
class Loaded:
    manifest: Manifest
    version: int
    sources: list[dict]


class Extensions:
    def __init__(self, mcp: FastMCP, log: EventLog, host: HostConfig, workspace: Path,
                 executor: Executor, archive_dir: Path, builtin: set[str]) -> None:
        self.mcp, self.log, self.host = mcp, log, host
        self.workspace, self.executor, self.archive_dir, self.builtin = workspace, executor, archive_dir, builtin
        self.loaded: dict[str, Loaded] = {}          # extension name -> what is live
        self.owner: dict[str, str] = {}               # tool name -> extension name
        self._lock = asyncio.Lock()

    async def load(self, manifest_rel: str) -> str:
        async with self._lock:
            m = parse_manifest(self.workspace, manifest_rel, self.host, self.builtin)
            for t in m.tools:
                if self.owner.get(t.name, m.name) != m.name:
                    raise ValueError(f"tool {t.name!r} already belongs to extension {self.owner[t.name]!r}")
            version = self.loaded[m.name].version + 1 if m.name in self.loaded else 1
            ext_dir = self.workspace / m.dir_rel
            archived = self.archive_dir / m.name / f"v{version}"
            shutil.copytree(ext_dir, archived, dirs_exist_ok=True)   # outside the agent's reach

            notes = []
            if m.in_host:
                notes.append(self._install(m, ext_dir))
            if m.reload:
                ok, tail = await self._reload(m.reload)
                if not ok:
                    if m.in_host:
                        self._restore(m)
                    raise ValueError(f"reload {m.reload!r} failed, so the previous version stays installed and "
                                     f"registered. Output:\n{tail}")
                notes.append(f"reload {m.reload!r} ok")

            for old in [n for n, owner in self.owner.items() if owner == m.name]:
                self.mcp.remove_tool(old)
                del self.owner[old]
            for t in m.tools:
                self._register(m, t)
                self.owner[t.name] = m.name
            sources = _sources(ext_dir)
            self.loaded[m.name] = Loaded(m, version, sources)
            for t in m.tools:
                self.log.write("hook_loaded", hook=t.name, version=version, extension=m.name, reason=m.reason,
                               language=_language(sources, t), kind="in_host" if m.in_host else "helper",
                               depends_on=t.depends_on, files=sources, source_ref=f"extensions/{m.name}/v{version}")

            health = ""
            if m.health_check:
                hc_tool = m.health_check["tool"]
                try:
                    out = await self.call(hc_tool, m.health_check.get("args", {}), via="health_check")
                    self.log.write("hook_status", hook=hc_tool, status="ok", detail="health check passed")
                    health = f"\nHealth check {hc_tool}: passed. Output: {out[:300]}"
                except ToolError as e:
                    self.log.write("hook_status", hook=hc_tool, status="broken", detail=str(e)[:500])
                    health = f"\nHealth check {hc_tool}: FAILED. {e}"
            names = ", ".join(t.name for t in m.tools)
            return (f"Loaded extension {m.name} v{version}. Tools now available: {names}. "
                    + " ".join(notes) + health)

    async def call(self, name: str, args: dict, via: str = "direct") -> str:
        ext = self.owner.get(name)
        if ext is None:
            raise ToolError(f"no hook named {name!r}; see list_hooks")
        loaded = self.loaded[ext]
        spec = next(t for t in loaded.manifest.tools if t.name == name)
        missing = [k for k in spec.input_schema.get("required", []) if k not in args]
        hook = {"name": name, "version": loaded.version, "extension": ext}
        with self.log.call(name, "hook", args, via=via, hook=hook) as res:
            if missing:
                raise ToolError(f"{name}: missing required argument(s) {missing}")
            code, out, err = await self.executor.run(spec.run, loaded.manifest.dir_rel, json.dumps(args), spec.timeout_s)
            if code != 0:
                raise ToolError(f"{name} exited with {code}. stderr (last lines):\n{_tail(err)}")
            out = out.strip()
            if len(out) > MAX_RESULT_CHARS:
                out = out[:MAX_RESULT_CHARS] + f"\n… [{len(out) - MAX_RESULT_CHARS} more characters cut]"
            res.set("text", len(out), summary=_summary(out))
            return out

    def describe(self) -> list[dict]:
        return [{"tool": t.name, "extension": name, "version": l.version, "description": t.description,
                 "kind": "in_host" if l.manifest.in_host else "helper", "depends_on": t.depends_on}
                for name, l in sorted(self.loaded.items()) for t in l.manifest.tools]

    # ----------------------------------------------------------------- internals

    def _register(self, m: Manifest, t: ToolSpec) -> None:
        async def run(arguments: dict) -> str:
            return await self.call(t.name, arguments)

        async def _shape(args: dict) -> str:  # only to borrow FastMCP's result conversion for str
            return ""

        base = Tool.from_function(_shape, name=t.name, description=t.description, structured_output=False)
        annotations = types.ToolAnnotations(readOnlyHint=t.read_only, destructiveHint=False, openWorldHint=False)
        self.mcp._tool_manager._tools[t.name] = _RawTool(
            fn=run, name=t.name, description=f"{t.description}\n(Hook from extension {m.name}.)",
            parameters=t.input_schema, fn_metadata=base.fn_metadata, is_async=True, annotations=annotations)

    def _target(self, m: Manifest) -> Path:
        return self.host.install_dir / m.in_host["install_as"]

    def _backup(self, m: Manifest) -> Path:
        # Kept in the run's archive, never in the host's folder, where the host might load it.
        return self.archive_dir / m.name / "installed-previous"

    def _install(self, m: Manifest, ext_dir: Path) -> str:
        target, backup = self._target(m), self._backup(m)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(backup, ignore_errors=True)
        if target.exists():
            shutil.move(str(target), str(backup))   # may cross drives, so move rather than rename
        shutil.copytree(ext_dir / m.in_host["source"], target)
        return f"Installed into the host as {target.name}."

    def _restore(self, m: Manifest) -> None:
        target, backup = self._target(m), self._backup(m)
        shutil.rmtree(target, ignore_errors=True)
        if backup.exists():
            shutil.move(str(backup), str(target))

    async def _reload(self, action: str) -> tuple[bool, str]:
        argv = self.host.reload_actions[action]
        if not argv:
            return True, ""
        code, out, err = await _exec(argv, None, "", self.host.reload_timeout_s)
        return code == 0, _tail(out + err)


def _sources(ext_dir: Path) -> list[dict]:
    out = []
    for p in sorted(ext_dir.rglob("*")):
        if p.is_file() and p.suffix in LANGS | {".json": "", ".toml": "", ".txt": ""} and len(out) < 40:
            data = p.read_bytes()[:MAX_SOURCE_BYTES]
            out.append({"path": p.relative_to(ext_dir).as_posix(), "content": data.decode(errors="replace")})
    return out


def _language(sources: list[dict], t: ToolSpec) -> str:
    counts: dict[str, int] = {}
    for s in sources:
        lang = LANGS.get(Path(s["path"]).suffix)
        if lang:
            counts[lang] = counts.get(lang, 0) + len(s["content"])
    return max(counts, key=counts.get) if counts else LANGS.get(Path(t.run[-1]).suffix, "")


def _summary(out: str) -> str:
    try:
        obj = json.loads(out)
        if isinstance(obj, dict) and isinstance(obj.get("summary"), str):
            return obj["summary"][:200]
    except json.JSONDecodeError:
        pass
    first = out.splitlines()[0] if out else ""
    return first[:120]


def _tail(text: str, lines: int = 15) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])
