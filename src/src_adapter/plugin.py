"""注册 SRC 的托管、账号、任务模式和备份接口。"""

from auto_mas_core import Config, ScriptAdapterPlugin, ScriptTypeDecl, TaskMode

from .backup_api import router
from .config import SrcConfig, SrcUserConfig
from .manager import SrcExpander

# 同一个开发版 SDK 号码同时出现在拆分前后的宿主。安装流程会在 load 成功后确认发行包。
_builtin = Config.ScriptConfig.entry_types.get("SrcConfig")
if _builtin is not None and _builtin.__module__.startswith("app."):
    raise ImportError("当前 AUTO-MAS 仍内置 SRC，请先更新至已迁移 SRC 专项的宿主")


class Plugin(ScriptAdapterPlugin):
    script_type_key = "SRC"
    api_router = router
    decl = ScriptTypeDecl(
        script_type=SrcConfig,
        user_type=SrcUserConfig,
        expander_class=SrcExpander,
        supported_modes=(
            TaskMode.AUTO_PROXY,
            TaskMode.SCRIPT_CONFIG,
            TaskMode.SCRIPT_VIEW,
        ),
        game_kinds=("emulator",),
        activity_game="starrail",
    )

    async def pre_enable(self):
        refs = {"PlanConfig": Config.PlanConfig, "games": Config.GameConfig}
        SrcConfig.related_config = refs
        SrcUserConfig.related_config = refs
