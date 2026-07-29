import os
import re
from pathlib import Path, PurePosixPath
from uuid import uuid4

import aiofiles
from fastapi import UploadFile

from app.config.setting import settings
from app.core.base_schema import DownloadFileSchema, PrivateUploadResponseSchema, UploadResponseSchema
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.utils.upload_util import UploadUtil


class FileService:
    """
    文件管理服务层
    """

    @classmethod
    async def upload_service(
        cls,
        base_url: str,
        file: UploadFile,
        upload_type: str = "file",
        target_path: str | None = None,
    ) -> UploadResponseSchema:
        """上传文件"""
        
        filename, filepath, file_url = await UploadUtil.upload_file(
            file=file,
            base_url=base_url,
            upload_type=upload_type,
            target_path=target_path,
        )

        return UploadResponseSchema(
            file_path=f"{filepath}",
            file_name=filename,
            origin_name=file.filename,
            file_url=f"{file_url}",
        )

    @staticmethod
    def _private_root(storage_root: Path | None = None) -> Path:
        return (storage_root or settings.PRIVATE_FILE_PATH).resolve()

    @staticmethod
    def _private_storage_key(*, tenant_id: int, namespace: str, file_name: str) -> str:
        if tenant_id <= 0:
            raise CustomException(msg="缺少有效租户信息", code=10403, status_code=403)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", namespace):
            raise CustomException(msg="非法的文件命名空间", status_code=400)
        return f"tenant/{tenant_id}/{namespace}/{uuid4().hex}_{file_name}"

    @classmethod
    def _resolve_private_path(
        cls,
        *,
        storage_key: str,
        tenant_id: int,
        storage_root: Path | None = None,
        action: str = "访问",
    ) -> Path:
        expected_prefix = f"tenant/{tenant_id}/"
        if not storage_key.startswith(expected_prefix):
            raise CustomException(msg=f"无权{action}该文件", code=10403, status_code=403)
        if "\\" in storage_key or "\0" in storage_key:
            raise CustomException(msg="非法的文件存储键", status_code=400)
        key_parts = PurePosixPath(storage_key).parts
        if len(key_parts) < 4 or key_parts[:2] != ("tenant", str(tenant_id)) or any(part in {".", ".."} for part in key_parts):
            raise CustomException(msg="非法的文件存储键", status_code=400)

        root = cls._private_root(storage_root)
        path = root.joinpath(storage_key).resolve()
        if not path.is_relative_to(root):
            raise CustomException(msg="非法的文件存储键", status_code=400)
        return path

    @classmethod
    async def save_private_service(
        cls,
        *,
        file: UploadFile,
        tenant_id: int,
        namespace: str = "file",
        storage_root: Path | None = None,
    ) -> PrivateUploadResponseSchema:
        """保存租户私有文件，不暴露物理路径或静态 URL。"""
        if not file or not file.filename:
            raise CustomException(msg="请选择要上传的文件")
        if not UploadUtil.check_path_traversal(file.filename):
            raise CustomException(msg="文件名包含非法字符", data=file.filename)

        extension = UploadUtil.get_extension_from_filename(file.filename)
        if not extension:
            raise CustomException(msg="无法识别文件类型")
        UploadUtil.validate_file_extension(extension)
        UploadUtil.check_file_size(file)
        content = await file.read()
        await file.seek(0)
        UploadUtil.validate_file_content_type(content, extension)

        safe_name = UploadUtil.generate_safe_filename(file.filename, extension)
        storage_key = cls._private_storage_key(
            tenant_id=tenant_id,
            namespace=namespace,
            file_name=safe_name,
        )
        path = cls._resolve_private_path(
            storage_key=storage_key,
            tenant_id=tenant_id,
            storage_root=storage_root,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "wb") as target:
            await target.write(content)

        return PrivateUploadResponseSchema(
            storage_key=storage_key,
            file_name=safe_name,
            origin_name=file.filename,
            size_bytes=len(content),
        )

    @classmethod
    def resolve_private_service(
        cls,
        *,
        storage_key: str,
        tenant_id: int,
        storage_root: Path | None = None,
    ) -> DownloadFileSchema:
        """在当前租户边界内解析私有文件。"""
        path = cls._resolve_private_path(
            storage_key=storage_key,
            tenant_id=tenant_id,
            storage_root=storage_root,
        )
        if not path.is_file():
            raise CustomException(msg="文件不存在", status_code=404)
        return DownloadFileSchema(file_path=str(path), file_name=path.name)

    @classmethod
    def delete_private_service(
        cls,
        *,
        storage_key: str,
        tenant_id: int,
        storage_root: Path | None = None,
    ) -> int:
        """删除当前租户私有文件，返回释放的字节数。"""
        path = cls._resolve_private_path(
            storage_key=storage_key,
            tenant_id=tenant_id,
            storage_root=storage_root,
            action="删除",
        )
        if not path.is_file():
            raise CustomException(msg="文件不存在", status_code=404)
        size_bytes = path.stat().st_size
        path.unlink()
        return size_bytes

    @classmethod
    async def download_service(cls, file_path: str) -> DownloadFileSchema:
        """下载文件"""

        if not file_path:
            raise CustomException(msg="请选择要下载的文件")

        dangerous_patterns = ["../", "..\\", "\0"]
        for pattern in dangerous_patterns:
            if pattern in file_path:
                logger.error(f"检测到路径穿越攻击: {file_path}")
                raise CustomException(msg="非法的文件路径")

        upload_root = settings.UPLOAD_FILE_PATH.resolve()
        abs_path = os.path.normpath(os.path.abspath(file_path))

        if not abs_path.startswith(str(upload_root)):
            logger.error(f"路径不在上传目录内: {file_path}")
            raise CustomException(msg="非法的文件路径")

        if not UploadUtil.check_file_exists(abs_path):
            raise CustomException(msg="文件不存在")

        file_name = UploadUtil.download_file(abs_path)

        return DownloadFileSchema(
            file_path=abs_path,
            file_name=str(file_name),
        )
