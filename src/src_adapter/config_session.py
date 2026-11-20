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
from contextlib import suppress
from pathlib import Path

from auto_mas_core import ScriptConfigWorker, ScriptViewWorker, TaskMode
from auto_mas_core.host.core import Publisher
from auto_mas_core.host.core_ws import protocol
from auto_mas_core.host.models_schema import WSTaskNoticeData
from auto_mas_core.host.task_proxy_helpers import (
    CONFIG_BASE_INDEPENDENT,
    CONFIG_BASE_NATIVE,
    CONFIG_BASE_SHARED,
    read_config_base,
)
from auto_mas_core.host.utils import ProcessManager, get_logger
from auto_mas_core.host.utils import io as _utils_io

from .tools import (
    archive_mas_runtime_backup,
    kill_src_processes,
    mas_config_dir,
    poor_yaml_read,
    poor_yaml_write,
    promote_src_config_update,
    read_overlay_values,
    read_src_webui_port,
    recover_src_user_config,
    save_src_user_config,
    stage_src_config_update,
    validate_src_installation,
    write_src_config_snapshot_state,
    write_src_process_state,
)

logger = get_logger("SRC 脚本设置")


class SrcGuiSession:
    """共用 SRC WebUI 生命周期，执行模式决定是否保存编辑结果。"""

    def __init__(self, expander, user, config):
        super().__init__(expander, user, config)
        self.script_config = expander.script_config
        self.src_installation_id = expander.src_installation_id
        self.view_only = self.mode == TaskMode.SCRIPT_VIEW
        self.cur_user_item = user
        self.src_webui_port: int | None = None
        self.config_session_started = False
        self.process_cleanup_success = True
        self.prepared = False

    def _mas_owner(self) -> str | None:
        """本会话的 MAS 配置目录 owner；原生来源时返回 ``None``。

        与运行下发（AutoProxy ``set_src``）同一套来源规则。会话的下发与
        回写此前硬编码用户目录，shared 来源账号的会话改动运行时根本不读（改了
        白改），配置备份也因此采不到会话现场；现对齐运行态。原生来源账号没有
        MAS 托管配置目录，返回 ``None``。
        """

        user_id = self.cur_user_item.user_id
        if user_id == "Default":
            return "Default"
        base = read_config_base(self.user_config, CONFIG_BASE_SHARED)
        if base == CONFIG_BASE_NATIVE:
            return None
        return user_id if base == CONFIG_BASE_INDEPENDENT else "Default"

    async def prepare(self):

        self.src_process_manager = ProcessManager()
        self.wait_event = asyncio.Event()

        src_root_path = self.script_config.Info.Path
        if src_root_path is None:
            raise ValueError("Path未配置，请检查路径设置")
        self.src_root_path = src_root_path
        self.src_set_path = self.src_root_path / "config"
        self.src_exe_path = self.src_root_path / "src.exe"
        self.src_process_state_path = (
            Path.cwd() / f"data/{self.script_info.script_id}/Temp.process.json"
        )
        self.temp_ready_path = (
            Path.cwd() / f"data/{self.script_info.script_id}/Temp.ready"
        )
        self.prepared = True

    async def main_logic(self):

        await self.set_src()
        self.src_webui_port = read_src_webui_port(self.src_set_path)
        validate_src_installation(
            self.src_root_path,
            self.src_installation_id,
        )
        write_src_process_state(
            self.src_process_state_path,
            script_id=self.script_info.script_id,
            src_root_path=self.src_root_path,
            webui_port=self.src_webui_port,
            installation_id=self.src_installation_id,
            config_user_id=None,
        )
        logger.info(f"启动 SRC 进程: {self.src_exe_path}")
        self.wait_event.clear()
        validate_src_installation(
            self.src_root_path,
            self.src_installation_id,
        )
        self.process_cleanup_success = False
        await self.src_process_manager.open_process(self.src_exe_path)
        write_src_config_snapshot_state(
            self.temp_ready_path,
            script_id=self.script_info.script_id,
            src_root_path=self.src_root_path,
            installation_id=self.src_installation_id,
            # 查看会话不登记归属：中断恢复路径（_save_pending_config_session）
            # 只在归属可验证时回写，查看会话的注入现场绝不能保存回用户目录
            config_user_id=None if self.view_only else self._mas_owner(),
        )
        write_src_process_state(
            self.src_process_state_path,
            script_id=self.script_info.script_id,
            src_root_path=self.src_root_path,
            webui_port=self.src_webui_port,
            installation_id=self.src_installation_id,
            config_user_id=None if self.view_only else self._mas_owner(),
        )
        self.config_session_started = True
        await self.wait_event.wait()

    async def set_src(self):
        """配置SRC运行参数"""

        logger.info(f"开始配置SRC运行参数: 设置账号 {self.cur_user_item.user_id}")

        self.process_cleanup_success = False
        cleanup_success = await kill_src_processes(
            self.src_process_manager,
            src_exe_path=self.src_exe_path,
            src_root_path=self.src_root_path,
            src_set_path=self.src_set_path,
            webui_port=self.src_webui_port,
            listener_wait_timeout=2.0,
            expected_installation_id=self.src_installation_id,
        )
        self.process_cleanup_success = cleanup_success
        if not cleanup_success:
            raise RuntimeError("未能完全中止 SRC 进程")
        validate_src_installation(
            self.src_root_path,
            self.src_installation_id,
        )

        # 查看会话的托管级入口：原生目录即所选备份，跳过下发与注入
        if self.view_only and self.cur_user_item.user_id == "Default":
            logger.info("SRC 查看会话跳过配置下发: 原生目录即所选备份")
            return

        # 原生来源会话：安装目录原生配置即现场，MAS 零写入（含归档）；SRC
        # webui 内的编辑由本体落盘并保留
        owner = self._mas_owner()
        if owner is None:
            logger.info("SRC 原生来源会话: 直接使用安装目录原生配置, MAS 零写入")
            return

        config_path = mas_config_dir(self.script_info.script_id, owner)
        recover_src_user_config(config_path)

        # 下发前归档下发源到账号池（会话保存会覆盖它；带页面核心字段侧车，
        # 指纹去重，失败不阻断会话）。池归属是当前真实账号（shared 来源多账号
        # 各自持有共享 Default 目录的快照），目标路径才按 owner 解析。
        # native 池由 manager.prepare 在任务级一次性归档
        overlay = (
            read_overlay_values(self.user_config)
            if self.cur_user_item.user_id != "Default"
            else None
        )
        with suppress(Exception):
            archive_mas_runtime_backup(
                self.script_info.script_id,
                self.cur_user_item.user_id,
                config_path,
                overlay=overlay,
                # 备份标注来源：tri_state 池跨来源恢复靠它切回
                base=(
                    CONFIG_BASE_INDEPENDENT
                    if owner == self.cur_user_item.user_id
                    else CONFIG_BASE_SHARED
                ),
            )

        staging_path = stage_src_config_update(
            self.src_set_path,
            expected_installation_id=self.src_installation_id,
            overlay_path=config_path if config_path.exists() else None,
        )

        src_set = _utils_io.read_file(staging_path / "src.json")
        deploy_set = poor_yaml_read((staging_path / "deploy.yaml"))

        # 不直接运行任务
        deploy_set["Run"] = None

        # 模拟器基础配置
        src_set["Alas"]["Emulator"]["GameClient"] = "android"
        src_set["Alas"]["Emulator"]["GameLanguage"] = "cn"
        src_set["Alas"]["Emulator"]["AdbRestart"] = True

        # 错误处理方式
        src_set["Alas"]["Error"]["Restart"] = "game"

        # 任务间切换方式
        src_set["Alas"]["Optimization"]["WhenTaskQueueEmpty"] = "close_game"

        # 养成规划
        src_set["Dungeon"]["PlannerTarget"]["Enable"] = False

        _utils_io.write_file(staging_path / "src.json", src_set)
        poor_yaml_write(
            deploy_set,
            staging_path / "deploy.yaml",
            (
                staging_path / "deploy.template-cn.yaml"
                if (staging_path / "deploy.template-cn.yaml").exists()
                else None
            ),
        )
        promote_src_config_update(
            self.src_set_path,
            staging_path,
            expected_installation_id=self.src_installation_id,
        )
        logger.success(f"SRC运行参数配置完成: 设置账号 {self.cur_user_item.user_id}")

    async def finish(self):

        if not self.prepared:
            return

        self.process_cleanup_success = False
        cleanup_success = await kill_src_processes(
            self.src_process_manager,
            src_exe_path=self.src_exe_path,
            src_root_path=self.src_root_path,
            src_set_path=self.src_set_path,
            webui_port=self.src_webui_port,
            listener_wait_timeout=2.0,
            expected_installation_id=self.src_installation_id,
        )
        self.process_cleanup_success = cleanup_success
        if not cleanup_success:
            raise RuntimeError("未能完全中止 SRC 进程，请关闭 SRC 后重试脚本设置")

        if not self.config_session_started:
            return

        validate_src_installation(
            self.src_root_path,
            self.src_installation_id,
        )

        # 查看会话：只读预览，不把安装 config/ 回写用户目录（安装现场由
        # manager 的任务前快照还原）；webui 内的改动一律丢弃
        if self.view_only:
            logger.success("SRC 查看结束（只读，不回写配置）")
            await self.cur_user_item.set_state(status="完成")
            return

        # 原生来源会话：MAS 零写入，安装目录配置由本体保存并保留，不回写 MAS 目录
        owner = self._mas_owner()
        if owner is None:
            self.expander.keep_native_edits = True
            logger.success("SRC 原生来源会话: 配置由脚本原生 GUI 保存")
            await self.cur_user_item.set_state(status="完成")
            return

        config_path = mas_config_dir(self.script_info.script_id, owner)
        self.process_cleanup_success = False
        save_src_user_config(
            self.src_set_path,
            config_path,
            preserve_commit_marker=True,
            expected_installation_id=self.src_installation_id,
        )
        write_src_process_state(
            self.src_process_state_path,
            script_id=self.script_info.script_id,
            src_root_path=self.src_root_path,
            webui_port=self.src_webui_port,
            installation_id=self.src_installation_id,
            config_user_id=None,
        )
        recover_src_user_config(config_path)
        self.process_cleanup_success = True

    async def on_crash(self, e: Exception):
        await self.cur_user_item.set_state(status="异常")
        logger.opt(exception=True).warning(f"脚本设置任务出现异常: {e}")
        try:
            await asyncio.wait_for(
                Publisher.send(
                    id=self.task_info.task_id,
                    type=protocol.TASK_NOTICE,
                    data=WSTaskNoticeData(
                        level="error", message=f"脚本设置任务出现异常: {e}"
                    ),
                ),
                timeout=5,
            )
        except Exception as report_error:
            logger.opt(exception=True).warning(
                f"上报 SRC 脚本设置异常失败: {report_error}"
            )


class ScriptConfigTask(SrcGuiSession, ScriptConfigWorker):
    """在 SRC 原生 GUI 编辑配置。"""


class ScriptViewTask(SrcGuiSession, ScriptViewWorker):
    """查看备份，退出时丢弃 GUI 改动。"""
