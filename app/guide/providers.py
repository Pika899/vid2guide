"""Model providers behind one small interface.

Two operations are all the guide needs:
  structured(system, text, schema)    -> JSON matching the schema
  vision(system, parts, schema)       -> JSON matching the schema, from text + images
Each returns (data, Usage). Failures raise ProviderError with a message meant
for the person using the app, in Italian.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path

PRICING = json.loads((Path(__file__).with_name("pricing.json")).read_text(encoding="utf-8"))


class ProviderError(RuntimeError):
    pass


@dataclass
class Usage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def cost(self) -> float | None:
        p = _rates(self.model, self.input_tokens)
        if p is None:
            return None
        return (self.input_tokens * p["input"] + self.output_tokens * p["output"]) / 1e6

    @property
    def may_cost_more(self) -> bool:
        """True when a long-request price exists but its threshold is not published."""
        p = _price(self.model)
        return bool(p and "long" in p and p["long"]["above_input_tokens"] is None)


def _price(model: str) -> dict | None:
    for table in PRICING.values():
        if isinstance(table, dict) and model in table:
            return table[model]
    return None


def _rates(model: str, input_tokens: int) -> dict | None:
    """Prices for one request: the long-request tier applies above its threshold."""
    p = _price(model)
    if p is None:
        return None
    long = p.get("long")
    if long and long["above_input_tokens"] is not None and input_tokens > long["above_input_tokens"]:
        return long
    return p


def models_for(provider: str) -> list[dict]:
    table = PRICING.get(provider, {})
    return [{"id": k, "label": v["label"], "input": v["input"], "output": v["output"]}
            for k, v in table.items() if not k.startswith("_")]


def input_cost(model: str, tokens: int) -> float | None:
    p = _rates(model, tokens)
    return None if p is None else tokens * p["input"] / 1e6


def output_price(model: str, input_tokens: int = 0) -> float | None:
    p = _rates(model, input_tokens)
    return None if p is None else p["output"]


# A "part" for vision calls is either a str (text) or bytes (a JPEG image).
Part = str | bytes


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ProviderError("La risposta del modello non è in un formato leggibile. Riprova.") from exc


class AnthropicProvider:
    name = "anthropic"
    # Server-side fallback re-runs a declined request on another model instead
    # of returning the refusal (header and "default" form go together).
    _BETAS = ["server-side-fallback-2026-07-01"]

    def __init__(self, api_key: str, model: str):
        import anthropic
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=4)
        self.model = model

    def _errors(self, exc: Exception) -> ProviderError:
        a = self._anthropic
        if isinstance(exc, a.AuthenticationError):
            return ProviderError("Chiave Anthropic non valida. Controllala in Impostazioni.")
        if isinstance(exc, a.PermissionDeniedError):
            return ProviderError("La chiave Anthropic non ha accesso a questo modello.")
        if isinstance(exc, a.NotFoundError):
            return ProviderError(f"Modello «{self.model}» non disponibile per questa chiave.")
        if isinstance(exc, a.RateLimitError):
            return ProviderError("Limite di richieste Anthropic raggiunto. Aspetta un minuto e riprova.")
        if isinstance(exc, a.BadRequestError):
            msg = str(getattr(exc, "message", exc))
            if "credit" in msg.lower() or "billing" in msg.lower():
                return ProviderError("Credito Anthropic esaurito: ricaricalo su console.anthropic.com.")
            return ProviderError(f"Richiesta rifiutata da Anthropic: {msg}")
        if isinstance(exc, a.APIConnectionError):
            return ProviderError("Nessuna connessione con Anthropic. Controlla internet e riprova.")
        if isinstance(exc, a.APIStatusError):
            return ProviderError(f"Anthropic non risponde correttamente (errore {exc.status_code}). Riprova più tardi.")
        return ProviderError(str(exc))

    def _call(self, system: str, content: list[dict], schema: dict, max_tokens: int, effort: str):
        params = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            output_config={"effort": effort,
                           "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": content}],
        )
        try:
            # Haiku has no server-side refusal fallback: send it the plain request.
            if self.model.startswith("claude-haiku"):
                stream_ctx = self.client.messages.stream(**params)
            else:
                stream_ctx = self.client.beta.messages.stream(
                    **params, betas=self._BETAS, fallbacks="default")
            with stream_ctx as stream:
                msg = stream.get_final_message()
        except self._anthropic.AnthropicError as exc:
            raise self._errors(exc) from exc

        if msg.stop_reason == "refusal":
            raise ProviderError("Il modello ha rifiutato di elaborare questo contenuto.")
        if msg.stop_reason == "max_tokens":
            raise ProviderError("La risposta del modello è stata troncata perché troppo lunga.")
        text = next((b.text for b in msg.content if b.type == "text"), "")
        usage = Usage(msg.model, msg.usage.input_tokens or 0, msg.usage.output_tokens or 0)
        return _parse_json(text), usage

    def structured(self, system: str, text: str, schema: dict, max_tokens: int = 64000):
        return self._call(system, [{"type": "text", "text": text}], schema, max_tokens, "high")

    def vision(self, system: str, parts: list[Part], schema: dict, max_tokens: int = 8000):
        content = []
        for part in parts:
            if isinstance(part, bytes):
                content.append({"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(part).decode("ascii")}})
            else:
                content.append({"type": "text", "text": part})
        return self._call(system, content, schema, max_tokens, "medium")

    def count_tokens(self, system: str, text: str) -> int | None:
        try:
            r = self.client.messages.count_tokens(
                model=self.model, system=system,
                messages=[{"role": "user", "content": text}])
            return r.input_tokens
        except self._anthropic.AnthropicError as exc:
            raise self._errors(exc) from exc

    def verify(self) -> None:
        """Cheapest possible check that the key works: list models, no generation."""
        try:
            self.client.models.list(limit=1)
        except self._anthropic.AnthropicError as exc:
            raise self._errors(exc) from exc


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str):
        import openai
        self._openai = openai
        # Long transcripts with high reasoning effort can take many minutes.
        self.client = openai.OpenAI(api_key=api_key, max_retries=4, timeout=1800)
        self.model = model

    def _errors(self, exc: Exception) -> ProviderError:
        o = self._openai
        if isinstance(exc, o.AuthenticationError):
            return ProviderError("Chiave OpenAI non valida. Controllala in Impostazioni.")
        if isinstance(exc, o.PermissionDeniedError):
            return ProviderError("La chiave OpenAI non ha accesso a questo modello.")
        if isinstance(exc, o.NotFoundError):
            return ProviderError(f"Modello «{self.model}» non disponibile per questa chiave.")
        if isinstance(exc, o.RateLimitError):
            msg = str(getattr(exc, "message", exc)).lower()
            if "quota" in msg or "billing" in msg:
                return ProviderError("Credito OpenAI esaurito: ricaricalo su platform.openai.com.")
            return ProviderError("Limite di richieste OpenAI raggiunto. Aspetta un minuto e riprova.")
        if isinstance(exc, o.BadRequestError):
            return ProviderError(f"Richiesta rifiutata da OpenAI: {getattr(exc, 'message', exc)}")
        if isinstance(exc, o.APIConnectionError):
            return ProviderError("Nessuna connessione con OpenAI. Controlla internet e riprova.")
        if isinstance(exc, o.APIStatusError):
            return ProviderError(f"OpenAI non risponde correttamente (errore {exc.status_code}). Riprova più tardi.")
        return ProviderError(str(exc))

    def _call(self, system: str, content: list[dict], schema: dict, max_tokens: int, effort: str):
        try:
            r = self.client.responses.create(
                model=self.model,
                instructions=system,
                input=[{"role": "user", "content": content}],
                max_output_tokens=max_tokens,
                reasoning={"effort": effort},
                text={"format": {"type": "json_schema", "name": "result",
                                 "schema": schema, "strict": True}},
            )
        except self._openai.OpenAIError as exc:
            raise self._errors(exc) from exc

        if r.status == "incomplete":
            reason = getattr(r.incomplete_details, "reason", "")
            if reason == "max_output_tokens":
                raise ProviderError("La risposta del modello è stata troncata perché troppo lunga.")
            raise ProviderError("Il modello non ha completato la risposta (filtro sui contenuti).")
        for item in r.output or []:
            for c in getattr(item, "content", None) or []:
                if getattr(c, "type", "") == "refusal":
                    raise ProviderError("Il modello ha rifiutato di elaborare questo contenuto.")
        usage = Usage(r.model or self.model, r.usage.input_tokens or 0, r.usage.output_tokens or 0)
        return _parse_json(r.output_text), usage

    def structured(self, system: str, text: str, schema: dict, max_tokens: int = 64000):
        return self._call(system, [{"type": "input_text", "text": text}], schema, max_tokens, "high")

    def vision(self, system: str, parts: list[Part], schema: dict, max_tokens: int = 8000):
        content = []
        for part in parts:
            if isinstance(part, bytes):
                content.append({"type": "input_image", "detail": "high",
                                "image_url": "data:image/jpeg;base64,"
                                + base64.standard_b64encode(part).decode("ascii")})
            else:
                content.append({"type": "input_text", "text": part})
        return self._call(system, content, schema, max_tokens, "medium")

    def count_tokens(self, system: str, text: str) -> int | None:
        return None    # no free counting endpoint used here: the caller estimates

    def verify(self) -> None:
        try:
            self.client.models.list()
        except self._openai.OpenAIError as exc:
            raise self._errors(exc) from exc


def make(provider: str, api_key: str, model: str):
    if provider == "anthropic":
        return AnthropicProvider(api_key, model)
    if provider == "openai":
        return OpenAIProvider(api_key, model)
    raise ProviderError(f"Provider sconosciuto: {provider}")
