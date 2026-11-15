"""News scoring.

Two scorers share one interface: score(headlines) -> DataFrame[sentiment, magnitude, event].

* LexiconScorer: fast, free, deterministic. Finance-specific phrases (Loughran-McDonald style)
  with negation handling. Good baseline and what the backtest uses by default.
* ClaudeScorer: sends headlines in batches to Claude with a strict JSON schema. Much better on
  nuance ("beats but guides down", "acquired at a premium" vs "acquires"). Enable with
  STOCKGPT_LLM_SCORING=1 and an Anthropic credential.
"""
from __future__ import annotations

import re
from typing import Iterable

import numpy as np
import pandas as pd

# (phrase, weight, event_type). Longer phrases are matched first.
_LEX: list[tuple[str, float, str]] = [
    # guidance / earnings
    ("raises full-year guidance", 1.0, "guidance"), ("raises outlook", 0.9, "guidance"),
    ("raises guidance", 0.9, "guidance"), ("cuts full-year guidance", -1.0, "guidance"),
    ("lowers guidance", -0.9, "guidance"), ("cuts guidance", -0.9, "guidance"),
    ("soft outlook", -0.6, "guidance"), ("maintains guidance", 0.0, "guidance"),
    ("beats on earnings", 0.9, "earnings"), ("tops estimates", 0.8, "earnings"),
    ("beats street expectations", 0.8, "earnings"), ("beats expectations", 0.7, "earnings"),
    ("misses earnings", -0.9, "earnings"), ("misses revenue", -0.8, "earnings"),
    ("falls short", -0.7, "earnings"), ("weak quarter", -0.7, "earnings"),
    ("in line with estimates", 0.0, "earnings"), ("record quarterly revenue", 0.8, "earnings"),
    ("margins expand", 0.4, "earnings"), ("margins under pressure", -0.5, "earnings"),
    ("warns of losses", -0.8, "earnings"), ("strong quarter", 0.7, "earnings"),
    # analysts
    ("upgraded to buy", 0.8, "analyst"), ("upgraded", 0.6, "analyst"),
    ("downgraded to sell", -0.8, "analyst"), ("downgraded", -0.6, "analyst"),
    ("price target raised", 0.5, "analyst"), ("trims price target", -0.4, "analyst"),
    ("reiterates outperform", 0.3, "analyst"), ("sees upside", 0.3, "analyst"),
    # corporate actions
    ("to be acquired", 1.0, "m&a"), ("premium", 0.3, "m&a"),
    ("share buyback", 0.6, "capital"), ("dividend increase", 0.4, "capital"),
    ("multi-billion dollar contract", 0.9, "contract"), ("landing", 0.2, "contract"),
    ("expands partnership", 0.4, "partnership"), ("wins approval", 0.5, "regulatory"),
    ("fda approves", 1.0, "regulatory"), ("fda rejects", -1.0, "regulatory"),
    ("opens investigation", -0.9, "legal"), ("sec ", -0.3, "legal"), ("lawsuit", -0.7, "legal"),
    ("recalls", -0.8, "product"), ("delays", -0.5, "product"), ("launches", 0.3, "product"),
    ("to step down", -0.4, "management"), ("appoints", 0.0, "management"),
    # generic tone
    ("surges", 0.6, "price"), ("plunge", -0.7, "price"), ("shares gain", 0.4, "price"),
    ("shares slip", -0.4, "price"), ("shares drop", -0.5, "price"),
    ("strong demand", 0.5, "demand"), ("robust", 0.4, "demand"), ("weaker demand", -0.6, "demand"),
    ("slowing growth", -0.5, "demand"), ("competition intensifies", -0.4, "competition"),
    ("supply chain delays", -0.5, "supply"), ("disappoints", -0.7, "earnings"),
    ("deteriorating", -0.6, "fundamental"), ("solid growth", 0.5, "demand"),
    ("beats", 0.6, "earnings"), ("misses", -0.6, "earnings"),
]
_LEX.sort(key=lambda x: -len(x[0]))
_NEG = re.compile(r"\b(not|no|fails to|without|denies)\b")


