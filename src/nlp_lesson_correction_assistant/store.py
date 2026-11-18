import json
from pathlib import Path

SCHEMA_VERSION = 1


def empty_state():
    return {
        "schema": SCHEMA_VERSION,
        "records": [],
        "wordlists": [],
        "current_wordlist": None,
        "decisions": [],
        "audit": [],
    }


class StateStore:
    """课件、词表版本、教师决定与采纳审计的单文件存储（原子写入）。"""

    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return empty_state()
        if not isinstance(value, dict):
            raise ValueError("状态文件格式无效")
        state = empty_state()
        for key in state:
            if key in value:
                state[key] = value[key]
        if not isinstance(state["records"], list) or not isinstance(state["wordlists"], list) \
                or not isinstance(state["decisions"], list) or not isinstance(state["audit"], list):
            raise ValueError("状态文件格式无效")
        return state

    def save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)
