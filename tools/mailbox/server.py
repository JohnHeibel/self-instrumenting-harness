# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2"]
# ///
"""Agent mailbox: async messages and a shared state board between coding agents.

Storage is a dedicated branch (default `mailbox`) of a GitHub repo, written via the
GitHub API. Every message is its own file, so concurrent writers never conflict.

Layout on the branch:
  messages/<ts>_<from>_<to>_<rand>.json   immutable messages
  state/<key>.json                        shared board entries (overwritten)
  cursors/<agent>.json                    ids of messages each agent has read
  agents/<agent>.json                     known agents and when they were last seen

Run as an MCP server (stdio):   uv run --script tools/mailbox/server.py
Print unread summary (hooks):   uv run --script tools/mailbox/server.py status
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

REPO = os.environ.get("MAILBOX_REPO", "JohnHeibel/self-instrumenting-harness")
BRANCH = os.environ.get("MAILBOX_BRANCH", "mailbox")
API = "https://api.github.com"
KINDS = ("info", "question", "request", "decision", "blocker")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
KEY_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")


class MailboxError(Exception):
    pass


# --------------------------------------------------------------------------- GitHub


def _token() -> str:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        raise MailboxError("No GITHUB_TOKEN set and the gh CLI is not installed.")
    if out.returncode != 0 or not out.stdout.strip():
        raise MailboxError("No GitHub token: run `gh auth login` or set GITHUB_TOKEN.")
    return out.stdout.strip()


class GitHub:
    def __init__(self, repo: str, branch: str):
        self.repo = repo
        self.branch = branch
        self.tok = _token()
        self.blob_cache: dict[str, dict] = {}  # immutable message files by path

    def req(self, method: str, path: str, body: dict | None = None, ok404: bool = False):
        url = path if path.startswith("http") else f"{API}{path}"
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(url, data=data, method=method)
        r.add_header("Authorization", f"Bearer {self.tok}")
        r.add_header("Accept", "application/vnd.github+json")
        r.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code == 404 and ok404:
                return None
            detail = e.read().decode(errors="replace")[:300]
            err = MailboxError(f"GitHub {method} {path} -> {e.code}: {detail}")
            err.status = e.code  # type: ignore[attr-defined]
            raise err

    # Branch -----------------------------------------------------------------

    def ensure_branch(self) -> None:
        ref = self.req("GET", f"/repos/{self.repo}/git/ref/heads/{self.branch}", ok404=True)
        if ref:
            return
        # Create an orphan branch holding only a README, so it shares no history with main.
        readme = (
            "# Agent mailbox\n\nMessages and shared state between coding agents. "
            "Written by tools/mailbox/server.py on the main branch; do not merge into main.\n"
        )
        blob = self.req("POST", f"/repos/{self.repo}/git/blobs",
                        {"content": readme, "encoding": "utf-8"})
        tree = self.req("POST", f"/repos/{self.repo}/git/trees",
                        {"tree": [{"path": "README.md", "mode": "100644", "type": "blob",
                                   "sha": blob["sha"]}]})
        commit = self.req("POST", f"/repos/{self.repo}/git/commits",
                          {"message": "mailbox: initialize", "tree": tree["sha"], "parents": []})
        try:
            self.req("POST", f"/repos/{self.repo}/git/refs",
                     {"ref": f"refs/heads/{self.branch}", "sha": commit["sha"]})
        except MailboxError as e:
            if getattr(e, "status", None) != 422:  # 422: someone else created it first
                raise

    # Files ------------------------------------------------------------------

    def list_paths(self, prefix: str) -> list[str]:
        tree = self.req("GET", f"/repos/{self.repo}/git/trees/{self.branch}?recursive=1")
        return sorted(t["path"] for t in tree.get("tree", [])
                      if t["type"] == "blob" and t["path"].startswith(prefix))

    def get_file(self, path: str) -> tuple[dict, str] | None:
        """Return (parsed JSON, sha) or None if missing."""
        q = urllib.parse.quote(path)
        res = self.req("GET", f"/repos/{self.repo}/contents/{q}?ref={self.branch}", ok404=True)
        if not res:
            return None
        content = base64.b64decode(res["content"]).decode()
        return json.loads(content), res["sha"]

    def get_message(self, path: str) -> dict:
        if path not in self.blob_cache:
            got = self.get_file(path)
            if got is None:
                raise MailboxError(f"Message vanished: {path}")
            self.blob_cache[path] = got[0]
        return self.blob_cache[path]

    def put_file(self, path: str, obj: dict, message: str, sha: str | None = None) -> str:
        body = {
            "message": message,
            "content": base64.b64encode((json.dumps(obj, indent=2) + "\n").encode()).decode(),
            "branch": self.branch,
        }
        if sha:
            body["sha"] = sha
        q = urllib.parse.quote(path)
        # Two writers committing to the branch at once can collide on the branch head (409)
        # even for different files; retry those. A stale `sha` on the same file also returns
        # 409, so with a sha, retry only while the file itself is still at that sha.
        for attempt in range(5):
            try:
                res = self.req("PUT", f"/repos/{self.repo}/contents/{q}", body)
                return res["content"]["sha"]
            except MailboxError as e:
                status = getattr(e, "status", None)
                if status == 409 and attempt < 4:
                    current = self.get_file(path) if sha else None
                    if sha is None or (current and current[1] == sha):
                        time.sleep(0.5 * (attempt + 1))
                        continue
                raise
        raise MailboxError("unreachable")


# --------------------------------------------------------------------------- Mailbox


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_agent(gh: GitHub) -> str:
    name = os.environ.get("MAILBOX_AGENT")
    if not name:
        name = gh.req("GET", "/user")["login"]
    name = name.strip().lower()
    if not NAME_RE.match(name):
        raise MailboxError(f"Bad agent name {name!r}: use lowercase letters, digits and dashes.")
    return name


def _parse_msg_path(path: str) -> dict | None:
    # messages/20261024T031255Z_john_all_ab12cd.json
    m = re.match(r"^messages/(\d{8}T\d{6}Z)_([a-z0-9-]+)_([a-z0-9-]+)_([0-9a-f]+)\.json$", path)
    if not m:
        return None
    return {"path": path, "id": path[len("messages/"):-len(".json")],
            "ts": m.group(1), "from": m.group(2), "to": m.group(3)}


class Mailbox:
    def __init__(self):
        self.gh = GitHub(REPO, BRANCH)
        self.gh.ensure_branch()
        self.me = _resolve_agent(self.gh)
        self.state_seen: dict[str, str] = {}  # key -> sha this session last read
        self._registered = False

    # Agents -----------------------------------------------------------------

    def _register(self) -> None:
        if self._registered:
            return
        path = f"agents/{self.me}.json"
        got = self.gh.get_file(path)
        rec = {"name": self.me, "last_seen": _now()}
        try:
            self.gh.put_file(path, rec, f"mailbox: {self.me} seen", sha=got[1] if got else None)
        except MailboxError:
            pass  # presence is best-effort
        self._registered = True

    def known_agents(self) -> list[str]:
        return [p[len("agents/"):-len(".json")] for p in self.gh.list_paths("agents/")]

    # Messages ---------------------------------------------------------------

    def send(self, to: str, kind: str, subject: str, body: str,
             reply_to: str | None, refs: list[str] | None) -> dict:
        self._register()
        to = to.strip().lower()
        if to != "all" and not NAME_RE.match(to):
            raise MailboxError(f"Bad recipient {to!r}.")
        if kind not in KINDS:
            raise MailboxError(f"kind must be one of {', '.join(KINDS)}.")
        if not subject.strip():
            raise MailboxError("subject is required.")
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        msg_id = f"{ts}_{self.me}_{to}_{secrets.token_hex(3)}"
        msg = {
            "id": msg_id, "from": self.me, "to": to, "kind": kind,
            "subject": subject.strip(), "body": body, "reply_to": reply_to,
            "refs": refs or [], "created_at": _now(),
        }
        path = f"messages/{msg_id}.json"
        self.gh.put_file(path, msg, f"mailbox: {self.me} -> {to} [{kind}] {subject.strip()[:60]}")
        self.gh.blob_cache[path] = msg
        return msg

    def _read_set(self) -> tuple[set[str], str | None]:
        got = self.gh.get_file(f"cursors/{self.me}.json")
        return (set(got[0].get("read", [])), got[1]) if got else (set(), None)

    def _addressed_to_me(self) -> list[dict]:
        out = []
        for p in self.gh.list_paths("messages/"):
            meta = _parse_msg_path(p)
            if not meta or meta["to"] not in ("all", self.me):
                continue
            if meta["from"] == self.me and meta["to"] != self.me:
                continue  # my own broadcasts
            out.append(meta)
        return out

    def read(self, unread_only: bool, from_: str | None, kind: str | None, limit: int) -> dict:
        self._register()
        limit = max(1, min(limit, 100))
        read_ids, read_sha = self._read_set()
        mine = self._addressed_to_me()
        pool = [m for m in mine if m["id"] not in read_ids] if unread_only else mine
        if from_:
            pool = [m for m in pool if m["from"] == from_.strip().lower()]
        msgs = [self.gh.get_message(m["path"]) for m in pool]
        if kind:
            msgs = [m for m in msgs if m.get("kind") == kind]
        msgs = msgs[:limit] if unread_only else msgs[-limit:]

        marked = 0
        if unread_only and msgs:
            new_ids = read_ids | {m["id"] for m in msgs}
            try:
                self.gh.put_file(f"cursors/{self.me}.json",
                                 {"agent": self.me, "read": sorted(new_ids), "updated_at": _now()},
                                 f"mailbox: {self.me} read {len(msgs)} message(s)", sha=read_sha)
                read_ids = new_ids
                marked = len(msgs)
            except MailboxError:
                pass  # another session of mine updated it concurrently; they stay unread
        still_unread = sum(1 for m in mine if m["id"] not in read_ids)
        return {"you_are": self.me, "messages": msgs, "marked_read": marked,
                "still_unread": still_unread, "known_agents": self.known_agents()}

    def unread_summary(self) -> dict:
        read_ids, _ = self._read_set()
        return {"you_are": self.me,
                "unread": [m for m in self._addressed_to_me() if m["id"] not in read_ids]}

    # State ------------------------------------------------------------------

    def get_state(self, key: str | None) -> dict:
        self._register()
        if key and not key.endswith("*"):
            _check_key(key)
            got = self.gh.get_file(f"state/{key}.json")
            if not got:
                self.state_seen[key] = ""
                return {"key": key, "exists": False}
            self.state_seen[key] = got[1]
            return {"key": key, "exists": True, **got[0]}
        prefix = "state/" + (key[:-1] if key else "")
        entries = []
        for p in self.gh.list_paths(prefix):
            k = p[len("state/"):-len(".json")]
            got = self.gh.get_file(p)
            if not got:
                continue
            self.state_seen[k] = got[1]
            rec = got[0]
            if key:  # prefix query returns values; bare listing returns metadata only
                entries.append({"key": k, **rec})
            else:
                entries.append({"key": k, "updated_by": rec.get("updated_by"),
                                "updated_at": rec.get("updated_at")})
        return {"you_are": self.me, "entries": entries}

    def set_state(self, key: str, value: str) -> dict:
        self._register()
        _check_key(key)
        path = f"state/{key}.json"
        current = self.gh.get_file(path)
        seen = self.state_seen.get(key)
        if current and seen is None:
            raise MailboxError(
                f"'{key}' already exists and you have not read it this session. "
                f"Call get_state('{key}') first, then set it.")
        if current and seen and current[1] != seen:
            raise MailboxError(
                f"'{key}' was changed by {current[0].get('updated_by')} at "
                f"{current[0].get('updated_at')} since you read it. Call get_state again, "
                f"merge, then set it.")
        if not current and seen:
            raise MailboxError(f"'{key}' was deleted since you read it.")
        rec = {"key": key, "value": value, "updated_by": self.me, "updated_at": _now()}
        try:
            sha = self.gh.put_file(path, rec, f"mailbox: {self.me} set {key}",
                                   sha=current[1] if current else None)
        except MailboxError as e:
            if getattr(e, "status", None) in (409, 422):
                raise MailboxError(f"'{key}' changed while you were writing it. "
                                   f"Call get_state again, merge, then set it.")
            raise
        self.state_seen[key] = sha
        return rec


def _check_key(key: str) -> None:
    if not KEY_RE.match(key) or len(key) > 120:
        raise MailboxError(f"Bad key {key!r}: use segments of letters, digits, . _ - joined by /.")


# --------------------------------------------------------------------------- MCP


def build_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "mailbox",
        instructions=(
            "Async mailbox and shared state board shared with the other coding agents on this "
            "project. Messages are for things that happened; the state board holds the current "
            "truth both sides should agree on (status/<agent>, decisions, interfaces/..., "
            "blockers). At session start call read_messages and get_state('status/*'). After "
            "anything another agent depends on, update status/<you> and send a message."
        ),
    )
    box: dict[str, Mailbox] = {}

    def mb() -> Mailbox:
        if "m" not in box:
            box["m"] = Mailbox()
        return box["m"]

    def run(fn):
        try:
            return fn()
        except MailboxError as e:
            return {"error": str(e)}

    @mcp.tool()
    def send_message(to: str, kind: str, subject: str, body: str = "",
                     reply_to: str | None = None, refs: list[str] | None = None) -> dict:
        """Send an async message to another agent.

        to: an agent name (see known_agents from read_messages) or "all".
        kind: one of info, question, request, decision, blocker.
        subject: one line. body: markdown details.
        reply_to: id of the message this answers, if any.
        refs: related file paths, commit SHAs or URLs.
        """
        return run(lambda: mb().send(to, kind, subject, body, reply_to, refs))

    @mcp.tool()
    def read_messages(unread_only: bool = True, from_agent: str | None = None,
                      kind: str | None = None, limit: int = 20) -> dict:
        """Read messages addressed to you or to "all", oldest first.

        With unread_only (default), returns unread messages and marks exactly those read;
        still_unread says how many remain. Optional filters: from_agent, kind.
        unread_only=false returns the most recent `limit` messages, read or not, and marks nothing.
        """
        return run(lambda: mb().read(unread_only, from_agent, kind, limit))

    @mcp.tool()
    def get_state(key: str | None = None) -> dict:
        """Read the shared state board.

        key: an exact key (e.g. "decisions"), a prefix ending in * (e.g. "status/*") to get
        every matching entry with values, or omit to list all keys with who updated them.
        Reading a key is required before you can overwrite it with set_state.
        """
        return run(lambda: mb().get_state(key))

    @mcp.tool()
    def set_state(key: str, value: str) -> dict:
        """Overwrite one shared state entry with a new value (markdown or JSON text).

        Fails if the key changed since you last read it with get_state, so you never
        clobber another agent's update; re-read, merge, and set again.
        """
        return run(lambda: mb().set_state(key, value))

    return mcp


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        try:
            s = Mailbox().unread_summary()
        except Exception as e:  # never break a hook
            print(f"[mailbox] unavailable: {e}")
            return
        n = len(s["unread"])
        if n:
            senders = sorted({m["from"] for m in s["unread"]})
            print(f"[mailbox] {s['you_are']}: {n} unread message(s) from {', '.join(senders)}. "
                  f"Call the mailbox read_messages tool before starting work.")
        else:
            print(f"[mailbox] {s['you_are']}: no unread messages.")
        return
    build_server().run()


if __name__ == "__main__":
    main()
