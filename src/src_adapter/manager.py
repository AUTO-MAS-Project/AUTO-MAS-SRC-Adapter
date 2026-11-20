#   AUTO-MAS: A Multi-Script, Multi-Config Management and Automation Software
#   Copyright © 2024-2025 DLmaster361
#   Copyright © 2025-2026 AUTO-MAS Team
#   This file is part of AUTO-MAS.
#   AUTO-MAS is free software: you can redistribute it and/or modify
#   it under the terms of the GNU Affero General Public License as
#   published by the Free Software Foundation, either version 3 of
#   the License, or (at your option) any later version.
#   AUTO-MAS is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty
#   of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See
#   the GNU Affero General Public License for more details.
#   You should have received a copy of the GNU Affero General Public License
#   along with AUTO-MAS. If not, see <https://www.gnu.org/licenses/>.
#   Contact: DLmaster_361@163.com
import asyncio
import hashlib
import shutil
import uuid
from contextlib import ExitStack, suppress
from datetime import datetime
from pathlib import Path

from auto_mas_core import TaskMode, UserExpander
from auto_mas_core.host.config import config_manager
from auto_mas_core.host.core import Notify, Publisher
from auto_mas_core.host.core_script_update import (
    ScriptUpdateRequest,
    auto_recover_script_install,
    run_managed_script_install,
    run_script_update_with_safe_fallback,
)
from auto_mas_core.host.core_script_update_recovery import DownloadRetryFailed
from auto_mas_core.host.core_ws import protocol
from auto_mas_core.host.models_notification_report import TaskResultReport
from auto_mas_core.host.models_schema import WSTaskNoticeData
from auto_mas_core.host.services import script_install
from auto_mas_core.host.services_script_install_backup import ManagedInstallBackup
from auto_mas_core.host.task_proxy_helpers import (
    CONFIG_BASE_NATIVE,
    CONFIG_BASE_SHARED,
    read_config_base,
)
from auto_mas_core.host.utils import ProcessManager, get_logger
from auto_mas_core.host.utils_common_fluxdown_cli import FluxdownCancelUnconfirmed

from .auto_proxy import AutoProxyTask
from .config_session import ScriptConfigTask, ScriptViewTask
from .tools import (
    SrcConfigSnapshotState,
    SrcProcessState,
    archive_native_backup,
    has_committed_src_user_config_transaction,
    is_src_config_available,
    kill_src_processes,
    program_update,
    read_src_config_snapshot_state,
    read_src_installation_id,
    read_src_process_state,
    recover_interrupted_src_config_swap,
    recover_src_user_config,
    save_src_user_config,
    validate_src_cleanup_paths,
    validate_src_installation,
    write_src_config_snapshot_state,
    write_src_process_state,
)
from .tools.config import assert_no_foreign_pending_snapshot
from .tools.managed_recovery import SRCInstallAdapter

logger = get_logger("SRC 调度器")

_EMULATOR_CLOSE_TIMEOUT_SECONDS = 30
_NOTIFICATION_TIMEOUT_SECONDS = 30
_WEBSOCKET_REPORT_TIMEOUT_SECONDS = 5


class SrcExpander(UserExpander):
    """在已有托管锁内恢复 SRC 安装与配置，向选中的账号派发执行器。"""

    def __init__(self, script, config):
        super().__init__(script, config)
        self._install_access = ExitStack()
        self._program_update_prepared = False
        self.process_cleanup_success = True
        self.prepared = False
        self._cleaned_src_roots = set()
        self.begin_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.keep_native_edits = False

    def can_run_user(self, entry):
        return super().can_run_user(entry) and (
            self.task_info.mode != TaskMode.AUTO_PROXY or entry.Info.RemainedDay != 0
        )

    async def check(self) -> str | None:
        """校验SRC配置是否可用"""
        root_path = self.script_config.Info.Path
        if root_path is None or not root_path.is_dir():
            return "src.exe文件不存在, 请检查SRC路径设置！"
        self._initialize_recovery_context()
        await self._recover_previous_run()
        self._recover_default_user_config_transaction()
        marker = script_install.workspace(root_path) / "src-install.json"
        # 仅存在上轮安装记录时允许损坏的原生 exe 进入显式恢复入口；
        # 无记录时仍按正常安装完整性检查拒绝任务。
        if not (root_path / "src.exe").is_file() and not marker.is_file():
            return "src.exe文件不存在, 请检查SRC路径设置！"
        src_set_path = root_path / "config"
        temp_path = Path.cwd() / f"data/{self.script_info.script_id}/Temp"
        temp_ready_path = temp_path.with_name(temp_path.name + ".ready")
        src_config_available = is_src_config_available(src_set_path)
        temp_config_available = temp_ready_path.exists() and is_src_config_available(
            temp_path
        )
        if not src_config_available and not temp_config_available:
            return "SRC配置文件不存在或已损坏, 请检查SRC路径设置或检查配置文件情况！"
        # 托管级存档仅在存在非原生来源账号时需要: 原生来源直接使用 SRC 安装目录的原生配置
        if (
            self.task_info.mode == TaskMode.AUTO_PROXY
            and self._has_mas_config_user()
            and not (
                Path.cwd() / f"data/{self.script_info.script_id}/Default/ConfigFile"
            ).exists()
        ):
            return "未完成 SRC 全局设置, 请先设置 SRC！"
        return None

    def _has_mas_config_user(self) -> bool:
        """检查本次参与代理并使用 MAS 配置的账号。"""
        return any(
            (account := self.resolve_user(user)) is not None
            and self.can_run_user(account)
            and read_config_base(account, CONFIG_BASE_SHARED) != CONFIG_BASE_NATIVE
            for user in self.script_info.users.values()
        )

    async def prepare(self):
        await self._recover_unconfirmed_install()
        if self.task_info.mode == TaskMode.AUTO_PROXY:
            await self._cleanup_current_src_root()
        else:
            program_update.assert_safe_to_launch(self.src_root_path)
        await self._prepare_program_update()
        program_update.assert_safe_to_launch(self.src_root_path)
        await self._cleanup_current_src_root()
        self._backup_src_config_to_temp()
        self.prepared = True
        with suppress(Exception):
            archive_native_backup(self.src_set_path)

    def _initialize_recovery_context(self) -> None:
        """锁定脚本并冻结本次恢复使用的路径。"""

        locked_root_path = self.script_config.Info.Path
        if locked_root_path is None:
            raise ValueError("Path未配置，请检查路径设置")
        locked_root_path = locked_root_path.resolve()
        self._install_access.enter_context(
            script_install.installation_access(locked_root_path)
        )
        self.src_root_path = locked_root_path
        self.src_exe_path = self.src_root_path / "src.exe"
        self.src_set_path = self.src_root_path / "config"
        self.temp_path = Path.cwd() / f"data/{self.script_info.script_id}/Temp"
        self.src_process_state_path = (
            Path.cwd() / f"data/{self.script_info.script_id}/Temp.process.json"
        )
        self.temp_ready_path = self.temp_path.with_name(self.temp_path.name + ".ready")

    async def _recover_previous_run(self) -> None:
        """在当前配置检查前清理进程并处置上次快照。"""

        self._clear_uncommitted_config_snapshots()

        process_state = None
        process_state_error: Exception | None = None
        try:
            process_state = read_src_process_state(
                self.src_process_state_path,
                expected_script_id=self.script_info.script_id,
            )
        except (OSError, ValueError) as e:
            process_state_error = e

        snapshot_state: SrcConfigSnapshotState | None = None
        snapshot_root_path = None
        snapshot_error: Exception | None = None
        if self.temp_path.exists() and self.temp_ready_path.exists():
            try:
                snapshot_state = self._read_config_snapshot_state()
                snapshot_root_path = snapshot_state.src_root_path
            except (OSError, ValueError) as e:
                snapshot_error = e

        if (
            process_state is not None
            and snapshot_root_path is not None
            and (
                process_state.src_root_path != snapshot_root_path
                or process_state.installation_id != snapshot_state.installation_id
            )
        ):
            raise RuntimeError("SRC 进程状态与配置快照的安装实例不一致，拒绝自动清理")

        cleanup_targets: list[tuple[Path, int | None, str]] = []
        if process_state is not None:
            cleanup_targets.append(
                (
                    process_state.src_root_path,
                    process_state.webui_port,
                    process_state.installation_id,
                )
            )
        if snapshot_root_path is not None and all(
            root_path != snapshot_root_path for root_path, _, _ in cleanup_targets
        ):
            cleanup_targets.append(
                (
                    snapshot_root_path,
                    None,
                    snapshot_state.installation_id,
                )
            )

        assert_no_foreign_pending_snapshot(
            self.temp_ready_path,
            [
                self.src_root_path,
                *(root_path for root_path, _, _ in cleanup_targets),
            ],
        )
        if (
            process_state is None
            and snapshot_state is not None
            and (snapshot_state.src_root_path / "src.exe").is_file()
            and is_src_config_available(self.temp_path)
        ):
            try:
                validate_src_cleanup_paths(
                    snapshot_state.src_root_path,
                    snapshot_state.src_root_path / "src.exe",
                    snapshot_state.src_root_path / "config",
                    expected_installation_id=snapshot_state.installation_id,
                )
            except (OSError, ValueError) as e:
                raise RuntimeError(
                    "SRC 配置恢复事务的历史根目录范围不安全，拒绝自动回滚: "
                    f"{snapshot_state.src_root_path}"
                ) from e
            recover_interrupted_src_config_swap(
                snapshot_state.src_root_path / "config",
                expected_installation_id=snapshot_state.installation_id,
            )

        cleanup_success = True
        for (
            cleanup_root_path,
            webui_port,
            installation_id,
        ) in cleanup_targets:
            target_success = await kill_src_processes(
                ProcessManager(),
                src_exe_path=cleanup_root_path / "src.exe",
                src_root_path=cleanup_root_path,
                src_set_path=cleanup_root_path / "config",
                webui_port=webui_port,
                listener_wait_timeout=2.0,
                expected_installation_id=installation_id,
            )
            if not target_success:
                cleanup_success = False
            else:
                self._cleaned_src_roots.add(cleanup_root_path.resolve())
        self.process_cleanup_success = cleanup_success
        if not cleanup_success:
            raise RuntimeError("SRC 进程清理未完成，请关闭 SRC 后重试")
        if process_state_error is not None:
            raise RuntimeError(
                f"SRC 进程状态无法验证: {self.src_process_state_path}"
            ) from process_state_error
        if snapshot_error is not None:
            raise RuntimeError(
                f"SRC 配置快照状态无法验证: {self.temp_ready_path}"
            ) from snapshot_error

        if process_state is not None and process_state.config_user_id is not None:
            if (
                snapshot_state is None
                or snapshot_state.owner_version != process_state.owner_version
                or snapshot_state.config_user_id != process_state.config_user_id
            ):
                raise RuntimeError(
                    "SRC 脚本设置会话归属无法由配置快照验证，已拒绝自动保存"
                )
            self._save_pending_config_session(process_state)

        # 上次异常收尾保留了原配置快照时，确认进程退出后再恢复。
        if self.temp_path.exists():
            if self.temp_ready_path.exists():
                snapshot_available = is_src_config_available(self.temp_path)
                if not snapshot_available and is_src_config_available(
                    self.src_set_path
                ):
                    self._quarantine_config_snapshot("快照内容不完整")
                elif not snapshot_available:
                    raise RuntimeError(
                        "SRC 当前配置和待恢复快照均不可用，"
                        f"请手动检查: {self.src_set_path}, {self.temp_path}"
                    )
                elif snapshot_root_path == self.src_root_path:
                    logger.warning(f"检测到待恢复的 SRC 配置快照: {self.temp_path}")
                    self._restore_src_config_from_temp(
                        expected_installation_id=snapshot_state.installation_id,
                    )
                    self._retire_src_config_snapshot()
                elif snapshot_root_path is not None and snapshot_root_path.exists():
                    logger.warning(
                        "SRC 路径已变更，正在把原配置恢复到旧根目录: "
                        f"{snapshot_root_path}"
                    )
                    self._restore_src_config_from_temp(
                        snapshot_root_path / "config",
                        expected_installation_id=snapshot_state.installation_id,
                    )
                    self._retire_src_config_snapshot()
                elif is_src_config_available(self.src_set_path):
                    self._quarantine_config_snapshot(
                        f"快照属于其他 SRC 根目录: {snapshot_root_path}"
                    )
                else:
                    raise RuntimeError(
                        "SRC 路径已变更且当前配置不可用，旧配置快照已保留，"
                        f"请手动检查: {self.temp_path}"
                    )
            elif is_src_config_available(self.src_set_path):
                self._quarantine_config_snapshot("快照未完成提交")
            else:
                raise RuntimeError(
                    f"检测到未完成的 SRC 配置快照，请手动检查: {self.temp_path}"
                )
        else:
            self.temp_ready_path.unlink(missing_ok=True)
            self.src_process_state_path.unlink(missing_ok=True)

    async def _cleanup_current_src_root(self) -> None:
        """在备份当前配置前确认当前 SRC 根目录已停止。"""

        current_root_path = self.src_root_path.resolve()
        installation_id = read_src_installation_id(current_root_path)
        if current_root_path not in self._cleaned_src_roots:
            cleanup_success = await kill_src_processes(
                ProcessManager(),
                src_exe_path=self.src_exe_path,
                src_root_path=self.src_root_path,
                src_set_path=self.src_set_path,
                webui_port=None,
                listener_wait_timeout=2.0,
                expected_installation_id=installation_id,
            )
            self.process_cleanup_success = cleanup_success
            if not cleanup_success:
                raise RuntimeError("SRC 进程清理未完成，请关闭 SRC 后重试")
            self._cleaned_src_roots.add(current_root_path)
        validate_src_installation(current_root_path, installation_id)
        self.src_installation_id = installation_id

    def _clear_uncommitted_config_snapshots(self) -> None:
        """清理不会被当作有效快照的中间目录。"""

        staging_path = self.temp_path.with_name(self.temp_path.name + ".tmp")
        discarded_path = self.temp_path.with_name(self.temp_path.name + ".discard")
        if staging_path.exists():
            shutil.rmtree(staging_path)
        if discarded_path.exists():
            # discard 只会在进程清理和配置恢复成功后生成；即使上次在删除
            # marker/state 前崩溃，也可以完成退休，避免孤立 state 再次授权清理。
            shutil.rmtree(discarded_path)
            self.temp_ready_path.unlink(missing_ok=True)
            self.src_process_state_path.unlink(missing_ok=True)
        if not self.temp_path.exists():
            self.temp_ready_path.unlink(missing_ok=True)

    def _recover_default_user_config_transaction(self) -> None:
        """在配置检查前恢复非设置任务依赖的默认账号配置。"""

        if self.task_info.mode != TaskMode.AUTO_PROXY:
            return
        recover_src_user_config(
            Path.cwd() / f"data/{self.script_info.script_id}/Default/ConfigFile"
        )

    def _backup_src_config_to_temp(self) -> None:
        """完整复制配置后，再提交为可恢复的 Temp 快照。"""

        validate_src_installation(
            self.src_root_path,
            self.src_installation_id,
        )
        staging_path = self.temp_path.with_name(self.temp_path.name + ".tmp")
        if staging_path.exists():
            shutil.rmtree(staging_path)
        if self.temp_path.exists():
            raise RuntimeError(f"SRC 配置快照已存在: {self.temp_path}")

        staging_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.src_set_path, staging_path)
        if not is_src_config_available(staging_path):
            raise RuntimeError(f"SRC 配置快照副本不完整，拒绝提交: {staging_path}")
        validate_src_installation(
            self.src_root_path,
            self.src_installation_id,
        )
        staging_path.rename(self.temp_path)
        write_src_config_snapshot_state(
            self.temp_ready_path,
            script_id=self.script_info.script_id,
            src_root_path=self.src_root_path,
            installation_id=self.src_installation_id,
            config_user_id=None,
        )

    def _save_pending_config_session(self, process_state: SrcProcessState) -> None:
        """进程确认退出后，先保存上次脚本设置会话再恢复原配置。"""

        user_id = process_state.config_user_id
        if user_id != "Default":
            try:
                user_id = str(uuid.UUID(user_id))
            except (TypeError, ValueError, AttributeError) as e:
                raise ValueError(f"SRC 脚本设置用户无效: {user_id}") from e

        if process_state.owner_version == 1 and user_id != "Default":
            # 旧进程记录只保存账号 ID。用稳定的恢复目录保留原件，避免覆盖现有账号或共享配置。
            installation_key = hashlib.sha256(
                process_state.installation_id.encode()
            ).hexdigest()
            config_path = (
                self.temp_path.parent / "RecoveredEdits" / user_id / installation_key
            )
            recovery_notice = (
                f"旧 SRC 会话的配置来源未记录，已将编辑结果保留到 {config_path}"
            )
        else:
            config_path = self.temp_path.parent / user_id / "ConfigFile"
            recovery_notice = None
        if has_committed_src_user_config_transaction(config_path):
            recover_src_user_config(config_path, preserve_commit_marker=True)
        else:
            source_path = process_state.src_root_path / "config"
            if not source_path.parent.exists():
                raise RuntimeError(
                    f"SRC 脚本设置目录已不存在，已保留待恢复状态: {source_path}"
                )
            validate_src_installation(
                process_state.src_root_path,
                process_state.installation_id,
            )
            if not is_src_config_available(source_path):
                raise RuntimeError(f"SRC 脚本设置配置不存在或已损坏: {source_path}")
            save_src_user_config(
                source_path,
                config_path,
                preserve_commit_marker=True,
                expected_installation_id=process_state.installation_id,
            )
        write_src_process_state(
            self.src_process_state_path,
            script_id=self.script_info.script_id,
            src_root_path=process_state.src_root_path,
            webui_port=process_state.webui_port,
            installation_id=process_state.installation_id,
            config_user_id=None,
        )
        recover_src_user_config(config_path)
        if recovery_notice:
            logger.warning(recovery_notice)
        else:
            logger.success(f"已保存上次中断的 SRC 脚本设置: {config_path}")

    def _read_config_snapshot_state(self) -> SrcConfigSnapshotState:
        """读取已提交配置快照的归属信息。"""

        return read_src_config_snapshot_state(
            self.temp_ready_path,
            expected_script_id=self.script_info.script_id,
        )

    def _quarantine_config_snapshot(self, reason: str) -> None:
        """隔离不应自动恢复的快照，避免覆盖现场配置。"""

        quarantine_path = self.temp_path.with_name(
            f"{self.temp_path.name}.untrusted-{uuid.uuid4().hex}"
        )
        self.temp_path.rename(quarantine_path)
        self.temp_ready_path.unlink(missing_ok=True)
        self.src_process_state_path.unlink(missing_ok=True)
        logger.warning(f"已隔离 SRC 配置快照: {quarantine_path}, 原因: {reason}")

    def _restore_src_config_from_temp(
        self,
        src_set_path: Path | None = None,
        *,
        expected_installation_id: str | None = None,
    ) -> None:
        """使用 Temp 快照替换 SRC 配置目录。"""

        if not self.temp_path.exists():
            return

        src_set_path = src_set_path or self.src_set_path
        if expected_installation_id is not None:
            validate_src_installation(
                src_set_path.parent,
                expected_installation_id,
            )
        temporary_path = src_set_path.with_name(src_set_path.name + ".tmp")
        backup_path = src_set_path.with_name(src_set_path.name + ".old")
        shutil.rmtree(temporary_path, ignore_errors=True)
        shutil.copytree(self.temp_path, temporary_path)
        if not is_src_config_available(temporary_path):
            raise RuntimeError(
                f"SRC 配置恢复副本不完整，保留当前配置: {temporary_path}"
            )
        if expected_installation_id is not None:
            validate_src_installation(
                src_set_path.parent,
                expected_installation_id,
            )
        if backup_path.exists():
            if not src_set_path.exists():
                backup_path.rename(src_set_path)
            elif is_src_config_available(src_set_path):
                shutil.rmtree(backup_path)
            elif is_src_config_available(backup_path):
                failed_path = src_set_path.with_name(
                    f"{src_set_path.name}.untrusted-{uuid.uuid4().hex}"
                )
                src_set_path.rename(failed_path)
                backup_path.rename(src_set_path)
                logger.warning(
                    f"已回滚损坏的 SRC 配置目录并保留现场副本: {failed_path}"
                )
            else:
                raise RuntimeError(
                    "SRC 配置恢复事务存在冲突，已保留所有副本: "
                    f"{src_set_path}, {backup_path}"
                )
        if expected_installation_id is not None:
            validate_src_installation(
                src_set_path.parent,
                expected_installation_id,
            )
        temporary_stat = temporary_path.stat()
        if src_set_path.exists():
            src_set_path.rename(backup_path)
        try:
            temporary_path.rename(src_set_path)
            if expected_installation_id is not None:
                validate_src_installation(
                    src_set_path.parent,
                    expected_installation_id,
                )
        except BaseException:
            if src_set_path.exists():
                restored_stat = src_set_path.stat()
                if (
                    restored_stat.st_dev,
                    restored_stat.st_ino,
                ) == (
                    temporary_stat.st_dev,
                    temporary_stat.st_ino,
                ):
                    src_set_path.rename(temporary_path)
            if backup_path.exists() and not src_set_path.exists():
                backup_path.rename(src_set_path)
            raise
        if not is_src_config_available(src_set_path):
            failed_path = src_set_path.with_name(
                f"{src_set_path.name}.untrusted-{uuid.uuid4().hex}"
            )
            src_set_path.rename(failed_path)
            if backup_path.exists():
                backup_path.rename(src_set_path)
            raise RuntimeError(
                f"SRC 配置恢复结果不完整，已回滚现场配置并保留恢复副本: {failed_path}"
            )
        if backup_path.exists():
            shutil.rmtree(backup_path)

    def _retire_src_config_snapshot(self) -> None:
        """先移出有效快照路径，再清理已恢复的快照。"""

        if not self.temp_path.exists():
            self.temp_ready_path.unlink(missing_ok=True)
            self.src_process_state_path.unlink(missing_ok=True)
            return

        discarded_path = self.temp_path.with_name(self.temp_path.name + ".discard")
        if discarded_path.exists():
            shutil.rmtree(discarded_path)
        self.temp_path.rename(discarded_path)
        self.temp_ready_path.unlink(missing_ok=True)
        self.src_process_state_path.unlink(missing_ok=True)
        shutil.rmtree(discarded_path, ignore_errors=True)

    async def _recover_unconfirmed_install(self) -> bool:
        """安装状态收发统一交给 #1916，不再等待 UI 用户确认。"""
        root = self.src_root_path
        marker = script_install.workspace(root) / "src-install.json"
        request = ScriptUpdateRequest(str(self.script_info.script_id), "SRC")
        adapter = SRCInstallAdapter(root)
        backup = ManagedInstallBackup(
            root, script_id=request.script_id, script_type="SRC"
        )
        if backup.read() is not None:
            result = await run_managed_script_install(
                request,
                install_root=root,
                inspect=adapter.inspect,
                acknowledge=adapter.acknowledge,
                install=adapter.install,
                ensure_idle=adapter.ensure_idle,
                after_rollback=adapter.after_rollback,
            )
        elif marker.exists() or marker.is_symlink():
            result = await auto_recover_script_install(
                request,
                install_root=root,
                inspect=adapter.inspect,
                acknowledge=adapter.acknowledge,
                reinstall=adapter.install,
            )
        else:
            return False
        # 公共入口已经确认并结算成功，此时清理安装器的自有完成证据。
        (script_install.workspace(root) / "src-native-install-complete.json").unlink(
            missing_ok=True
        )
        await self.script_info.set_log(f"SRC 安装状态已自动恢复：{result}")
        if self.task_info.mode == TaskMode.AUTO_PROXY:
            self._program_update_prepared = True
        return True

    async def _prepare_program_update(self) -> None:
        if self.task_info.mode != TaskMode.AUTO_PROXY or self._program_update_prepared:
            return
        root = self.src_root_path
        program_update.recover_deploy(root)
        marker = script_install.workspace(root) / "src-install.json"
        if self.script_config.Update.TakeoverEnabled:
            request = ScriptUpdateRequest(str(self.script_info.script_id), "SRC")

            async def prepare():
                try:
                    return await program_update.prepare_update(root)
                except (script_install.InstallUnsafeError, FluxdownCancelUnconfirmed):
                    raise
                except Exception as error:
                    raise DownloadRetryFailed(
                        source="SRC Git/CDN",
                        reason=f"下载或只读准备失败（{type(error).__name__}）",
                    ) from error

            candidate = await run_script_update_with_safe_fallback(
                request, prepare, root, SRCInstallAdapter(root).inspect
            )
            if isinstance(candidate, program_update.PreparedUpdate):
                adapter = SRCInstallAdapter(root, prepared=candidate)
                try:
                    result = await run_managed_script_install(
                        request,
                        install_root=root,
                        inspect=adapter.inspect,
                        acknowledge=adapter.acknowledge,
                        install=adapter.install,
                        ensure_idle=adapter.ensure_idle,
                        after_rollback=adapter.after_rollback,
                    )
                finally:
                    await adapter.ensure_idle()
                    program_update.remove_cached_git_objects(candidate.objects)
                (
                    script_install.workspace(root) / "src-native-install-complete.json"
                ).unlink(missing_ok=True)
            else:
                # 不更新或安全旧版回退时，也必须确认原生安装确实完整。
                await program_update.confirm_current_install(root)
                result = candidate or "SRC 当前已经是最新版本"
            await self.script_info.set_log(str(result))
        elif marker.exists() or marker.is_symlink():
            raise script_install.InstallUnsafeError(
                "SRC 有未确认更新标记，禁止继续启动"
            )
        self._program_update_prepared = True

    def build_worker(self, user, config):
        worker = {
            TaskMode.AUTO_PROXY: AutoProxyTask,
            TaskMode.SCRIPT_CONFIG: ScriptConfigTask,
            TaskMode.SCRIPT_VIEW: ScriptViewTask,
        }[self.task_info.mode]
        return worker(self, user, config)

    async def spawn(self, child):
        try:
            await super().spawn(child)
        finally:
            self.process_cleanup_success = child.process_cleanup_success
        if not self.process_cleanup_success:
            raise RuntimeError("SRC 清理未完成，保留快照并停止派发账号")

    async def finish(self):
        try:
            if not self.prepared or not self.process_cleanup_success:
                return
            if not is_src_config_available(self.temp_path):
                raise RuntimeError(f"SRC 快照不完整，已保留现场: {self.temp_path}")
            if not self.keep_native_edits:
                self._restore_src_config_from_temp(
                    expected_installation_id=self.src_installation_id
                )
            self._retire_src_config_snapshot()
            if self.task_info.mode == TaskMode.AUTO_PROXY:
                await config_manager.flush()
                await self._send_final_notification()
        finally:
            self._install_access.close()

    async def _send_final_notification(self):
        users = tuple(self.script_info.users.values())
        completed = sum(user.info.status == "完成" for user in users)
        failed = sum(user.info.status not in ("完成", "跳过") for user in users)
        title = (
            f"{datetime.now():%m-%d} | {self.script_info.info.name}的自动代理任务报告"
        )
        report = TaskResultReport.from_message(
            {
                "title": "自动代理任务报告",
                "script_name": self.script_info.info.name,
                "start_time": self.begin_time,
                "end_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "completed_count": completed,
                "uncompleted_count": failed,
                "result": self.script_info.result,
                "summary_title": title.replace(
                    "报告", "存在异常" if failed else "已完成！"
                ),
            }
        )
        try:
            await asyncio.wait_for(
                Notify.push_proxy_result(
                    title=title, report=report, task_info=self.task_info
                ),
                timeout=_NOTIFICATION_TIMEOUT_SECONDS,
            )
        except Exception as error:
            await self._report_notification_error("推送代理结果", error)

    async def _report_notification_error(
        self, operation: str, error: Exception
    ) -> None:
        logger.opt(exception=True).warning(f"{operation}时出现异常: {error}")
        try:
            await asyncio.wait_for(
                Publisher.send(
                    id=self.task_info.task_id,
                    type=protocol.TASK_NOTICE,
                    data=WSTaskNoticeData(
                        level="error",
                        message=f"{operation}时出现异常: {error}",
                    ),
                ),
                timeout=_WEBSOCKET_REPORT_TIMEOUT_SECONDS,
            )
        except Exception as report_error:
            logger.opt(exception=True).warning(f"上报 SRC 通知异常失败: {report_error}")
