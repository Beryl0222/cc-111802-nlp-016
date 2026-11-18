import hashlib
from dataclasses import dataclass

# 建议锚点使用的上下文窗口（词前/词后各取的字数）。
# 窗口内的文字不变，指纹就不变：位置平移不影响锚点，文字改动立即使指纹失效。
CONTEXT_WINDOW = 16

SENTENCE_ENDINGS = "。！？!?；;\n"


def fingerprint(record_id, term, prefix, suffix):
    """建议的内容指纹：绑定课件、原词及其所在语境，与绝对位置无关。"""
    payload = "\x00".join((record_id, term, prefix, suffix))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def sentence_around(text, start, end):
    """截取命中位置所在的完整句子，供采纳后回查改了哪句话。"""
    left = 0
    for index in range(start - 1, -1, -1):
        if text[index] in SENTENCE_ENDINGS:
            left = index + 1
            break
    right = len(text)
    for index in range(end, len(text)):
        if text[index] in SENTENCE_ENDINGS:
            right = index + 1
            break
    return text[left:right].strip()


@dataclass(frozen=True)
class Occurrence:
    """词表条目在当前文本中的一次命中，即一条待决建议。"""

    suggestion_id: str
    record_id: str
    term: str
    replacement: str
    category: str
    note: str
    start: int
    end: int
    prefix: str
    suffix: str
    sentence: str

    def to_dict(self):
        return {
            "suggestion_id": self.suggestion_id,
            "record_id": self.record_id,
            "term": self.term,
            "replacement": self.replacement,
            "category": self.category,
            "note": self.note,
            "position": self.start,
            "context": {"prefix": self.prefix, "match": self.term, "suffix": self.suffix},
            "sentence": self.sentence,
        }


def scan_record(record, wordlist):
    """用指定词表版本扫描一条课件记录，返回全部命中（按出现位置排序）。"""
    text = record["text"]
    found = []
    for entry in wordlist.entries:
        start = 0
        while True:
            index = text.find(entry.term, start)
            if index < 0:
                break
            end = index + len(entry.term)
            prefix = text[max(0, index - CONTEXT_WINDOW):index]
            suffix = text[end:end + CONTEXT_WINDOW]
            found.append(Occurrence(
                suggestion_id=fingerprint(record["record_id"], entry.term, prefix, suffix),
                record_id=record["record_id"],
                term=entry.term,
                replacement=entry.replacement,
                category=entry.category,
                note=entry.note,
                start=index,
                end=end,
                prefix=prefix,
                suffix=suffix,
                sentence=sentence_around(text, index, end),
            ))
            start = end
    found.sort(key=lambda item: (item.start, item.term))
    return found
