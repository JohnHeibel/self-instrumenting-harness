"""read_docs: search and read the host's documentation.

The host config lists sources under [docs]; nothing here knows any particular host:

    [docs]
    sources = [
      { name = "api", url = "https://example.com/api.json", format = "json" },
      { name = "guide", url = "https://example.com/guide.html", format = "html" },
      { name = "notes", path = "{config_dir}/myhost/docs", format = "markdown" },
    ]

Each source is fetched once per run (lazily, on the first read_docs call), cached under
the run directory, and split into sections:
  html      a section per heading (h1-h4)
  markdown  a section per # heading (also used for .txt; files without headings become one section)
  json      every object with a "name" becomes a section, titled by the dotted path of names
            leading to it (e.g. "Entity.status"); its "description" and other scalar fields are
            the text. This fits most machine-readable API references.
A `path` may be a file or a directory (all files in it). Search is BM25 over titles and text.
"""

from __future__ import annotations

import html.parser
import json
import math
import re
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

FORMATS = ("html", "markdown", "json")
MAX_SECTION_CHARS = 12_000
FETCH_TIMEOUT_S = 60


@dataclass
class Section:
    id: str          # "<source>:<title>", unique
    source: str
    title: str
    text: str


@dataclass
class Docs:
    sources: list[dict]
    cache_dir: Path
    sections: list[Section] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    _loaded: bool = False
    _index: list[Counter] = field(default_factory=list)
    _df: Counter = field(default_factory=Counter)
    _avg_len: float = 1.0

    # ------------------------------------------------------------------ loading

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        seen: set[str] = set()
        for src in self.sources:
            try:
                for title, text in _split(_read(src, self.cache_dir), src["format"]):
                    sid = f"{src['name']}:{title}"
                    n = 2
                    while sid in seen:
                        sid, n = f"{src['name']}:{title} ({n})", n + 1
                    seen.add(sid)
                    self.sections.append(Section(sid, src["name"], title, text))
            except Exception as e:  # one bad source must not hide the others
                self.errors[src["name"]] = f"{type(e).__name__}: {e}"
        self._index = [Counter(_tokens(s.title) * 3 + _tokens(s.text)) for s in self.sections]
        for counts in self._index:
            self._df.update(counts.keys())
        self._avg_len = sum(sum(c.values()) for c in self._index) / max(1, len(self._index))

    # ------------------------------------------------------------------ the tool

    def overview(self) -> str:
        self.load()
        if not self.sources:
            return "This host has no documentation configured."
        lines = ["Documentation sources (search with read_docs(query=...), read with read_docs(section=...)):"]
        for src in self.sources:
            n = sum(1 for s in self.sections if s.source == src["name"])
            where = src.get("url") or src.get("path")
            err = f"  UNAVAILABLE: {self.errors[src['name']]}" if src["name"] in self.errors else ""
            lines.append(f"- {src['name']} ({src['format']}, {n} sections) {where}{err}")
        return "\n".join(lines)

    def search(self, query: str, source: str = "", limit: int = 8) -> list[tuple[float, Section]]:
        self.load()
        terms = _tokens(query)
        if not terms:
            return []
        n = len(self.sections)
        scored = []
        for s, counts in zip(self.sections, self._index):
            if source and s.source != source:
                continue
            length = sum(counts.values())
            score = 0.0
            for t in terms:
                f = counts.get(t, 0)
                if f:
                    idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                    score += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * length / self._avg_len))
            if query.lower() in s.title.lower():
                score += 5  # an exact name match should come first
            if score > 0:
                scored.append((score, s))
        scored.sort(key=lambda x: -x[0])
        return scored[:limit]

    def section(self, sid: str) -> Section | None:
        self.load()
        for s in self.sections:
            if s.id == sid:
                return s
        lowered = sid.lower()
        matches = [s for s in self.sections if s.id.lower() == lowered or s.title.lower() == lowered]
        return matches[0] if len(matches) == 1 else None


def answer(docs: Docs, query: str, section: str, source: str, limit: int) -> tuple[str, str]:
    """read_docs' reply and a one-line summary for the event log."""
    if section:
        s = docs.section(section)
        if s:
            return f"[{s.id}]\n{s.text}", f"read {s.id}"
        near = docs.search(section, limit=5)
        hint = "\n".join(f"- {x.id}" for _, x in near)
        raise ValueError(f"no section {section!r}." + (f" Closest:\n{hint}" if hint else ""))
    if query:
        if source and source not in {s["name"] for s in docs.sources}:
            raise ValueError(f"no source {source!r}; read_docs() lists them")
        hits = docs.search(query, source, limit)
        if not hits:
            return f"No sections match {query!r}." + _unavailable(docs), f"no results for {query!r}"
        lines = [f"Sections matching {query!r} (read one with read_docs(section=<id>)):"]
        for _, s in hits:
            body = s.text[len(s.title):] if s.text.startswith(s.title) else s.text
            lines.append(f"- {s.id}\n    {' '.join(body.split())[:240]}")
        return "\n".join(lines) + _unavailable(docs), f"{len(hits)} results for {query!r}"
    return docs.overview(), "listed documentation sources"


def _unavailable(docs: Docs) -> str:
    return "".join(f"\n(Source {n} is unavailable: {e})" for n, e in docs.errors.items())


