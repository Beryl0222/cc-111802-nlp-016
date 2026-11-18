import json
import tempfile
import unittest
from pathlib import Path

from nlp_lesson_correction_assistant.proofreading import (
    ProofreadingWorkflow,
    StaleSuggestionError,
    UnknownRecordError,
    WordListError,
)
from nlp_lesson_correction_assistant.store import StateStore

WORDLIST_V1 = {
    "version": "2026.11",
    "entries": [
        {"entry_id": "typo-pao-de", "term": "跑的很快", "replacement": "跑得很快",
         "category": "错别字", "note": "动词后的补语标志用“得”"},
        {"entry_id": "term-guanxing", "term": "惯性定律", "replacement": "牛顿第一定律",
         "category": "术语", "note": "物理课件推荐使用规范术语"},
        {"entry_id": "punct-double-stop", "term": "。。", "replacement": "。",
         "category": "标点", "note": "重复的句号"},
    ],
}

WORDLIST_V2 = {
    "version": "2026.12",
    "entries": [
        *WORDLIST_V1["entries"],
        {"entry_id": "typo-zai-jian", "term": "在见", "replacement": "再见", "category": "错别字"},
    ],
}

PHYSICS = {
    "record_id": "physics-1",
    "source": "demo",
    "revision": 1,
    "observed_at": "2026-11-18T09:10:00Z",
    "context": "物理",
    "text": "小球从斜面滚下，速度越来越快，它跑的很快。根据惯性定律，物体总保持匀速直线运动状态或静止状态。。",
}

HISTORY = {
    "record_id": "history-1",
    "source": "demo",
    "revision": 1,
    "observed_at": "2026-11-18T09:12:00Z",
    "context": "科学史",
    "text": "教材先介绍了惯性定律一词的来源，再说明它后来被称为牛顿第一定律。",
}

DECIDED = "2026-11-18T10:00:00Z"


def make_workflow(directory, records=(PHYSICS, HISTORY)):
    workflow = ProofreadingWorkflow(StateStore(Path(directory) / "state.json"))
    workflow.import_records(list(records))
    workflow.register_wordlist(WORDLIST_V1, registered_at="2026-11-18T09:20:00Z")
    return workflow


def pending_for(workflow, record_id, term=None):
    items = [item for item in workflow.scan() if item["record_id"] == record_id]
    if term is not None:
        items = [item for item in items if item["term"] == term]
    return items


