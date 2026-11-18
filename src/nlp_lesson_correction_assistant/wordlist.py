from dataclasses import asdict, dataclass

CATEGORIES = ("错别字", "术语", "标点")


@dataclass(frozen=True)
class WordListEntry:
    entry_id: str
    term: str
    replacement: str
    category: str
    note: str = ""

    @classmethod
    def from_dict(cls, value):
        entry = cls(
            entry_id=str(value.get("entry_id", "")).strip(),
            term=str(value.get("term", "")),
            replacement=str(value.get("replacement", "")),
            category=str(value.get("category", "")).strip(),
            note=str(value.get("note", "")).strip(),
        )
        if not entry.entry_id or not entry.term.strip() or not entry.replacement.strip():
            raise ValueError("词表条目缺少编号、原词或推荐词")
        if entry.category not in CATEGORIES:
            raise ValueError(f"词表条目类别无效：{entry.category or '空'}")
        if entry.term == entry.replacement:
            raise ValueError(f"词表条目 {entry.entry_id} 的原词与推荐词相同")
        return entry

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class WordList:
    """某一版本的词表。版本不可变，更新词表即注册新版本。"""

    version: str
    entries: tuple
    registered_at: str = ""

    @classmethod
    def from_dict(cls, value):
        version = str(value.get("version", "")).strip()
        if not version:
            raise ValueError("词表缺少版本号")
        raw_entries = value.get("entries")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise ValueError("词表必须包含至少一个条目")
        entries = tuple(WordListEntry.from_dict(item) for item in raw_entries)
        entry_ids = [entry.entry_id for entry in entries]
        if len(set(entry_ids)) != len(entry_ids):
            raise ValueError("词表条目编号重复")
        terms = [entry.term for entry in entries]
        if len(set(terms)) != len(terms):
            raise ValueError("词表存在重复的原词")
        return cls(
            version=version,
            entries=entries,
            registered_at=str(value.get("registered_at", "")).strip(),
        )

    def to_dict(self):
        return {
            "version": self.version,
            "registered_at": self.registered_at,
            "entries": [entry.to_dict() for entry in self.entries],
        }