def parse_sources(raw: dict, fill) -> list[dict]:
    """[docs] from a host config -> validated sources. `fill` expands {config_dir} etc."""
    out, names = [], set()
    for i, s in enumerate(raw.get("sources", [])):
        where = f"host config: [docs] sources[{i}]"
        if not isinstance(s, dict) or not isinstance(s.get("name"), str) or not s["name"]:
            raise ValueError(f"{where} needs a name")
        if s["name"] in names:
            raise ValueError(f"{where}: name {s['name']!r} appears twice")
        names.add(s["name"])
        if s.get("format") not in FORMATS:
            raise ValueError(f"{where}: format must be one of {', '.join(FORMATS)}")
        if bool(s.get("url")) == bool(s.get("path")):
            raise ValueError(f"{where}: give exactly one of url or path")
        src = {"name": s["name"], "format": s["format"]}
        if s.get("url"):
            src["url"] = fill(s["url"])
        else:
            src["path"] = fill(s["path"])
        out.append(src)
    return out


# --------------------------------------------------------------------------- reading sources

def _read(src: dict, cache_dir: Path) -> list[tuple[str, str]]:
    """[(file name, content)] for a source, fetching URLs into the cache once."""
    if "url" in src:
        cached = cache_dir / f"{src['name']}.{ {'markdown': 'md'}.get(src['format'], src['format'])}"
        if not cached.exists():
            cache_dir.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(src["url"], headers={"User-Agent": "harness-read-docs"})
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as r:
                cached.write_bytes(r.read())
        return [(cached.name, cached.read_text(encoding="utf-8", errors="replace"))]
    p = Path(src["path"])
    if p.is_dir():
        files = sorted(f for f in p.rglob("*") if f.is_file())
        return [(f.relative_to(p).as_posix(), f.read_text(encoding="utf-8", errors="replace")) for f in files]
    if p.is_file():
        return [(p.name, p.read_text(encoding="utf-8", errors="replace"))]
    raise FileNotFoundError(f"no file or directory at {p}")


def _split(files: list[tuple[str, str]], fmt: str) -> list[tuple[str, str]]:
    out = []
    for name, content in files:
        parts = {"html": _split_html, "markdown": _split_markdown, "json": _split_json}[fmt](content)
        for t, x in parts or [("", content)]:
            t = t.strip()[:200]
            label = (f"{name} > {t}" if len(files) > 1 else t) if t else name
            if x.strip():
                out.append((label, x.strip()[:MAX_SECTION_CHARS]))
    return out


def _split_markdown(text: str) -> list[tuple[str, str]]:
    out, title, buf = [], "", []
    for line in text.splitlines():
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            if buf and "".join(buf).strip():
                out.append((title, "\n".join(buf)))
            title, buf = m.group(2), [line]
        else:
            buf.append(line)
    if buf and "".join(buf).strip():
        out.append((title, "\n".join(buf)))
    return out


class _HTML(html.parser.HTMLParser):
    SKIP = {"script", "style", "nav", "footer", "header"}
    BLOCK = {"p", "div", "li", "tr", "br", "pre", "section", "article", "table", "dt", "dd"}

    def __init__(self) -> None:
        super().__init__()
        self.sections: list[tuple[str, list[str]]] = [("", [])]
        self._skip = 0
        self._heading: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in ("h1", "h2", "h3", "h4"):
            self._heading = []
        elif tag in self.BLOCK:
            self.sections[-1][1].append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in ("h1", "h2", "h3", "h4") and self._heading is not None:
            title = " ".join("".join(self._heading).split())
            self.sections.append((title, [title, "\n"]))
            self._heading = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._heading is not None:
            self._heading.append(data)
        else:
            self.sections[-1][1].append(data)


def _split_html(text: str) -> list[tuple[str, str]]:
    p = _HTML()
    p.feed(text)
    return [(t, re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", "".join(b)))) for t, b in p.sections]


def _split_json(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    def walk(node, path: list[str]) -> None:
        if isinstance(node, dict):
            here = path + [node["name"]] if isinstance(node.get("name"), str) else path
            if isinstance(node.get("name"), str):
                lines = [".".join(here)]
                for k, v in node.items():
                    if isinstance(v, (str, int, float, bool)) and k != "name" and v != "":
                        lines.append(f"{k}: {v}")
                    elif isinstance(v, list) and v and all(isinstance(x, (str, int, float)) for x in v):
                        lines.append(f"{k}: {', '.join(map(str, v))}")
                    elif isinstance(v, (dict, list)) and k in ("type", "parameters", "return_values", "args"):
                        lines.append(f"{k}: {_brief(v)}")
                out.append((".".join(here), "\n".join(lines)))
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v, here)
        elif isinstance(node, list):
            for v in node:
                walk(v, path)

    walk(json.loads(text), [])
    return out


def _brief(v) -> str:
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= 600 else s[:600] + "…"


def _tokens(text: str) -> list[str]:
    """Lowercase words, with dotted, snake_case and CamelCase names also split into parts."""
    words = re.findall(r"[A-Za-z0-9_.]+", text)
    out = []
    for w in words:
        lw = w.lower().strip(".")
        if not lw:
            continue
        out.append(lw)
        parts = re.split(r"[._]|(?<=[a-z0-9])(?=[A-Z])", w)
        if len(parts) > 1:
            out += [p.lower() for p in parts if p]
    return out
