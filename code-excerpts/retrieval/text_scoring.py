# Source excerpt; private application dependencies are intentionally omitted.
import re
def _chars(text: str) -> set[str]:
    return {c for c in text.lower() if "\u4e00" <= c <= "\u9fff" or c.isalnum()}

def _light_rerank_score(query: str, content: str, title: str = "") -> float:
    q = _chars(query)
    if not q:
        return 0.0
    c = _chars(content + title)
    overlap = len(q & c) / max(len(q), 1)
    phrase_bonus = 0.2 if any(k in content or k in title for k in re.findall(r"[\u4e00-\u9fff]{2,}", query)) else 0.0
    return overlap + phrase_bonus

def _chinese_ngrams(text: str, n_range: tuple[int, int] = (2, 4)) -> list[str]:
    """对连续中文段切分 2-4 gram，弥补无 jieba 时无法做中文分词。

    例如「剑桥英语课程」→ [剑桥英语, 桥英语课, 英语课程, 剑桥英, ..., 剑桥, 桥英, 英语, 语课, 课程]，
    让 entity_anchor_terms 能拿到「剑桥」等真正的实体子词。
    """
    out: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"[\u4e00-\u9fff]+", text):
        span = m.group(0)
        for n in range(n_range[0], n_range[1] + 1):
            if len(span) < n:
                continue
            for i in range(len(span) - n + 1):
                t = span[i:i + n]
                if t in seen:
                    continue
                seen.add(t)
                out.append(t)
    return out
