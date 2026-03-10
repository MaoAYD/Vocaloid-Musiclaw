from __future__ import annotations

import hashlib
import json
import threading
import uuid
from pathlib import Path
from typing import Any


class JsonCache:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path_for(self, namespace: str, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        directory = self.root / namespace
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{digest}.json"

    def load(self, namespace: str, key: str) -> Any | None:
        path = self._path_for(namespace, key)
        with self._lock:
            if not path.exists():
                return None
            return json.loads(path.read_text(encoding="utf-8"))

    def store(self, namespace: str, key: str, payload: Any) -> Path:
        path = self._path_for(namespace, key)
        temp_path = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        data = json.dumps(payload, ensure_ascii=False, indent=2)
        with self._lock:
            temp_path.write_text(data, encoding="utf-8")
            temp_path.replace(path)
        return path
