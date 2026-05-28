"""Emoji 管理后台 Router。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from src.core.components.base.router import BaseRouter

from ..service import EmojiAdminService

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin


class EmojiUpdatePayload(BaseModel):
    """表情包更新请求体。"""

    description: str = Field(..., description="表情包描述")
    tags: list[str] = Field(default_factory=list, description="表情包标签")


class EmojiBatchDeletePayload(BaseModel):
    """表情包批量删除请求体。"""

    meme_ids: list[str] = Field(default_factory=list, description="需要删除的表情包 ID 列表")


class EmojiAdminRouter(BaseRouter):
    """表情包管理后台路由。"""

    router_name: str = "emoji_admin"
    router_description: str = "表情包管理后台"
    custom_route_path: str = "/emoji-admin"
    cors_origins: list[str] = ["*"]

    def __init__(self, plugin: "BasePlugin") -> None:
        """初始化路由。"""

        self._service: EmojiAdminService | None = None
        super().__init__(plugin)

    def _get_service(self) -> EmojiAdminService:
        """获取后台服务。"""

        if self._service is None:
            self._service = EmojiAdminService(plugin=self.plugin)
        return self._service

    @staticmethod
    def _html_path() -> Path:
        """获取 HTML 文件路径。"""

        return Path(__file__).with_name("emoji_admin.html")

    @classmethod
    def _load_html(cls) -> str:
        """读取 HTML 页面。"""

        try:
            return cls._html_path().read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise RuntimeError("emoji_admin.html 不存在，无法加载管理后台") from exc

    def register_endpoints(self) -> None:
        """注册页面与 API。"""

        @self.app.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def admin_page() -> HTMLResponse:
            """返回管理页面。"""

            from fastapi.responses import HTMLResponse as _HTMLResponse

            resp = _HTMLResponse(self._load_html())
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
            return resp

        @self.app.get("/api/memes")
        async def list_memes(keyword: str | None = Query(default=None, description="关键词过滤")) -> dict[str, Any]:
            """列出所有表情包。"""

            return await self._get_service().get_dashboard_payload(keyword=keyword)

        @self.app.get("/api/memes/{meme_id}")
        async def get_meme_detail(meme_id: str) -> dict[str, Any]:
            """获取单个表情包详情。"""

            detail = await self._get_service().get_meme_detail(meme_id)
            if detail is None:
                raise HTTPException(status_code=404, detail="表情包不存在")
            return detail

        @self.app.get("/api/image/{meme_id}")
        async def get_image(meme_id: str) -> FileResponse:
            """返回表情包图片。"""

            path = await self._get_service().get_preview_path(meme_id)
            if path is None:
                raise HTTPException(status_code=404, detail="表情包不存在")
            return FileResponse(path)

        @self.app.put("/api/memes/{meme_id}")
        async def update_meme(meme_id: str, payload: EmojiUpdatePayload) -> dict[str, Any]:
            """更新表情包的描述与标签。"""

            ok = await self._get_service().update_meme(
                meme_id=meme_id,
                description=payload.description,
                tags=payload.tags,
            )
            if not ok:
                raise HTTPException(status_code=400, detail="更新失败")
            return {"status": "ok"}

        @self.app.delete("/api/memes/{meme_id}")
        async def delete_meme(meme_id: str) -> dict[str, Any]:
            """删除表情包。"""

            ok = await self._get_service().delete_meme(meme_id)
            if not ok:
                raise HTTPException(status_code=404, detail="表情包不存在或删除失败")
            return {"status": "ok"}

        @self.app.delete("/api/memes")
        async def batch_delete_meme(payload: EmojiBatchDeletePayload) -> dict[str, Any]:
            """批量删除表情包。"""

            result = await self._get_service().delete_memes(payload.meme_ids)
            if not result["deleted"] and not result["failed"]:
                raise HTTPException(status_code=400, detail="没有可删除的表情包")
            return {
                "status": "ok",
                **result,
            }


__all__ = ["EmojiAdminRouter"]
