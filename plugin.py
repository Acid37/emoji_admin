"""Emoji 管理后台插件入口。"""

from __future__ import annotations

from src.core.components.base.plugin import BasePlugin
from src.core.components.loader import register_plugin
from src.kernel.logger import get_logger

from .router.emoji_admin_router import EmojiAdminRouter
from .service import EmojiAdminService


logger = get_logger("emoji_admin_plugin")


@register_plugin
class EmojiAdminPlugin(BasePlugin):
    """独立的表情包管理后台插件。"""

    plugin_name: str = "emoji_admin"
    plugin_description: str = "表情包管理后台插件"
    plugin_version: str = "1.0.0"

    configs: list[type] = []
    dependent_components: list[str] = []

    def get_components(self) -> list[type]:
        """返回插件提供的组件。"""

        return [EmojiAdminRouter, EmojiAdminService]

    async def on_plugin_loaded(self) -> None:
        """插件加载完成后记录日志。"""

        logger.info("emoji_admin 插件已加载，后台路径: /emoji-admin/")

    async def on_plugin_unloaded(self) -> None:
        """插件卸载前记录日志。"""

        logger.info("emoji_admin 插件已卸载")


__all__ = ["EmojiAdminPlugin"]
