<!-- auto-mas:i18n
{"display_name_i18n":{"zh_CN":"SRC 专项","en":"StarRailCopilot adapter","ja":"StarRailCopilot アダプター"},"description_i18n":{"zh_CN":"使用 SRC 执行星穹铁道托管、编辑账号配置和恢复备份。","en":"Run StarRailCopilot accounts, edit configurations and restore backups.","ja":"StarRailCopilot の実行、設定編集、バックアップの復元を行います。"},"tags_i18n":{"zh_CN":["星穹铁道","脚本适配"],"en":["Star Rail","Script adapter"],"ja":["スターレイル","スクリプト"]}}
-->

# AUTO-MAS SRC Adapter

从 AUTO-MAS 插件中心安装并启用 SRC 专项。新建托管时选择已安装的 StarRailCopilot 目录和模拟器，在账号页打开 SRC 原生界面编辑配置。由内置 SRC 升级的托管继续使用已有账号和备份。

兼容范围由宿主的 SRC 迁移决定。配套宿主从 [SRC 插件迁移提交](https://github.com/jinghero/AUTO-MAS/commit/a0b62cb01) 起接入。PyPI 元数据中的 5.9.0b1 是此开发周期的 SDK 编号。已发布的 AUTO-MAS v5.9.0-beta.1 没有插件中心。仍内置 SRC 的开发版会在加载发行包时提示先更新宿主。面向普通用户的安装入口随包含该迁移的桌面版交付。

开发时将本仓库克隆到配套宿主的 `plugins/src_adapter`，通过插件中心加载。测试时设置 `AUTO_MAS_HOST_SOURCE` 指向宿主检出目录，用宿主的 Python 3.12 环境运行 `python -m pytest tests`。

发布由 AUTO-MAS-Project 的 `publish.yml` 使用 `pypi` 环境与同组织的 PyPI Trusted Publisher 完成。只有匹配包版本的发布标签会上传分发包。
