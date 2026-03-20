#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import tempfile
from typing import Dict, List, Optional

from .file_utils import ensure_hidden_directory


class ResumeStateManager:
    """Persist lightweight resume state outside the report database."""

    FILENAME = "resume_state.json"

    def __init__(self, directory: str):
        self.directory = directory
        self.state_dir = os.path.join(directory, ".superpicky")
        self.state_path = os.path.join(self.state_dir, self.FILENAME)

    def exists(self) -> bool:
        return os.path.exists(self.state_path)

    def load(self) -> Optional[Dict]:
        if not self.exists():
            return None
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            return None
        return None

    def start(self, ordered_prefixes: List[str]) -> None:
        payload = {
            "version": 1,
            "status": "running",
            "total_files": len(ordered_prefixes),
            "next_index": 1,
            "pending_prefixes": list(ordered_prefixes),
        }
        self._write(payload)

    def get_resume_plan(self, available_prefixes: List[str]) -> Optional[Dict]:
        state = self.load()
        if not state or state.get("status") != "running":
            return None
        available_set = set(available_prefixes)
        pending = [prefix for prefix in (state.get("pending_prefixes") or []) if prefix in available_set]
        if not pending:
            return None
        total_files = int(state.get("total_files") or len(available_prefixes))
        completed = max(0, total_files - len(pending))
        return {
            "total_files": total_files,
            "next_index": completed + 1,
            "pending_prefixes": pending,
        }

    def mark_completed(self, prefix: str) -> None:
        state = self.load()
        if not state:
            return
        pending = [item for item in (state.get("pending_prefixes") or []) if item != prefix]
        state["pending_prefixes"] = pending
        total_files = int(state.get("total_files") or 0)
        state["next_index"] = min(total_files + 1, total_files - len(pending) + 1) if total_files > 0 else 1
        if not pending:
            self.clear()
            return
        self._write(state)

    def clear(self) -> None:
        try:
            if self.exists():
                os.remove(self.state_path)
        except FileNotFoundError:
            pass

    def _write(self, payload: Dict) -> None:
        ensure_hidden_directory(self.state_dir)
        fd, temp_path = tempfile.mkstemp(prefix="resume_", suffix=".json", dir=self.state_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.state_path)
        finally:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass
