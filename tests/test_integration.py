"""Guard saved-account identity and the public plugin editing contract."""

from uuid import uuid4

import httpx
import pytest
from app.config import ConfigCollection, config_manager
from app.core import Config, Plugin
from app.core.config.upgrade import convert_legacy
from app.models.config import PluginRecord
from app.plugin.openapi import bind_openapi
from app.plugin.types import PluginState
from app.utils import io
from fastapi import FastAPI

from src_adapter.config import SrcConfig
from src_adapter.plugin import Plugin as Adapter


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_children", [False, True])
async def test_legacy_accounts_survive_save_and_reload(tmp_path, legacy_children):
    sid, first, second = [str(uuid4()) for _ in range(3)]
    payload = {
        "order": [{"uid": sid, "type": "SrcConfig"}],
        "data": {
            sid: {
                "Info": {"Name": "原托管"},
                "UserData": {
                    "order": [
                        {"uid": uid, "type": "SrcUserConfig"} for uid in (first, second)
                    ],
                    "data": {
                        uid: {
                            "Info": {
                                "Name": uid,
                                "Status": True,
                                "Base": "independent",
                            },
                        }
                        for uid in (first, second)
                    },
                },
            }
        },
    }
    if legacy_children:
        script = payload["data"][sid]
        users = script.pop("UserData")
        script["SubConfigsInfo"] = {
            "UserData": {"instances": users["order"], **users["data"]}
        }
    scripts = ConfigCollection.build([SrcConfig])
    if legacy_children:
        # Startup converts pre-YAML documents before activating plugin types.
        payload = convert_legacy(
            {"instances": payload["order"], **payload["data"]}, scripts
        )
    path = tmp_path / "scripts.yaml"
    io.write_file(path, payload)
    config_manager.register_root(scripts, path)
    await scripts.activate()
    await config_manager.flush()
    assert [str(uid) for uid in scripts[sid].users.keys()] == [first, second]
    assert scripts[sid].users[first].Info.Base == "independent"
    assert scripts[sid].info.name == "原托管"
    config_manager.unregister_root(scripts)
    restored = ConfigCollection.build([SrcConfig])
    config_manager.register_root(restored, path)
    await restored.activate()
    assert [str(uid) for uid in restored[sid].users.keys()] == [first, second]
    assert restored[sid].users[second].info.name == second


@pytest.mark.asyncio
async def test_public_crud_restore_and_plugin_capabilities(
    monkeypatch, tmp_path_factory
):
    scripts = ConfigCollection.build([SrcConfig])
    await scripts.activate()
    monkeypatch.setattr(Config, "ScriptConfig", scripts)
    monkeypatch.setitem(config_manager._collections, "ScriptConfig", scripts)
    record = PluginRecord.build(
        info={"plugin_name": "src_adapter", "state": PluginState.ENABLED}
    )
    record._plugin_cls = Adapter
    monkeypatch.setattr(Plugin, "script_types", {record.uid: Adapter.decl})
    monkeypatch.setattr(Plugin, "list_plugins", lambda: [record])
    await Adapter().pre_enable()
    from app.api import scripts_router

    app = FastAPI()
    app.include_router(scripts_router)
    app.include_router(Adapter.api_router)
    bind_openapi(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        types = (await client.get("/api/scripts/types")).json()["types"]
        assert "SRC" in types
        record.info.state = PluginState.DISABLING
        assert "SRC" not in (await client.get("/api/scripts/types")).json()["types"]
        record.info.state = PluginState.ENABLED
        created = (await client.post("/api/scripts/add", json={"type": "SRC"})).json()
        assert created["code"] == 200, created
        sid = created["scriptId"]
        added = (
            await client.post("/api/scripts/user/add", json={"scriptId": sid})
        ).json()
        assert added["code"] == 200, added
        uid = added["userId"]
        user = scripts[sid].users[uid]
        await user.update(type(user).model_validate({"Info": {"Base": "independent"}}))
        assert user.info.config_path.endswith("ConfigFile")
        context = {"scriptId": sid, "userId": uid, "target": "mas"}
        prefix = "/api/plugin/src_adapter/backup"
        result = (await client.get(prefix + "/list", params=context)).json()
        assert result["code"] == 200 and result["base"] == "independent", result
        assert {
            "value": sid,
            "label": f"SRC - {scripts[sid].Info.Name}",
        } in await Config.get_script_combox()
        schema = app.openapi()
        assert prefix + "/restore" in schema["paths"]
