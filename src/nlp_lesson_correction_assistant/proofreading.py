from datetime import datetime, timezone

from .records import TextRecord
from .suggestions import scan_record
from .text import normalize_text
from .wordlist import WordList


class ProofreadingError(Exception):
    """校对建议工作流的基础异常。"""


class StaleSuggestionError(ProofreadingError):
    """建议锚点已不命中当前文本（课件被编辑过），拒绝套用旧决定。"""


class UnknownRecordError(ProofreadingError):
    pass


class WordListError(ProofreadingError):
    pass


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class ProofreadingWorkflow:
    """校对建议工作流。

    关系模型：
    - 建议：不落库，按“当前文本 × 当前词表版本”扫描得出，身份是内容指纹
      （课件 + 原词 + 前后语境），与绝对位置无关；
    - 决定：追加式日志，按建议指纹记录采纳/拒绝及当时的词表版本，
      同一指纹以最新决定为准；
    - 拒绝按指纹匹配，与词表版本无关：重新扫描或词表更新后仍然生效，
      且只作用于原语境，不波及其他课件或其他句子中的同一术语；
    - 采纳：先校验指纹仍命中当前文本，再改写文本并写入审计，
      审计保留原句、位置与来源词表版本，供回查。
    """

    def __init__(self, store):
        self.store = store

    # ---- 课件记录 ----

    def import_records(self, values):
        if not isinstance(values, list):
            raise TypeError("导入内容必须是记录列表")
        incoming = [TextRecord.from_dict(value).to_dict() for value in values]
        state = self.store.load()
        current = {record["record_id"]: record for record in state["records"]}
        for record in incoming:
            record["text"] = normalize_text(record["text"])
            previous = current.get(record["record_id"])
            if previous is None or record["revision"] >= previous["revision"]:
                current[record["record_id"]] = record
        state["records"] = [current[key] for key in sorted(current)]
        self.store.save(state)
        return {"received": len(incoming), "stored": len(state["records"])}

    def list_records(self, source=None):
        records = self.store.load()["records"]
        if source:
            records = [record for record in records if record["source"] == source]
        return records

    def edit_text(self, record_id, new_text, observed_at=None):
        """课件编辑：只更新正文与修订号，不改动任何决定。

        决定靠内容指纹重新锚定：语境未变的决定继续生效；
        语境已变的决定不再命中，相应建议重新进入待处理。
        """
        state = self.store.load()
        record = self._record(state, record_id)
        text = normalize_text(new_text)
        if not text:
            raise ValueError("课件正文不能为空")
        record["text"] = text
        record["revision"] += 1
        if observed_at is not None:
            datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            record["observed_at"] = observed_at
        self.store.save(state)
        return dict(record)

    # ---- 词表版本 ----

    def register_wordlist(self, value, registered_at=None):
        wordlist = WordList.from_dict(value)
        state = self.store.load()
        if any(item["version"] == wordlist.version for item in state["wordlists"]):
            raise WordListError(f"词表版本已存在：{wordlist.version}")
        payload = wordlist.to_dict()
        payload["registered_at"] = registered_at or _utc_now()
        state["wordlists"].append(payload)
        state["current_wordlist"] = wordlist.version
        self.store.save(state)
        return {"version": wordlist.version, "entries": len(wordlist.entries)}

    def wordlists(self):
        state = self.store.load()
        return {
            "current": state["current_wordlist"],
            "versions": [
                {"version": item["version"], "registered_at": item["registered_at"],
                 "entries": len(item["entries"])}
                for item in state["wordlists"]
            ],
        }

    # ---- 建议扫描与教师决定 ----

    def scan(self, record_id=None):
        """仍待处理的建议：当前词表命中且未被生效中的拒绝覆盖。"""
        state = self.store.load()
        wordlist = self._current_wordlist(state)
        rejected = self._active_rejections(state)
        pending = []
        for record in state["records"]:
            if record_id and record["record_id"] != record_id:
                continue
            for occurrence in scan_record(record, wordlist):
                if occurrence.suggestion_id in rejected:
                    continue
                item = occurrence.to_dict()
                item["record_context"] = record.get("context", "")
                item["wordlist_version"] = wordlist.version
                pending.append(item)
        return pending

    def adopt(self, suggestion_id, decided_at=None):
        """采纳：校验锚点后改写文本、升修订号，并写入可回查的审计记录。"""
        state = self.store.load()
        record, occurrence, wordlist = self._locate(state, suggestion_id)
        text = record["text"]
        record["text"] = text[:occurrence.start] + occurrence.replacement + text[occurrence.end:]
        record["revision"] += 1
        decided = decided_at or _utc_now()
        decision = self._decision(state, "adopt", occurrence, wordlist, decided)
        state["decisions"].append(decision)
        entry = {
            "audit_id": f"aud-{len(state['audit']) + 1:04d}",
            "decision_id": decision["decision_id"],
            "record_id": occurrence.record_id,
            "record_context": record.get("context", ""),
            "category": occurrence.category,
            "term": occurrence.term,
            "replacement": occurrence.replacement,
            "sentence": occurrence.sentence,
            "context": {"prefix": occurrence.prefix, "match": occurrence.term, "suffix": occurrence.suffix},
            "position": occurrence.start,
            "wordlist_version": wordlist.version,
            "decided_at": decided,
            "revision_after": record["revision"],
        }
        state["audit"].append(entry)
        self.store.save(state)
        return entry

    def reject(self, suggestion_id, decided_at=None):
        """按语境拒绝：只覆盖该指纹对应的命中，不影响其他课件或其他句子。"""
        state = self.store.load()
        record, occurrence, wordlist = self._locate(state, suggestion_id)
        active = self._active_rejections(state)
        if suggestion_id in active:
            return active[suggestion_id]
        decision = self._decision(state, "reject", occurrence, wordlist, decided_at or _utc_now())
        state["decisions"].append(decision)
        self.store.save(state)
        return decision

    # ---- 教师视图 ----

    def adopted(self):
        """已采纳的变化及其来源词表版本。"""
        return list(self.store.load()["audit"])

    def rejected(self):
        """生效中的拒绝决定：指纹仍命中当前文本（失效的见 stale()）。"""
        state = self.store.load()
        active = self._active_rejections(state)
        if not active:
            return []
        current_ids = self._current_ids(state)
        if current_ids is None:
            return []
        return [decision for decision in active.values()
                if decision["suggestion_id"] in current_ids]

    def stale(self):
        """因课件编辑而不再命中任何文本的拒绝决定（不会套到不同文字上）。"""
        state = self.store.load()
        current_ids = self._current_ids(state)
        if current_ids is None:
            return []
        return [decision for decision in self._active_rejections(state).values()
                if decision["suggestion_id"] not in current_ids]

    def status(self):
        state = self.store.load()
        result = {
            "records": len(state["records"]),
            "current_wordlist": state["current_wordlist"],
            "wordlist_versions": [item["version"] for item in state["wordlists"]],
            "adopted": len(state["audit"]),
        }
        if state["current_wordlist"]:
            result["pending"] = len(self.scan())
            result["rejected"] = len(self.rejected())
            result["stale"] = len(self.stale())
        return result

    # ---- 内部 ----

    def _record(self, state, record_id):
        for record in state["records"]:
            if record["record_id"] == record_id:
                return record
        raise UnknownRecordError(f"未知课件记录：{record_id}")

    def _current_wordlist(self, state):
        version = state["current_wordlist"]
        if not version:
            raise WordListError("尚未注册词表，无法生成建议")
        for item in state["wordlists"]:
            if item["version"] == version:
                return WordList.from_dict(item)
        raise WordListError(f"当前词表版本缺失：{version}")

    def _locate(self, state, suggestion_id):
        wordlist = self._current_wordlist(state)
        for record in state["records"]:
            for occurrence in scan_record(record, wordlist):
                if occurrence.suggestion_id == suggestion_id:
                    return record, occurrence, wordlist
        raise StaleSuggestionError(f"建议已失效或不存在：{suggestion_id}（课件可能已编辑，请重新扫描）")

    def _decision(self, state, kind, occurrence, wordlist, decided):
        return {
            "decision_id": f"dec-{len(state['decisions']) + 1:04d}",
            "type": kind,
            "suggestion_id": occurrence.suggestion_id,
            "record_id": occurrence.record_id,
            "term": occurrence.term,
            "replacement": occurrence.replacement,
            "category": occurrence.category,
            "context": {"prefix": occurrence.prefix, "match": occurrence.term, "suffix": occurrence.suffix},
            "wordlist_version": wordlist.version,
            "decided_at": decided,
        }

    def _current_ids(self, state):
        """当前文本中全部建议指纹；未注册词表时返回 None。"""
        try:
            wordlist = self._current_wordlist(state)
        except WordListError:
            return None
        ids = set()
        for record in state["records"]:
            for occurrence in scan_record(record, wordlist):
                ids.add(occurrence.suggestion_id)
        return ids

    def _active_rejections(self, state):
        latest = {}
        for decision in state["decisions"]:
            latest[decision["suggestion_id"]] = decision
        return {key: decision for key, decision in latest.items() if decision["type"] == "reject"}
