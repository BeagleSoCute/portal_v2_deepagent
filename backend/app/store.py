"""Spec store แบบไฟล์ JSON — มี version ตั้งแต่วันแรก เพราะ P5 ต้องใช้ diff/rollback.

layout:
  data/agents/<id>/v1.json, v2.json, ...
  data/agents/<id>/HEAD          <- เลข version ที่ active อยู่
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from .spec import AgentSpec


class SpecStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.getenv("DATA_DIR", "./data")).resolve() / "agents"
        self.root.mkdir(parents=True, exist_ok=True)

    # -- paths
    def _dir(self, agent_id: str) -> Path:
        return self.root / agent_id

    def _head_file(self, agent_id: str) -> Path:
        return self._dir(agent_id) / "HEAD"

    # -- read
    def list_agents(self) -> list[dict[str, Any]]:
        out = []
        for d in sorted(self.root.iterdir()) if self.root.exists() else []:
            if not d.is_dir():
                continue
            try:
                spec = self.get(d.name)
            except FileNotFoundError:
                continue
            out.append({
                "id": spec.id, "name": spec.name, "description": spec.description,
                "version": spec.version, "model": spec.model.ref,
                "updated_at": spec.updated_at,
                "versions": self.versions(d.name),
            })
        return out

    def versions(self, agent_id: str) -> list[int]:
        d = self._dir(agent_id)
        if not d.exists():
            return []
        return sorted(int(p.stem[1:]) for p in d.glob("v*.json"))

    def head(self, agent_id: str) -> int:
        f = self._head_file(agent_id)
        if f.exists():
            return int(f.read_text().strip())
        vs = self.versions(agent_id)
        if not vs:
            raise FileNotFoundError(f"ไม่พบ agent '{agent_id}'")
        return vs[-1]

    def get(self, agent_id: str, version: int | None = None) -> AgentSpec:
        v = version or self.head(agent_id)
        f = self._dir(agent_id) / f"v{v}.json"
        if not f.exists():
            raise FileNotFoundError(f"ไม่พบ agent '{agent_id}' version {v}")
        return AgentSpec.model_validate(json.loads(f.read_text(encoding="utf-8")))

    # -- write
    def create(self, spec: AgentSpec) -> AgentSpec:
        agent_id = spec.id or uuid.uuid4().hex[:12]
        d = self._dir(agent_id)
        if d.exists():
            raise ValueError(f"agent id '{agent_id}' มีอยู่แล้ว")
        d.mkdir(parents=True)
        spec = spec.model_copy(update={"id": agent_id, "version": 1})
        self._write(agent_id, spec)
        return spec

    def update(self, agent_id: str, spec: AgentSpec) -> AgentSpec:
        """สร้าง version ใหม่เสมอ ไม่ทับของเดิม — rollback ได้ตลอด."""
        next_v = (self.versions(agent_id) or [0])[-1] + 1
        spec = spec.model_copy(update={"id": agent_id, "version": next_v})
        self._write(agent_id, spec)
        return spec

    def activate(self, agent_id: str, version: int) -> AgentSpec:
        if version not in self.versions(agent_id):
            raise FileNotFoundError(f"agent '{agent_id}' ไม่มี version {version}")
        self._head_file(agent_id).write_text(str(version))
        return self.get(agent_id, version)

    def delete(self, agent_id: str) -> None:
        import shutil

        shutil.rmtree(self._dir(agent_id), ignore_errors=True)

    def _write(self, agent_id: str, spec: AgentSpec) -> None:
        d = self._dir(agent_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"v{spec.version}.json").write_text(
            json.dumps(spec.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._head_file(agent_id).write_text(str(spec.version))

    # -- import from ./specs
    def seed_from(self, folder: str | Path) -> list[str]:
        """โหลด spec ตัวอย่างเข้า store ถ้ายังไม่มี (ชื่อซ้ำถือว่ามีแล้ว)."""
        folder = Path(folder)
        if not folder.exists():
            return []
        existing = {a["name"] for a in self.list_agents()}
        created = []
        for f in sorted(folder.glob("*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            spec = AgentSpec.model_validate(data)
            if spec.name in existing:
                continue
            created.append(self.create(spec.model_copy(update={"id": None})).id or "")
        return created