class ScanTests(unittest.TestCase):
    def test_pending_suggestions_carry_position_version_and_context(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = make_workflow(directory)
            pending = workflow.scan()
            self.assertEqual(len(pending), 4)
            self.assertEqual(len({item["suggestion_id"] for item in pending}), 4)
            for item in pending:
                self.assertEqual(item["wordlist_version"], "2026.11")
                self.assertIsInstance(item["position"], int)
                self.assertIn(item["term"], item["sentence"])
                self.assertTrue(item["record_context"])
            physics_terms = [item["term"] for item in pending_for(workflow, "physics-1")]
            self.assertEqual(physics_terms, ["跑的很快", "惯性定律", "。。"])

    def test_scan_requires_registered_wordlist(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = ProofreadingWorkflow(StateStore(Path(directory) / "state.json"))
            workflow.import_records([PHYSICS])
            with self.assertRaises(WordListError):
                workflow.scan()


class AdoptTests(unittest.TestCase):
    def test_adopt_rewrites_text_and_records_auditable_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = make_workflow(directory)
            target = pending_for(workflow, "physics-1", "跑的很快")[0]
            punct = pending_for(workflow, "physics-1", "。。")[0]
            history = pending_for(workflow, "history-1")[0]

            entry = workflow.adopt(target["suggestion_id"], decided_at=DECIDED)

            record = workflow.list_records("demo")[1]
            self.assertEqual(record["revision"], 2)
            self.assertIn("跑得很快", record["text"])
            self.assertNotIn("跑的很快", record["text"])
            self.assertEqual(entry["term"], "跑的很快")
            self.assertEqual(entry["replacement"], "跑得很快")
            self.assertEqual(entry["wordlist_version"], "2026.11")
            self.assertEqual(entry["decided_at"], DECIDED)
            self.assertEqual(entry["revision_after"], 2)
            self.assertIn("跑的很快", entry["sentence"])
            self.assertEqual(entry["context"]["match"], "跑的很快")

            remaining = workflow.scan()
            remaining_ids = {item["suggestion_id"] for item in remaining}
            self.assertEqual(len(remaining), 3)
            self.assertNotIn(target["suggestion_id"], remaining_ids)
            # 远处的锚点（标点、另一门课的术语）不受本次采纳影响
            self.assertIn(punct["suggestion_id"], remaining_ids)
            self.assertIn(history["suggestion_id"], remaining_ids)
            # 语境窗口覆盖了被改文字的术语建议仍在待处理中（按新锚点）
            self.assertEqual(len(pending_for(workflow, "physics-1", "惯性定律")), 1)
            self.assertEqual(workflow.adopted(), [entry])

    def test_adopt_rejects_stale_suggestion_after_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = make_workflow(directory)
            target = pending_for(workflow, "physics-1", "跑的很快")[0]
            workflow.edit_text("physics-1", "小球从斜面滚下，速度越来越快，它跑得很快。")
            with self.assertRaises(StaleSuggestionError):
                workflow.adopt(target["suggestion_id"])
            self.assertEqual(workflow.adopted(), [])

    def test_adopt_after_reject_supersedes_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = make_workflow(directory)
            target = pending_for(workflow, "history-1", "惯性定律")[0]
            workflow.reject(target["suggestion_id"], decided_at=DECIDED)
            workflow.adopt(target["suggestion_id"], decided_at="2026-11-18T11:00:00Z")
            self.assertEqual(workflow.rejected(), [])
            record = [r for r in workflow.list_records() if r["record_id"] == "history-1"][0]
            self.assertIn("牛顿第一定律一词的来源", record["text"])


class RejectTests(unittest.TestCase):
    def test_rejection_survives_rescan_reload_and_wordlist_update(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = make_workflow(directory)
            target = pending_for(workflow, "history-1", "惯性定律")[0]
            decision = workflow.reject(target["suggestion_id"], decided_at=DECIDED)
            self.assertEqual(decision["type"], "reject")
            self.assertEqual(decision["wordlist_version"], "2026.11")

            self.assertEqual(pending_for(workflow, "history-1"), [])

            reloaded = ProofreadingWorkflow(StateStore(Path(directory) / "state.json"))
            self.assertEqual(pending_for(reloaded, "history-1"), [])

            reloaded.register_wordlist(WORDLIST_V2, registered_at="2026-11-19T09:00:00Z")
            self.assertEqual(pending_for(reloaded, "history-1"), [])
            versions = {item["wordlist_version"] for item in reloaded.scan()}
            self.assertEqual(versions, {"2026.12"})
            self.assertEqual(len(reloaded.rejected()), 1)

    def test_rejection_is_scoped_to_its_context(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = make_workflow(directory)
            target = pending_for(workflow, "history-1", "惯性定律")[0]
            workflow.reject(target["suggestion_id"], decided_at=DECIDED)
            physics_hits = pending_for(workflow, "physics-1", "惯性定律")
            self.assertEqual(len(physics_hits), 1)
            self.assertNotEqual(physics_hits[0]["suggestion_id"], target["suggestion_id"])

    def test_rejection_of_one_sentence_keeps_other_occurrence_pending(self):
        record = {
            "record_id": "physics-2",
            "source": "demo",
            "revision": 1,
            "observed_at": "2026-11-18T09:15:00Z",
            "context": "物理",
            "text": "甲班先学惯性定律。乙班也讨论惯性定律的表述。",
        }
        with tempfile.TemporaryDirectory() as directory:
            workflow = make_workflow(directory, records=(record,))
            hits = pending_for(workflow, "physics-2", "惯性定律")
            self.assertEqual(len(hits), 2)
            workflow.reject(hits[0]["suggestion_id"], decided_at=DECIDED)
            remaining = pending_for(workflow, "physics-2", "惯性定律")
            self.assertEqual(len(remaining), 1)
            self.assertEqual(remaining[0]["suggestion_id"], hits[1]["suggestion_id"])

    def test_reject_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = make_workflow(directory)
            target = pending_for(workflow, "history-1", "惯性定律")[0]
            first = workflow.reject(target["suggestion_id"], decided_at=DECIDED)
            second = workflow.reject(target["suggestion_id"], decided_at="2026-11-18T12:00:00Z")
            self.assertEqual(first["decision_id"], second["decision_id"])
            self.assertEqual(len(workflow.rejected()), 1)


class EditTests(unittest.TestCase):
    def test_position_shift_keeps_decision_on_same_text(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = make_workflow(directory)
            target = pending_for(workflow, "physics-1", "惯性定律")[0]
            workflow.reject(target["suggestion_id"], decided_at=DECIDED)

            record = [r for r in workflow.list_records() if r["record_id"] == "physics-1"][0]
            workflow.edit_text("physics-1", "一、课程导入。" + record["text"])

            self.assertEqual(pending_for(workflow, "physics-1", "惯性定律"), [])
            self.assertEqual(workflow.stale(), [])
            self.assertEqual(len(workflow.rejected()), 1)

    def test_text_change_invalidates_decision_without_touching_other_text(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = make_workflow(directory)
            target = pending_for(workflow, "physics-1", "惯性定律")[0]
            workflow.reject(target["suggestion_id"], decided_at=DECIDED)

            workflow.edit_text(
                "physics-1",
                "小球从斜面滚下，速度越来越快。由惯性定律可知，物体保持匀速直线运动。",
            )

            hits = pending_for(workflow, "physics-1", "惯性定律")
            self.assertEqual(len(hits), 1)
            self.assertNotEqual(hits[0]["suggestion_id"], target["suggestion_id"])
            stale = workflow.stale()
            self.assertEqual([item["suggestion_id"] for item in stale], [target["suggestion_id"]])
            self.assertEqual(workflow.rejected(), [])

    def test_edit_unknown_record_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = make_workflow(directory)
            with self.assertRaises(UnknownRecordError):
                workflow.edit_text("missing", "新正文")


class VersionViewTests(unittest.TestCase):
    def test_adopted_entries_keep_source_version_after_wordlist_update(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = make_workflow(directory)
            target = pending_for(workflow, "physics-1", "跑的很快")[0]
            workflow.adopt(target["suggestion_id"], decided_at=DECIDED)
            workflow.register_wordlist(WORDLIST_V2, registered_at="2026-11-19T09:00:00Z")

            adopted = workflow.adopted()
            self.assertEqual(len(adopted), 1)
            self.assertEqual(adopted[0]["wordlist_version"], "2026.11")
            self.assertEqual(workflow.wordlists()["current"], "2026.12")
            self.assertEqual(workflow.wordlists()["versions"][0]["version"], "2026.11")

    def test_status_overview(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = make_workflow(directory)
            status = workflow.status()
            self.assertEqual(status["records"], 2)
            self.assertEqual(status["pending"], 4)
            self.assertEqual(status["adopted"], 0)
            self.assertEqual(status["rejected"], 0)
            self.assertEqual(status["current_wordlist"], "2026.11")

    def test_duplicate_wordlist_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = make_workflow(directory)
            with self.assertRaises(WordListError):
                workflow.register_wordlist(WORDLIST_V1)


class FixtureTests(unittest.TestCase):
    def test_demo_fixtures_drive_the_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = ProofreadingWorkflow(StateStore(Path(directory) / "state.json"))
            records = json.loads(Path("fixtures/proofreading_records.json").read_text(encoding="utf-8"))
            wordlist = json.loads(Path("fixtures/wordlist.json").read_text(encoding="utf-8"))
            workflow.import_records(records)
            workflow.register_wordlist(wordlist)
            pending = workflow.scan()
            self.assertEqual(len(pending), 4)
            history_hit = [item for item in pending if item["record_id"] == "history-lesson-1"][0]
            workflow.reject(history_hit["suggestion_id"])
            typo = [item for item in pending if item["category"] == "错别字"][0]
            workflow.adopt(typo["suggestion_id"])
            self.assertEqual(len(workflow.scan()), 2)
            self.assertEqual(len(workflow.adopted()), 1)
            self.assertEqual(len(workflow.rejected()), 1)


if __name__ == "__main__":
    unittest.main()
