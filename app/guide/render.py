"""Validated guide + screenshots -> Markdown (same layout as the analyze-course skill)."""
from __future__ import annotations


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _fence(text: str) -> str:
    fence = "~~~~" if "```" in text else "```"
    return f"{fence}\n{text.rstrip()}\n{fence}"


def render(guide: dict, *, source: str, duration: str, whisper_model: str,
           generated_by: str, shots: list[dict], dropped: list[str]) -> str:
    out: list[str] = [f"# {guide.get('title') or 'Guida di studio'}", ""]
    out.append(f"> Fonte: `{source}` — durata {duration}, trascrizione {whisper_model}. "
               f"Guida generata con {generated_by}.")
    out.append("> Ogni timestamp rimanda al video originale.")
    if guide.get("correction_note"):
        out += ["", f"> **Nota sulla trascrizione:** {guide['correction_note']}"]
    out += ["", "## In breve", "", guide.get("summary", "").strip(), ""]

    out += ["## Indice cronologico", "", "| Timestamp | Argomento |", "|---|---|"]
    for item in guide.get("index", []):
        out.append(f"| `[{item['timestamp']}]` | {_cell(item['topic'])} |")
    out.append("")

    out += ["## Concetti tecnici", ""]
    concepts = guide.get("concepts", [])
    if not concepts:
        out += ["Nessun concetto tecnico spiegato in questa lezione.", ""]
    for c in concepts:
        first, *rest = c["timestamps"]
        out += [f"### {c['title']} `[{first}]`", "", c["explanation"].strip()]
        if rest:
            out += ["", "Ci torna anche a " + ", ".join(f"`[{t}]`" for t in rest) + "."]
        out.append("")

    out += ["## Prompt e framework mostrati", ""]
    if not shots and not guide.get("frameworks"):
        out += ["Nessuno mostrato in questa lezione.", ""]
    for s in shots:
        out += [f"### {s['title']} `[{s['timestamp']}]`", ""]
        if s.get("context"):
            out += [s["context"].strip(), ""]
        if s.get("image"):
            out += [f"![{_cell(s['title'])}]({s['image']})", ""]
        vis = s.get("visibility")
        if vis in ("complete", "partial") and s.get("text", "").strip():
            out += [_fence(s["text"]), ""]
            if vis == "partial":
                out += ["> Prompt visibile solo in parte: trascritto per quanto leggibile.", ""]
        elif vis == "illegible":
            out += ["> Il testo del prompt non è leggibile nel fotogramma: guardalo nel video "
                    "a questo minuto.", ""]
        elif vis == "not_visible":
            out += ["> Il prompt non compare nei fotogrammi estratti: guardalo nel video a "
                    "questo minuto.", ""]
        elif vis == "skipped":
            out += [f"> {s.get('note') or 'Screenshot non disponibile.'}", ""]
        if s.get("note") and vis != "skipped":
            out += [f"*{s['note'].strip()}*", ""]
    for f in guide.get("frameworks", []):
        out += [f"### {f['title']} `[{f['timestamp']}]`", "", f["content"].strip(), ""]

    verify = guide.get("to_verify", [])
    if verify:
        out += ["## Da verificare", ""]
        out += [f"- `[{v['timestamp']}]` {v['note'].strip()}" for v in verify]
        out.append("")

    if dropped:
        out += ["---", "",
                f"*Nota tecnica: {len(dropped)} riferimenti proposti dal modello non "
                "corrispondevano a nessun punto della trascrizione e sono stati rimossi, "
                "invece di essere spostati a un minuto stimato.*", ""]
    return "\n".join(out).rstrip() + "\n"