class LexiconScorer:
    name = "lexicon"

    def score_one(self, text: str) -> tuple[float, float, str]:
        t = " " + text.lower() + " "
        total, hits, event, used = 0.0, 0, "other", []
        for phrase, w, ev in _LEX:
            pos = t.find(phrase)
            if pos >= 0 and not any(a <= pos < b for a, b in used):
                window = t[max(0, pos - 20):pos]
                sign = -1.0 if _NEG.search(window) else 1.0
                total += sign * w
                hits += 1
                used.append((pos, pos + len(phrase)))
                if event == "other" and w != 0:
                    event = ev
        if hits == 0:
            return 0.0, 0.0, "other"
        s = float(np.tanh(total))
        return s, float(min(1.0, abs(total))), event

    def score(self, headlines: Iterable[str]) -> pd.DataFrame:
        out = [self.score_one(h) for h in headlines]
        return pd.DataFrame(out, columns=["sentiment", "magnitude", "event"])


class ClaudeScorer:
    """Batch-score headlines with Claude using structured outputs.

    Each request carries up to `headlines_per_request` headlines. The system prompt is fixed
    so it is cached across requests. For the nightly backfill of history use the Message
    Batches API (50% cheaper) via `score_batch_async`.
    """
    name = "claude"

    SYSTEM = (
        "You are an equity news analyst. For each headline, estimate the likely direction and "
        "size of the stock's price reaction over the next trading session for the named "
        "company. sentiment is in [-1, 1] (-1 = strongly negative for the stock, +1 = strongly "
        "positive). magnitude is in [0, 1]: how material the news is (routine conference "
        "appearances ~0, guidance changes, M&A, regulatory decisions ~1). event is one short "
        "lowercase category such as earnings, guidance, analyst, m&a, regulatory, legal, "
        "product, management, capital, contract, macro, other. Judge the news itself, not "
        "prior price moves. Return one result per input id."
    )

    def __init__(self, model: str | None = None, per_request: int = 40):
        import anthropic  # optional dependency
        from pydantic import BaseModel

        from ..config import SETTINGS

        class Item(BaseModel):
            id: int
            sentiment: float
            magnitude: float
            event: str

        class Result(BaseModel):
            items: list[Item]

        self._Result = Result
        self.client = anthropic.Anthropic()
        self.model = model or SETTINGS.llm.model
        self.per_request = per_request

    def _score_chunk(self, chunk: list[str]) -> dict[int, tuple[float, float, str]]:
        body = "\n".join(f"{i}\t{h}" for i, h in enumerate(chunk))
        resp = self.client.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=[{"type": "text", "text": self.SYSTEM, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": f"Headlines (id<TAB>text):\n{body}"}],
            output_format=self._Result,
        )
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            return {}
        return {it.id: (float(np.clip(it.sentiment, -1, 1)), float(np.clip(it.magnitude, 0, 1)),
                        it.event) for it in resp.parsed_output.items}

    def score(self, headlines: Iterable[str]) -> pd.DataFrame:
        hs = list(headlines)
        fallback = LexiconScorer()
        out: list[tuple[float, float, str]] = []
        for start in range(0, len(hs), self.per_request):
            chunk = hs[start:start + self.per_request]
            try:
                got = self._score_chunk(chunk)
            except Exception:  # network/rate-limit: degrade to lexicon for this chunk
                got = {}
            out.extend(got.get(i) or fallback.score_one(h) for i, h in enumerate(chunk))
        return pd.DataFrame(out, columns=["sentiment", "magnitude", "event"])


def get_scorer():
    from ..config import SETTINGS
    if SETTINGS.llm.enabled:
        try:
            return ClaudeScorer(per_request=SETTINGS.llm.headlines_per_request)
        except Exception:
            pass
    return LexiconScorer()
