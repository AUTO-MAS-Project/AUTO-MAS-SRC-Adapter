"""验证真实任务树中的编辑与查看收尾，对原生进程边界使用替身。"""

import asyncio
import json
import shutil
from unittest.mock import AsyncMock

import pytest
from app.config import ConfigCollection
from app.models.task import TaskItem, TaskScriptItem, TaskUserItem

from src_adapter import config_session, manager
from src_adapter.config import SrcConfig, SrcUserConfig
from src_adapter.manager import SrcExpander


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ScriptConfig", "ScriptView"])
@pytest.mark.parametrize("base", ["shared", "native"])
async def test_session_preserves_edits_only_in_its_owner(
    tmp_path, tmp_path_factory, monkeypatch, mode, base
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
    uid = config.users.add(SrcUserConfig, payload={"Info": {"Base": base}})
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
        expect_shared = mode == "ScriptConfig" and base == "shared"
        expect_native = mode == "ScriptConfig" and base == "native"
        assert pending["config_user_id"] == ("Default" if expect_shared else None)
        if base == "native":
            # 保留 GUI 已保存改动但任务尚未收尾时的真实日志，模拟下一次启动遇到的现场。
            run_data = tmp_path / "data" / str(sid)
            crashed = tmp_path / "crashed"
            shutil.copytree(run_data, crashed)
    finally:
        await expander.stop()
    assert expander.error is None
    assert (native / "edit.txt").exists() == expect_native
    assert json.loads((native / "src.json").read_text("utf-8")) == initial
    assert not (tmp_path / "data" / str(sid) / "Temp.ready").exists()
    shared = tmp_path / "data" / str(sid) / "Default" / "ConfigFile"
    assert (shared / "edit.txt").exists() == expect_shared
    if base == "native":
        shutil.copytree(crashed, run_data, dirs_exist_ok=True)
        (native / "edit.txt").write_text("saved by GUI before exit", encoding="utf-8")
        restarted = SrcExpander(script, config)
        try:
            assert await restarted.check() is None
            assert (native / "edit.txt").exists() == expect_native
            assert not (run_data / "Temp.ready").exists()
        finally:
            await restarted.finish()
