import math
import re
from collections import Counter, deque
from dataclasses import dataclass, field

HANDOVER_LIMIT = 1500
KEPT_HANDOVERS = 4
SHOWN_QUESTIONS = 5
MAX_RECALL = 10
NONE_WRITTEN = "(none written)"
STOPWORDS = frozenset(
    "a an and are as at be by did do does for from has have how in is it its of on or our "
    "that the this to was were what when which who why will with you your".split()
)


@dataclass(frozen=True)
class SavedAnswer:
    date: str
    week: int
    turn: str
    question: str
    answer: str


@dataclass(frozen=True)
class Handover:
    date: str
    week: int
    turn: str
    text: str


def tokens(text: str) -> set[str]:
    # A trailing-s strip lets "croissants" meet "croissant" without a stemmer.
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words} - STOPWORDS


@dataclass
class Archive:
    answers: list[SavedAnswer] = field(default_factory=list)
    handovers: deque[Handover] = field(default_factory=lambda: deque(maxlen=KEPT_HANDOVERS))

    def recall(self, query: str, limit: int = 5) -> list[SavedAnswer]:
        docs = [tokens(a.question + " " + a.answer) for a in self.answers]
        df = Counter(t for d in docs for t in d)
        idf = {t: math.log((1 + len(docs)) / (1 + n)) + 1 for t, n in df.items()}
        wanted = tokens(query)
        scored = [(sum(idf[t] for t in wanted & d), n) for n, d in enumerate(docs) if wanted & d]
        best = sorted(scored, key=lambda s: (-s[0], -s[1]))[:limit]
        return [self.answers[n] for _, n in best]

    def recent_questions(self) -> tuple[str, ...]:
        return tuple(a.question for a in self.answers[-SHOWN_QUESTIONS:])
