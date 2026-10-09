"""JSON schemas for structured output. Written to be valid for both providers'
strict modes: every object closed, every property required, no string limits."""

TS = {"type": "string", "description": "Timestamp HH:MM:SS copied from the transcript"}


def _obj(**props) -> dict:
    return {"type": "object", "additionalProperties": False,
            "required": list(props), "properties": props}


def _arr(item: dict) -> dict:
    return {"type": "array", "items": item}


GUIDE_SCHEMA = _obj(
    title={"type": "string"},
    summary={"type": "string"},
    correction_note={"type": "string"},
    index=_arr(_obj(timestamp=TS, topic={"type": "string"})),
    concepts=_arr(_obj(title={"type": "string"}, timestamps=_arr(TS),
                       explanation={"type": "string"})),
    screen_prompts=_arr(_obj(timestamp=TS, title={"type": "string"},
                             spoken_context={"type": "string"})),
    frameworks=_arr(_obj(timestamp=TS, title={"type": "string"},
                         content={"type": "string"})),
    to_verify=_arr(_obj(timestamp=TS, note={"type": "string"})),
)

VISION_SCHEMA = _obj(
    best_frame={"type": "integer", "description": "1-based number of the chosen frame, 0 if none shows it"},
    visibility={"type": "string", "enum": ["complete", "partial", "illegible", "not_visible"]},
    text={"type": "string"},
    note={"type": "string"},
)
