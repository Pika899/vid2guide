"""Read a pipeline transcript and check timestamps against it."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_LINE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\]\s?(.*)$")
_TS = re.compile(r"^\[?\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s*\]?$")
# H:MM:SS or HH:MM:SS inside prose (MM:SS alone is too ambiguous to police).
_PROSE_TS = re.compile(r"(?<![\d:])\d{1,2}:\d{2}:\d{2}(?![\d:])")


def to_seconds(ts: str) -> int | None:
    """'01:02:03', '1:02:03', '[01:02:03]', '02:03' -> seconds. None if not a timestamp."""
    m = _TS.match(ts.strip())
    if not m:
        return None
    h, mnt, s = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    if mnt > 59 or s > 59:
        return None
    return h * 3600 + mnt * 60 + s


def fmt(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


@dataclass
class Transcript:
    path: Path
    header: list[str] = field(default_factory=list)
    segments: list[tuple[int, str]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Transcript":
        t = cls(path)
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                t.header.append(line.lstrip("# ").strip())
            elif m := _LINE.match(line):
                secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                t.segments.append((secs, m.group(4)))
        return t

    @property
    def stamps(self) -> set[int]:
        return {s for s, _ in self.segments}

    @property
    def last_second(self) -> int:
        return self.segments[-1][0] if self.segments else 0

    def header_value(self, key: str) -> str | None:
        """e.g. header_value('Modello') -> 'whisper-small' from '# Modello: whisper-small | ...'."""
        for line in self.header:
            for part in line.split("|"):
                k, _, v = part.partition(":")
                if k.strip() == key:
                    return v.strip()
        return None


def validate_guide(guide: dict, transcript: Transcript) -> tuple[dict, list[str]]:
    """Keep only timestamps that exist in the transcript, normalised to HH:MM:SS.

    Nothing is moved to a "nearby" time: a reference that does not match a segment
    is dropped and reported, because a guessed timestamp is worse than none.
    """
    stamps = transcript.stamps
    dropped: list[str] = []

    def check(ts: str, where: str) -> str | None:
        secs = to_seconds(ts or "")
        if secs is not None and secs in stamps:
            return fmt(secs)
        dropped.append(f"{where}: «{ts}»")
        return None

    out = dict(guide)
    for key, label in (("index", "indice"), ("screen_prompts", "prompt a schermo"),
                       ("frameworks", "framework"), ("to_verify", "da verificare")):
        kept = []
        for item in guide.get(key, []):
            ts = check(item.get("timestamp", ""), label)
            if ts:
                kept.append({**item, "timestamp": ts})
        kept.sort(key=lambda i: to_seconds(i["timestamp"]))
        out[key] = kept

    # Timestamps written inside free text get the same check: a valid one stays,
    # an invented one is replaced, never nudged to a nearby segment.
    def prose(text: str, where: str) -> str:
        def repl(m: re.Match) -> str:
            secs = to_seconds(m.group(0))
            if secs is not None and secs in stamps:
                return m.group(0)
            dropped.append(f"{where}: «{m.group(0)}» nel testo")
            return "(riferimento rimosso)"
        return _PROSE_TS.sub(repl, text or "")

    for key in ("summary", "correction_note", "title"):
        out[key] = prose(guide.get(key, ""), key)
    for key, fields in (("index", ("topic",)), ("screen_prompts", ("title", "spoken_context")),
                        ("frameworks", ("title", "content")), ("to_verify", ("note",))):
        out[key] = [{**i, **{f: prose(i.get(f, ""), key) for f in fields}} for i in out[key]]

    concepts = []
    for c in guide.get("concepts", []):
        c = {**c, "title": prose(c.get("title", ""), "concetto"),
             "explanation": prose(c.get("explanation", ""), "concetto")}
        good = sorted({t for t in (check(ts, f"concetto «{c.get('title', '')}»")
                                   for ts in c.get("timestamps", [])) if t},
                      key=to_seconds)
        if good:
            concepts.append({**c, "timestamps": good})
        else:
            dropped.append(f"concetto senza riferimenti validi: «{c.get('title', '')}»")
    out["concepts"] = concepts
    return out, dropped
