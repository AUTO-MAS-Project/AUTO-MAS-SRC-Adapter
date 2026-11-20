"""注册 SRC 的托管、账号、任务模式和备份接口。"""

from auto_mas_core import Config, ScriptAdapterPlugin, ScriptTypeDecl, TaskMode

from .backup_api import router
from .config import SrcConfig, SrcUserConfig
from .manager import SrcExpander


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
        existing = Config.ScriptConfig.entry_types.get("SrcConfig")
        if existing is not None and existing.__module__.startswith("app."):
            raise RuntimeError("请先升级到支持独立 SRC 插件的 AUTO-MAS 版本")
        refs = {"PlanConfig": Config.PlanConfig, "games": Config.GameConfig}
        SrcConfig.related_config = refs
        SrcUserConfig.related_config = refs
