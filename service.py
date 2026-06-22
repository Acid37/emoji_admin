"""Emoji 管理后台服务。"""

from __future__ import annotations

import asyncio
import tomllib
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.app.plugin_system.api.llm_api import (
    create_embedding_request,
    get_model_set_by_task,
)
from src.core.components.base.service import BaseService
from src.kernel.logger import get_logger
from src.kernel.vector_db import get_vector_db_service

logger = get_logger("emoji_admin")


@dataclass(frozen=True, slots=True)
class EmojiRecord:
    """表情包聚合后的展示记录。"""

    meme_id: str
    description: str
    path: str
    tags: list[str]
    created_at: float
    record_count: int
    file_exists: bool


class EmojiAdminService(BaseService):
    """表情包管理服务。"""

    service_name: str = "emoji_admin"
    service_description: str = "表情包预览、标签编辑、描述修改与删除服务"
    version: str = "1.0.0"

    collection_name: str = "emoji_sender"
    vector_db_path: str = "data/emoji_sender/vector_db"
    data_dir: str = "data/emoji_sender/memes"

    def __init__(self, plugin: Any) -> None:
        """初始化服务。"""

        super().__init__(plugin)

    def _vector_db(self):
        """获取向量数据库服务。"""

        return get_vector_db_service(self.vector_db_path)

    @staticmethod
    def _repo_root() -> Path:
        """获取仓库根目录。"""

        return Path(__file__).resolve().parents[2]

    @classmethod
    def _emoji_sender_config_path(cls) -> Path:
        """获取 emoji_sender 配置文件路径。"""

        return cls._repo_root() / "config" / "plugins" / "emoji_sender" / "config.toml"

    @classmethod
    def _load_sender_interval_seconds(cls) -> int | None:
        """从实际配置文件读取 emoji_sender 同步间隔。"""

        config_path = cls._emoji_sender_config_path()
        try:
            with config_path.open("rb") as file_handle:
                raw = tomllib.load(file_handle)
        except FileNotFoundError:
            logger.warning(f"未找到 emoji_sender 配置文件: {config_path}")
            return None
        except Exception as exc:
            logger.warning(f"读取 emoji_sender 配置失败: {config_path} - {exc}")
            return None

        scheduler = raw.get("scheduler") if isinstance(raw, dict) else None
        if not isinstance(scheduler, dict):
            return None

        raw_interval = scheduler.get("interval_seconds")
        try:
            interval = int(raw_interval)
        except (TypeError, ValueError):
            return None

        return interval if interval > 0 else None

    @staticmethod
    def _normalize_text(value: str | None) -> str:
        """归一化文本。"""

        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore").strip()
        return str(value).strip()

    @classmethod
    def _normalize_tags(cls, tags: list[str] | None) -> list[str]:
        """归一化标签列表。"""

        normalized: list[str] = []
        iterable = [] if tags is None else list(tags)
        for tag in iterable:
            cleaned = cls._normalize_text(tag)
            if cleaned and cleaned not in normalized:
                normalized.append(cleaned)
        return normalized

    async def _get_all_records(self) -> list[dict[str, Any]]:
        """从向量库拉取全部记录。"""

        vdb = self._vector_db()
        await vdb.get_or_create_collection(self.collection_name)

        rows: list[dict[str, Any]] = []
        offset = 0
        page_size = 256
        while True:
            data = await vdb.get(
                collection_name=self.collection_name,
                limit=page_size,
                offset=offset,
                include=["metadatas", "embeddings"],
            )
            ids_raw = data.get("ids")
            metadatas_raw = data.get("metadatas")
            embeddings_raw = data.get("embeddings")
            ids: list[str] = list(ids_raw) if ids_raw is not None else []
            metadatas: list[dict[str, Any]] = (
                list(metadatas_raw) if metadatas_raw is not None else []
            )
            embeddings: list[list[float]] = (
                list(embeddings_raw) if embeddings_raw is not None else []
            )
            if not ids:
                break

            for index, record_id in enumerate(ids):
                metadata = metadatas[index] if index < len(metadatas) else {}
                embedding = embeddings[index] if index < len(embeddings) else []
                rows.append(
                    {
                        "id": record_id,
                        "metadata": metadata,
                        "embedding": embedding,
                    }
                )

            offset += len(ids)
        return rows

    def _aggregate_records(self, rows: list[dict[str, Any]]) -> list[EmojiRecord]:
        """将 tag 级记录聚合为单条表情包记录。"""

        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            metadata = row.get("metadata")
            if not isinstance(metadata, dict):
                continue
            meme_id = self._normalize_text(metadata.get("meme_id"))
            if not meme_id:
                continue

            group = grouped.setdefault(
                meme_id,
                {
                    "description": self._normalize_text(metadata.get("description")),
                    "path": self._normalize_text(metadata.get("path")),
                    "tags": [],
                    "created_at": float(metadata.get("created_at") or 0.0),
                    "record_count": 0,
                },
            )
            group["record_count"] += 1
            group["description"] = group["description"] or self._normalize_text(
                metadata.get("description")
            )
            group["path"] = group["path"] or self._normalize_text(metadata.get("path"))
            group["created_at"] = max(
                group["created_at"], float(metadata.get("created_at") or 0.0)
            )

            tag = self._normalize_text(metadata.get("tag"))
            if tag and tag not in group["tags"]:
                group["tags"].append(tag)

        result: list[EmojiRecord] = []
        for meme_id, data in grouped.items():
            path_value = self._normalize_text(data.get("path"))
            result.append(
                EmojiRecord(
                    meme_id=meme_id,
                    description=self._normalize_text(data.get("description")),
                    path=path_value,
                    tags=list(data.get("tags") or []),
                    created_at=float(data.get("created_at") or 0.0),
                    record_count=int(data.get("record_count") or 0),
                    file_exists=Path(path_value).exists() if path_value else False,
                )
            )

        result.sort(key=lambda item: item.created_at, reverse=True)
        return result

    @staticmethod
    def _collect_tags(rows: list[dict[str, Any]]) -> list[str]:
        """从原始记录中收集去重标签。"""

        tags: list[str] = []
        for row in rows:
            metadata = row.get("metadata")
            if not isinstance(metadata, dict):
                continue
            tag = EmojiAdminService._normalize_text(metadata.get("tag"))
            if tag and tag not in tags:
                tags.append(tag)
        tags.sort()
        return tags

    @staticmethod
    def _record_to_dict(item: EmojiRecord) -> dict[str, Any]:
        """将展示记录序列化为 API 返回结构。"""

        return {
            "meme_id": item.meme_id,
            "description": item.description,
            "path": item.path,
            "tags": item.tags,
            "created_at": item.created_at,
            "record_count": item.record_count,
            "file_exists": item.file_exists,
        }

    async def _get_meme_vector_data(
        self, meme_id: str
    ) -> tuple[list[dict[str, Any]], list[list[float]]]:
        """读取单个 meme_id 对应的向量库数据。"""

        vdb = self._vector_db()
        await vdb.get_or_create_collection(self.collection_name)
        data = await vdb.get(
            collection_name=self.collection_name,
            where={"meme_id": self._normalize_text(meme_id)},
            include=["metadatas", "embeddings"],
        )
        metadatas_raw = data.get("metadatas")
        embeddings_raw = data.get("embeddings")
        metadatas: list[dict[str, Any]] = (
            list(metadatas_raw) if metadatas_raw is not None else []
        )
        embeddings: list[list[float]] = (
            list(embeddings_raw) if embeddings_raw is not None else []
        )
        return metadatas, embeddings

    def _build_detail_from_metadatas(
        self, metadatas: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """从元数据聚合出单条表情包详情。"""

        records = self._aggregate_records(
            [
                {"id": str(index), "metadata": metadata}
                for index, metadata in enumerate(metadatas)
            ]
        )
        if not records:
            return None

        return self._record_to_dict(records[0])

    @staticmethod
    def _matches_keyword(item: EmojiRecord, keyword: str) -> bool:
        """判断单条记录是否匹配关键词。"""

        haystack = " ".join(
            [item.meme_id, item.description, item.path, " ".join(item.tags)]
        ).lower()
        return keyword in haystack

    def _filter_records(
        self, records: list[EmojiRecord], keyword: str | None
    ) -> list[EmojiRecord]:
        """按关键词过滤聚合记录。"""

        query = self._normalize_text(keyword).lower()
        if not query:
            return records
        return [item for item in records if self._matches_keyword(item, query)]

    async def _build_dashboard_payload(
        self,
        memes: list[dict[str, Any]],
        available_tags: list[str],
        latest_sync_ts: float | None,
    ) -> dict[str, Any]:
        """组装列表页返回结构。"""

        return {
            "memes": memes,
            "available_tags": available_tags,
            "sync_info": await self.get_sync_info(latest_sync_ts=latest_sync_ts),
        }

    async def get_dashboard_payload(self, keyword: str | None = None) -> dict[str, Any]:
        """一次性返回表情包列表页所需的全部数据。"""

        try:
            rows = await self._get_all_records()
            records = self._aggregate_records(rows)
            latest_sync_ts = self._get_latest_sync_timestamp_from_rows(rows)
            records = self._filter_records(records, keyword)
            memes = [self._record_to_dict(item) for item in records]
            available_tags = self._collect_tags(rows)
        except Exception as exc:
            logger.error(f"读取 emoji_admin 仪表盘数据失败: {exc}")
            memes = []
            available_tags = []
            latest_sync_ts = None

        return await self._build_dashboard_payload(
            memes, available_tags, latest_sync_ts
        )

    async def list_all_memes(self, keyword: str | None = None) -> list[dict[str, Any]]:
        """列出所有表情包并可选按关键词过滤。"""

        payload = await self.get_dashboard_payload(keyword=keyword)
        return list(payload["memes"])

    async def list_all_tags(self) -> list[str]:
        """从当前向量库中聚合全部真实存在的标签。"""

        rows = await self._get_all_records()
        return self._collect_tags(rows)

    @staticmethod
    def _format_local_datetime(value: datetime) -> str:
        """格式化本地时间。"""

        return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")

    async def _get_latest_sync_timestamp(self) -> float | None:
        """从现有记录中提取最近一次同步时间戳。"""

        rows = await self._get_all_records()
        return self._get_latest_sync_timestamp_from_rows(rows)

    @staticmethod
    def _get_latest_sync_timestamp_from_rows(
        rows: list[dict[str, Any]],
    ) -> float | None:
        """从已拉取的记录中提取最近一次同步时间戳。"""

        latest_ts: float | None = None
        for row in rows:
            metadata = row.get("metadata")
            if not isinstance(metadata, dict):
                continue
            try:
                created_at = float(metadata.get("created_at") or 0.0)
            except (TypeError, ValueError):
                continue
            if created_at <= 0.0:
                continue
            if latest_ts is None or created_at > latest_ts:
                latest_ts = created_at
        return latest_ts

    @staticmethod
    def _build_next_refresh_at(
        interval_seconds: int | None,
        now_local: datetime,
        latest_sync_at: datetime | None,
    ) -> datetime | None:
        """根据同步间隔和最近同步时间计算下次刷新时间。"""

        if interval_seconds is None:
            return None
        if latest_sync_at is not None:
            return latest_sync_at + timedelta(seconds=interval_seconds)
        return now_local + timedelta(seconds=interval_seconds)

    def _build_refresh_hint(
        self,
        interval_seconds: int | None,
        latest_sync_at: datetime | None,
        next_refresh_at: datetime | None,
    ) -> str:
        """拼接同步提示文案。"""

        if interval_seconds is None or next_refresh_at is None:
            return ""

        next_refresh_text = self._format_local_datetime(next_refresh_at)
        if latest_sync_at is not None:
            latest_sync_text = self._format_local_datetime(latest_sync_at)
            return (
                f"最近一次同步约在 {latest_sync_text}，下一次预计在 {next_refresh_text}；"
                f"如果当前还没更新，请在 {next_refresh_text} 刷新。"
            )
        return f"当前未找到同步记录；如果刚启动且暂未同步，可在 {next_refresh_text} 刷新查看。"

    async def get_sync_info(
        self, latest_sync_ts: float | None = None
    ) -> dict[str, Any]:
        """获取 emoji_sender 当前部署的同步信息。"""

        interval_seconds = self._load_sender_interval_seconds()
        config_path = self._emoji_sender_config_path()
        now_local = datetime.now().astimezone()
        if latest_sync_ts is None:
            try:
                latest_sync_ts = await self._get_latest_sync_timestamp()
            except Exception as exc:
                logger.error(f"读取 emoji_admin 最近同步时间失败: {exc}")
                latest_sync_ts = None
        latest_sync_at = (
            datetime.fromtimestamp(latest_sync_ts).astimezone()
            if latest_sync_ts
            else None
        )

        next_refresh_at = self._build_next_refresh_at(
            interval_seconds, now_local, latest_sync_at
        )
        refresh_hint = self._build_refresh_hint(
            interval_seconds, latest_sync_at, next_refresh_at
        )

        return {
            "config_path": str(config_path),
            "interval_seconds": interval_seconds,
            "current_time": self._format_local_datetime(now_local),
            "latest_sync_at": self._format_local_datetime(latest_sync_at)
            if latest_sync_at
            else None,
            "next_refresh_at": self._format_local_datetime(next_refresh_at)
            if next_refresh_at
            else None,
            "refresh_hint": refresh_hint,
            "startup_immediate_ingest": True,
            "schedule_mode": "startup_once_plus_interval",
        }

    async def get_meme_detail(self, meme_id: str) -> dict[str, Any] | None:
        """获取单个表情包详情。"""

        meme_id = self._normalize_text(meme_id)
        if not meme_id:
            return None

        metadatas, _ = await self._get_meme_vector_data(meme_id)
        return self._build_detail_from_metadatas(metadatas)

    async def delete_meme(self, meme_id: str) -> bool:
        """删除一个表情包及其文件。"""

        detail = await self.get_meme_detail(meme_id)
        if not detail:
            return False

        vdb = self._vector_db()
        await vdb.delete(
            collection_name=self.collection_name,
            where={"meme_id": self._normalize_text(meme_id)},
        )

        await self._delete_file_if_exists(self._normalize_text(detail.get("path")))
        return True

    async def _delete_file_if_exists(self, path_value: str) -> None:
        """删除表情包图片文件，失败时仅记录日志。"""

        if not path_value:
            return

        try:
            await asyncio.to_thread(Path(path_value).unlink, True)
        except Exception as exc:
            logger.warning(f"删除表情包文件失败: {path_value} - {exc}")

    async def delete_memes(self, meme_ids: list[str]) -> dict[str, Any]:
        """批量删除多个表情包。"""

        normalized_ids = [self._normalize_text(meme_id) for meme_id in meme_ids]
        targets = [meme_id for meme_id in normalized_ids if meme_id]
        if not targets:
            return {"deleted": [], "failed": []}

        deleted: list[str] = []
        failed: list[str] = []
        for meme_id in targets:
            ok = await self.delete_meme(meme_id)
            if ok:
                deleted.append(meme_id)
            else:
                failed.append(meme_id)

        return {
            "deleted": deleted,
            "failed": failed,
        }

    async def update_meme(
        self, meme_id: str, description: str, tags: list[str]
    ) -> bool:
        """更新表情包描述与标签。"""

        meme_id = self._normalize_text(meme_id)
        description = self._normalize_text(description)
        normalized_tags = self._normalize_tags(tags)
        if not meme_id or not description or not normalized_tags:
            return False

        vdb = self._vector_db()
        metadatas, embeddings = await self._get_meme_vector_data(meme_id)
        if not metadatas:
            return False

        old_meta = metadatas[0]
        old_description = self._normalize_text(old_meta.get("description"))
        path_value = self._normalize_text(old_meta.get("path"))
        created_at = float(old_meta.get("created_at") or time.time())
        source_hash = self._normalize_text(old_meta.get("source_hash")) or meme_id
        source_cache_path = self._normalize_text(old_meta.get("source_cache_path"))

        if description != old_description or not embeddings:
            try:
                embedding_model_set = get_model_set_by_task("embedding")
                emb_req = create_embedding_request(
                    model_set=embedding_model_set,
                    request_name="emoji_admin_update_embedding",
                    inputs=[description],
                )
                emb_resp = await emb_req.send()
                embedding = list(emb_resp.embeddings[0])
            except Exception as exc:
                logger.warning(f"更新表情包 embedding 失败: {exc}")
                return False
        else:
            embedding = list(embeddings[0])

        await vdb.delete(
            collection_name=self.collection_name, where={"meme_id": meme_id}
        )

        ids: list[str] = []
        embedded: list[list[float]] = []
        documents: list[str] = []
        metadatas_to_add: list[dict[str, Any]] = []
        for tag in normalized_tags:
            ids.append(f"{meme_id}:{tag}")
            embedded.append(list(embedding))
            documents.append(description)
            metadatas_to_add.append(
                {
                    "meme_id": meme_id,
                    "tag": tag,
                    "path": path_value,
                    "description": description,
                    "source_hash": source_hash,
                    "source_cache_path": source_cache_path,
                    "created_at": created_at,
                }
            )

        await vdb.add(
            collection_name=self.collection_name,
            ids=ids,
            embeddings=embedded,
            documents=documents,
            metadatas=metadatas_to_add,
        )
        return True

    async def get_preview_path(self, meme_id: str) -> Path | None:
        """获取可预览的图片路径。"""

        detail = await self.get_meme_detail(meme_id)
        if not detail:
            return None

        path_value = self._normalize_text(detail.get("path"))
        if not path_value:
            return None
        path = Path(path_value)
        return path if path.exists() else None


__all__ = ["EmojiAdminService"]
