"""SRC托管与账号配置，保留已有配置身份与字段。"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from typing import Annotated, ClassVar, Literal

from auto_mas_core import ScriptEntry, UserEntry
from auto_mas_core.host.config import (
    ConfigCollection,
    ConfigGroup,
    Virtual,
    collection,
    encrypted,
    legacy,
    tz,
    ui,
    virtual_field,
)
from auto_mas_core.host.config_types import (
    FilePath,
    FolderPath,
    WindowsNameString,
)
from auto_mas_core.host.models_config_queue import Webhook
from auto_mas_core.host.models_config_shared import (
    _tag_notes,
    _tag_proxy,
    _tag_remained_days,
    none_as,
)
from auto_mas_core.host.utils import constants as _utils_constants
from pydantic import Field

from .constants import STARRAIL_STAGE_BOOK


class SrcUserConfig(UserEntry):
    "SRC账号配置"

    related_config: ClassVar = {}

    class DataFields(ConfigGroup):
        LastProxyDate: Annotated[
            datetime, tz(_utils_constants.UTC4), ui(format="date")
        ] = Field(
            default_factory=lambda: datetime(2000, 1, 1, tzinfo=_utils_constants.UTC4),
            description="上次代理日期",
        )

        ProxyTimes: Annotated[int, Field(ge=0, le=9999)] = Field(
            default_factory=lambda: 0, description="代理次数"
        )

    Data: DataFields = Field(default_factory=DataFields)

    class InfoFields(ConfigGroup):
        Base: Annotated[
            Literal["shared", "independent", "native"],
            legacy(
                group="Info",
                name="Mode",
                convert=_utils_constants.normalize_base_source,
            ),
        ] = Field(
            default_factory=lambda: "shared",
            description="base 来源（shared=共享/independent=独立/native=原生）",
        )

        Id: str = Field(default_factory=lambda: "", description="账号 ID")

        IfQuickConfig: bool = Field(
            default_factory=lambda: True,
            description="是否启用覆写常规配置（与配置来源独立，按账号保存）",
        )

        IfScriptAfterTask: bool = Field(
            default_factory=lambda: False, description="任务后执行脚本"
        )

        IfScriptBeforeTask: bool = Field(
            default_factory=lambda: False, description="任务前执行脚本"
        )

        Name: WindowsNameString = Field(
            default_factory=lambda: "新账号", description="账号名称"
        )

        Notes: str = Field(default_factory=lambda: "无", description="备注")

        Password: Annotated[str, encrypted()] = Field(
            default_factory=lambda: "", description="密码"
        )
        RemainedDay: Annotated[int, Field(ge=-1, le=9999), none_as(-1)] = Field(
            default_factory=lambda: -1, description="剩余天数"
        )

        ScriptAfterTask: FilePath = Field(
            description="任务后脚本路径", default_factory=lambda: None
        )

        ScriptBeforeTask: FilePath = Field(
            description="任务前脚本路径", default_factory=lambda: None
        )

        Server: Literal[
            "CN-Official",
            "CN-Bilibili",
            "VN-Official",
            "OVERSEA-America",
            "OVERSEA-Asia",
            "OVERSEA-Europe",
            "OVERSEA-TWHKMO",
        ] = Field(default_factory=lambda: "CN-Official", description="游戏服务器")

        Status: bool = Field(default_factory=lambda: True, description="是否启用")

        Tag: Virtual[str] = Field(
            default_factory=lambda: None, description="账号标签信息"
        )

    Info: InfoFields = Field(default_factory=InfoFields)

    class NotifyFields(ConfigGroup):
        Enabled: bool = Field(default_factory=lambda: False, description="是否启用通知")

        IfSendMail: bool = Field(
            default_factory=lambda: False, description="是否发送邮件"
        )

        IfSendStatistic: bool = Field(
            default_factory=lambda: False, description="是否发送统计信息"
        )

        IfServerChan: bool = Field(
            default_factory=lambda: False, description="是否启用 Server 酱"
        )

        ServerChanKey: str = Field(
            default_factory=lambda: "", description="Server 酱密钥"
        )

        ToAddress: str = Field(default_factory=lambda: "", description="收件地址")

    Notify: NotifyFields = Field(default_factory=NotifyFields)

    class StageFields(ConfigGroup):
        Channel: Literal["Relic", "Materials", "Ornament"] = Field(
            default_factory=lambda: "Relic", description="关卡通道"
        )

        EchoOfWar: Literal[
            "-",
            "Echo_of_War_The_Comedy_of_Doom",
            "Echo_of_War_Rusted_Crypt_of_the_Iron_Carcass",
            "Echo_of_War_Glance_of_Twilight",
            "Echo_of_War_Inner_Beast_Battlefield",
            "Echo_of_War_Salutations_of_Ashen_Dreams",
            "Echo_of_War_Borehole_Planet_Past_Nightmares",
            "Echo_of_War_Divine_Seed",
            "Echo_of_War_End_of_the_Eternal_Freeze",
            "Echo_of_War_Destruction_Beginning",
        ] = Field(default_factory=lambda: "-", description="历战余响关卡")

        ExtractReservedTrailblazePower: bool = Field(
            default_factory=lambda: False, description="使用储备开拓力"
        )

        FuelReserve: Annotated[int, Field(ge=0, le=9999), none_as(0)] = Field(
            default_factory=lambda: 5, description="保留的燃料数量"
        )

        Materials: Literal[
            "-",
            "Calyx_Golden_Memories_Planarcadia",
            "Calyx_Golden_Memories_Amphoreus",
            "Calyx_Golden_Memories_Penacony",
            "Calyx_Golden_Memories_The_Xianzhou_Luofu",
            "Calyx_Golden_Memories_Jarilo_VI",
            "Calyx_Golden_Aether_Planarcadia",
            "Calyx_Golden_Aether_Amphoreus",
            "Calyx_Golden_Aether_Penacony",
            "Calyx_Golden_Aether_The_Xianzhou_Luofu",
            "Calyx_Golden_Aether_Jarilo_VI",
            "Calyx_Golden_Treasures_Planarcadia",
            "Calyx_Golden_Treasures_Amphoreus",
            "Calyx_Golden_Treasures_Penacony",
            "Calyx_Golden_Treasures_The_Xianzhou_Luofu",
            "Calyx_Golden_Treasures_Jarilo_VI",
            "Calyx_Crimson_Destruction_Herta_StorageZone",
            "Calyx_Crimson_Destruction_Luofu_ScalegorgeWaterscape",
            "Calyx_Crimson_Destruction_Planarcadia_InkfordHermitage",
            "Calyx_Crimson_Preservation_Herta_SupplyZone",
            "Calyx_Crimson_Preservation_Penacony_ClockStudiosThemePark",
            "Calyx_Crimson_The_Hunt_Jarilo_OutlyingSnowPlains",
            "Calyx_Crimson_The_Hunt_Penacony_SoulGladScorchsandAuditionVenue",
            "Calyx_Crimson_The_Hunt_Amphoreus_MemortisShoreRuinsofTime",
            "Calyx_Crimson_Abundance_Jarilo_BackwaterPass",
            "Calyx_Crimson_Abundance_Luofu_FyxestrollGarden",
            "Calyx_Crimson_Erudition_Jarilo_RivetTown",
            "Calyx_Crimson_Erudition_Penacony_PenaconyGrandTheater",
            "Calyx_Crimson_Erudition_Planarcadia_SeafeldTVTower",
            "Calyx_Crimson_Harmony_Jarilo_RobotSettlement",
            "Calyx_Crimson_Harmony_Penacony_TheReverieDreamscape",
            "Calyx_Crimson_Nihility_Jarilo_GreatMine",
            "Calyx_Crimson_Nihility_Luofu_AlchemyCommission",
            "Calyx_Crimson_Nihility_Amphoreus_RadiantScarwoodGroveofEpiphany",
            "Calyx_Crimson_Remembrance_Amphoreus_StrifeRuinsCastrumKremnos",
            "Calyx_Crimson_Elation_Planarcadia_WorldEndTavern",
            "Stagnant_Shadow_Spike",
            "Stagnant_Shadow_Perdition",
            "Stagnant_Shadow_Duty",
            "Stagnant_Shadow_Deepsheaf",
            "Stagnant_Shadow_Blaze",
            "Stagnant_Shadow_Scorch",
            "Stagnant_Shadow_Roast",
            "Stagnant_Shadow_Ire",
            "Stagnant_Shadow_Ashes",
            "Stagnant_Shadow_Rime",
            "Stagnant_Shadow_Icicle",
            "Stagnant_Shadow_Nectar",
            "Stagnant_Shadow_Sirens",
            "Stagnant_Shadow_Fulmination",
            "Stagnant_Shadow_Doom",
            "Stagnant_Shadow_Mechwolf",
            "Stagnant_Shadow_Soundburst",
            "Stagnant_Shadow_Gust",
            "Stagnant_Shadow_Celestial",
            "Stagnant_Shadow_Gloam",
            "Stagnant_Shadow_Cinders",
            "Stagnant_Shadow_Quanta",
            "Stagnant_Shadow_Abomination",
            "Stagnant_Shadow_Gelidmoon",
            "Stagnant_Shadow_Devour",
            "Stagnant_Shadow_Mirage",
            "Stagnant_Shadow_Puppetry",
            "Stagnant_Shadow_Timbre",
            "Stagnant_Shadow_Sloggyre",
        ] = Field(default_factory=lambda: "-", description="材料关卡")

        Ornament: Literal[
            "-",
            "Divergent_Universe_Bugs_Incoming",
            "Divergent_Universe_Gilded_Recollection",
            "Divergent_Universe_Within_the_West_Wind",
            "Divergent_Universe_Moonlit_Blood",
            "Divergent_Universe_Unceasing_Strife",
            "Divergent_Universe_Famished_Worker",
            "Divergent_Universe_Eternal_Comedy",
            "Divergent_Universe_To_Sweet_Dreams",
            "Divergent_Universe_Pouring_Blades",
            "Divergent_Universe_Fruit_of_Evil",
            "Divergent_Universe_Permafrost",
            "Divergent_Universe_Gentle_Words",
            "Divergent_Universe_Smelted_Heart",
            "Divergent_Universe_Untoppled_Walls",
        ] = Field(default_factory=lambda: "-", description="饰品关卡")

        Relic: Literal[
            "-",
            "Cavern_of_Corrosion_Path_of_the_Secret_Actor",
            "Cavern_of_Corrosion_Path_of_Insight",
            "Cavern_of_Corrosion_Path_of_Possession",
            "Cavern_of_Corrosion_Path_of_Hidden_Salvation",
            "Cavern_of_Corrosion_Path_of_Thundersurge",
            "Cavern_of_Corrosion_Path_of_Aria",
            "Cavern_of_Corrosion_Path_of_Uncertainty",
            "Cavern_of_Corrosion_Path_of_Cavalier",
            "Cavern_of_Corrosion_Path_of_Dreamdive",
            "Cavern_of_Corrosion_Path_of_Darkness",
            "Cavern_of_Corrosion_Path_of_Elixir_Seekers",
            "Cavern_of_Corrosion_Path_of_Conflagration",
            "Cavern_of_Corrosion_Path_of_Holy_Hymn",
            "Cavern_of_Corrosion_Path_of_Providence",
            "Cavern_of_Corrosion_Path_of_Drifting",
            "Cavern_of_Corrosion_Path_of_Jabbing_Punch",
            "Cavern_of_Corrosion_Path_of_Gelid_Wind",
        ] = Field(default_factory=lambda: "-", description="遗器关卡")

        SimulatedUniverseWorld: Literal[
            "-",
            "Simulated_Universe_World_3",
            "Simulated_Universe_World_4",
            "Simulated_Universe_World_5",
            "Simulated_Universe_World_6",
            "Simulated_Universe_World_8",
        ] = Field(default_factory=lambda: "-", description="模拟宇宙关卡")

        UseFuel: bool = Field(default_factory=lambda: False, description="使用燃料")

    Stage: StageFields = Field(default_factory=StageFields)
    Notify_CustomWebhooks: ConfigCollection[Webhook] = collection(Webhook)

    def getTags(self) -> str:
        """生成账号标签列表，返回JSON字符串格式的TagItem列表"""
        tags = []
        tags.append(_tag_proxy(self))
        tags.append(_tag_remained_days(self))
        if not self.Info.IfQuickConfig:
            tags.append(_tag_notes(self))
            return json.dumps(tags, ensure_ascii=False)

        tags.append(
            {
                "text": f"关卡：{STARRAIL_STAGE_BOOK.get(getattr(self.Stage, self.Stage.Channel), '未知关卡')}",
                "color": "blue",
            }
        )
        tags.append(
            {
                "text": f"周本：{STARRAIL_STAGE_BOOK.get(self.Stage.EchoOfWar, '未知关卡')}",
                "color": "blue",
            }
        )
        tags.append(
            {
                "text": f"模拟宇宙：{STARRAIL_STAGE_BOOK.get(self.Stage.SimulatedUniverseWorld, '未知关卡')}",
                "color": "blue",
            }
        )
        tags.append(_tag_notes(self))
        return json.dumps(tags, ensure_ascii=False)

    @virtual_field("Info.Tag")
    def compute_Info_Tag(self):
        try:
            return self.getTags()
        except Exception as exc:
            return str(exc)

    class Metadata(UserEntry.Info):
        config_path: Virtual[str] = None
        name: Virtual[str] = None  # pyright: ignore[reportIncompatibleVariableOverride]
        enabled: Virtual[bool] = None  # pyright: ignore[reportIncompatibleVariableOverride]

    info: Metadata = Field(default_factory=Metadata)  # pyright: ignore[reportIncompatibleVariableOverride]

    @virtual_field("info.name")
    def plugin_name(self):
        """向宿主提供已有账号名称。"""
        return self.Info.Name

    @virtual_field("info.config_path")
    def plugin_config_path(self) -> str:
        """给账号编辑页提供当前来源对应的配置文件夹。"""
        from auto_mas_core import Config

        parent = self.parent
        script = parent.parent if parent is not None else None
        if not isinstance(script, SrcConfig):
            return ""
        if self.Info.Base == "native":
            return str(script.Info.Path / "config") if script.Info.Path else ""
        owner = self.uid if self.Info.Base == "independent" else "Default"
        return str(Config.data_path / str(script.uid) / str(owner) / "ConfigFile")

    @virtual_field("info.enabled")
    def plugin_enabled(self):
        """向宿主提供已有账号启用状态。"""
        return self.Info.Status


class SrcConfig(ScriptEntry):
    "SRC配置"

    class UpdateFields(ConfigGroup):
        TakeoverEnabled: bool = Field(
            default=False,
            description="运行前由 MAS 预取 SRC 原生代码源并调用官方安装器完成更新，普通下载失败后沿用当前安装；运行期暂停原生更新并在任务结束后恢复",
        )

    Update: UpdateFields = Field(default_factory=UpdateFields)

    related_config: ClassVar = {}

    class InfoFields(ConfigGroup):
        Name: str = Field(
            default_factory=lambda: "新 SRC 托管", description="SRC 托管名称"
        )

        Path: FolderPath = Field(default_factory=lambda: None, description="SRC 路径")

    Info: InfoFields = Field(default_factory=InfoFields)

    class RunFields(ConfigGroup):
        GameUpdateTimeLimit: Annotated[int, Field(ge=1, le=9999), none_as(1)] = Field(
            default_factory=lambda: 60,
            description="游戏更新时间限制（分钟）",
        )

        HardTimeLimit: Annotated[int, Field(ge=1, le=9999), none_as(1)] = Field(
            default_factory=lambda: 120,
            description="单账号运行总时限（分钟），包含等待和全部重试",
        )

        IfAutoInstallGameApk: bool = Field(
            default_factory=lambda: False,
            description="版本落后时是否由 MAS 自动下载并安装安装包",
        )

        IfCheckGameUpdate: bool = Field(
            default_factory=lambda: False, description="是否在登录游戏前检查游戏更新"
        )

        ProxyTimesLimit: Annotated[int, Field(ge=0, le=9999), none_as(0)] = Field(
            default_factory=lambda: 0, description="代理次数限制"
        )

        RunTimeLimit: Annotated[int, Field(ge=1, le=9999), none_as(1)] = Field(
            default_factory=lambda: 10,
            description="运行时间限制（分钟）",
        )

        RunTimesLimit: Annotated[int, Field(ge=1, le=9999), none_as(1)] = Field(
            default_factory=lambda: 3, description="运行次数限制"
        )

        TaskTransitionMethod: Literal["ExitGame", "ExitEmulator"] = Field(
            default_factory=lambda: "ExitGame", description="任务切换方式"
        )

    Run: RunFields = Field(default_factory=RunFields)
    users: ConfigCollection[SrcUserConfig] = collection(SrcUserConfig)

    class Metadata(ScriptEntry.Info):
        name: Virtual[str] = None  # pyright: ignore[reportIncompatibleVariableOverride]

    info: Metadata = Field(default_factory=Metadata)  # pyright: ignore[reportIncompatibleVariableOverride]

    @virtual_field("info.name")
    def plugin_name(self):
        """向宿主提供已有托管名称。"""
        return self.Info.Name

    @classmethod
    def normalize_payload(cls, data):
        """迁移原账号集合名称，保留 UID、顺序及字段。"""
        data = deepcopy(data)
        if "UserData" in data:
            data["users"] = data.pop("UserData")
        if "UserData" in data.get("SubConfigsInfo", {}):
            data["SubConfigsInfo"]["users"] = data["SubConfigsInfo"].pop("UserData")
        return data

    async def _activate_from_payload(self, payload, *, regenerate_uuids=False):
        """在集合激活边界转换旧配置文档。"""
        await super()._activate_from_payload(
            type(self).normalize_payload(payload), regenerate_uuids=regenerate_uuids
        )
