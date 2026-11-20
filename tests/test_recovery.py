"""原生备份只能在同一物理安装没有待恢复事务时应用。"""

from pathlib import Path
from uuid import uuid4

import pytest

from src_adapter.tools.backup_archive import (
    archive_native_backup,
    restore_native_backup,
)
from src_adapter.tools.config import (
    read_src_installation_id,
    write_src_config_snapshot_state,
)


def test_native_restore_respects_other_tasks_snapshot(isolated_host, tmp_path):
    root = tmp_path / "install"
    config = root / "config"
    config.mkdir(parents=True)
    (root / "src.exe").write_bytes(b"installed executable")
    (config / "src.json").write_text('{"value":"archived"}', encoding="utf-8")
    (config / "deploy.yaml").write_text("Deploy: {}\n", encoding="utf-8")
    archived = archive_native_backup(config)
    assert archived is not None
    (config / "src.json").write_text('{"value":"current"}', encoding="utf-8")
    owner = str(uuid4())
    journal = isolated_host.data_path / owner / "Temp.ready"
    (journal.parent / "Temp").mkdir(parents=True)
    write_src_config_snapshot_state(
        journal,
        script_id=owner,
        src_root_path=root,
        installation_id=read_src_installation_id(root),
    )
    with pytest.raises(RuntimeError, match="其他托管"):
        restore_native_backup(config, Path(archived).name, str(uuid4()))
    assert (config / "src.json").read_text(encoding="utf-8") == '{"value":"current"}'
    assert journal.exists()
