"""验证真实任务树中的编辑与查看收尾，对原生进程边界使用替身。"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from app.config import ConfigCollection
from app.models.task import TaskItem, TaskScriptItem, TaskUserItem

from src_adapter import config_session, manager
from src_adapter.config import SrcConfig, SrcUserConfig
from src_adapter.manager import SrcExpander


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ScriptConfig", "ScriptView"])
async def test_shared_session_stops_before_installation_is_restored(
    tmp_path, tmp_path_factory, monkeypatch, mode
):
    install = tmp_path_factory.mktemp("src-install")
    native = install / "config"
    native.mkdir(parents=True)
    (install / "src.exe").write_bytes(b"src")
    (native / "deploy.yaml").write_text("WebuiPort: 22267\n", encoding="utf-8")
    initial = {
        "Alas": {"Emulator": {}, "Error": {}, "Optimization": {}},
        "Dungeon": {"PlannerTarget": {}},
    }
    (native / "src.json").write_text(json.dumps(initial), encoding="utf-8")
    scripts = ConfigCollection.build([SrcConfig])
    await scripts.activate()
    sid = scripts.add(SrcConfig, payload={"Info": {"Path": str(install)}})
    await scripts.commit()
    config = scripts[sid]
    uid = config.users.add(SrcUserConfig, payload={"Info": {"Base": "shared"}})
    await config.users.commit()
    root = TaskItem(info={"mode": mode})
    await root.activate()
    run_id = root.scripts.add(
        TaskScriptItem, payload={"info": {"script_id": str(sid), "script_type": "SRC"}}
    )
    await root.scripts.commit()
    script = root.scripts[run_id]
    script.users.add(TaskUserItem, payload={"info": {"user_id": str(uid)}})
    await script.users.commit()
    assert config.Info.Path == install
    expander = SrcExpander(script, config)
    opened = asyncio.Event()

    async def open_gui(_self, _exe):
        (native / "edit.txt").write_text("changed", encoding="utf-8")
        opened.set()

    monkeypatch.setattr(config_session.ProcessManager, "open_process", open_gui)
    for module in (config_session, manager):
        monkeypatch.setattr(module, "kill_src_processes", AsyncMock(return_value=True))
    expander.start()
    try:
        await asyncio.wait_for(opened.wait(), 5)
        pending = json.loads(
            (tmp_path / "data" / str(sid) / "Temp.ready").read_text("utf-8")
        )
        assert pending["config_user_id"] == (
            "Default" if mode == "ScriptConfig" else None
        )
    finally:
        await expander.stop()
    assert expander.error is None
    assert not (native / "edit.txt").exists()
    assert json.loads((native / "src.json").read_text("utf-8")) == initial
    assert not (tmp_path / "data" / str(sid) / "Temp.ready").exists()
    shared = tmp_path / "data" / str(sid) / "Default" / "ConfigFile"
    assert (shared / "edit.txt").exists() == (mode == "ScriptConfig")
