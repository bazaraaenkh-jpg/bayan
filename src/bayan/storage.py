"""SaaS Файл Хадгалах Модуль (AWS S3 & Local Storage).

  * Хөгжүүлэлтийн горимд (STORAGE_PROVIDER=local) файлуудыг локал хавтаст хадгална.
  * Продакшн горимд (STORAGE_PROVIDER=s3) файлуудыг AWS S3 эсвэл S3-compatible санд хадгална.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# Тохиргоонуудыг орчны хувьсагчаас авна
STORAGE_PROVIDER = os.environ.get("STORAGE_PROVIDER", "local").lower().strip()
LOCAL_STORAGE_DIR = Path(os.environ.get("STORAGE_LOCAL_DIR", "storage"))

AWS_ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-1")
AWS_BUCKET = os.environ.get("AWS_S3_BUCKET", "bayan-ai-statements")


class StorageError(Exception):
    pass


def _ensure_local_dir():
    if not LOCAL_STORAGE_DIR.exists():
        LOCAL_STORAGE_DIR.mkdir(parents=True, exist_ok=True)


class S3ClientWrapper:
    """boto3 санг зөвхөн S3 сонгогдсон үед дуудна."""
    _client = None

    @classmethod
    def get_client(cls):
        if cls._client is None:
            try:
                import boto3
            except ImportError as e:
                raise StorageError(
                    "boto3 суулгаагүй байна. S3 ашиглахын тулд 'pip install boto3' ажиллуулна уу."
                ) from e
            
            kwargs = {}
            if AWS_ACCESS_KEY and AWS_SECRET_KEY:
                kwargs["aws_access_key_id"] = AWS_ACCESS_KEY
                kwargs["aws_secret_access_key"] = AWS_SECRET_KEY
            if AWS_REGION:
                kwargs["region_name"] = AWS_REGION
                
            cls._client = boto3.client("s3", **kwargs)
        return cls._client


def save_file(company_id: str, key: str, source_path: Path) -> str:
    """Файлыг хадгалах сан руу хуулна.

    company_id: tenant ID
    key: файлын нэр эсвэл зам (жишээ нь: 'statements/file.pdf')
    source_path: локал түр файлын зам

    Буцаах утга: Хадгалагдсан файлын зам эсвэл URL.
    """
    source_path = Path(source_path)
    if not source_path.exists():
        raise StorageError(f"Хадгалах эх файл олдсонгүй: {source_path}")

    storage_key = f"{company_id}/{key.lstrip('/')}"

    if STORAGE_PROVIDER == "s3":
        client = S3ClientWrapper.get_client()
        try:
            client.upload_file(str(source_path), AWS_BUCKET, storage_key)
            return f"s3://{AWS_BUCKET}/{storage_key}"
        except Exception as e:
            raise StorageError(f"S3 руу файл оруулахад алдаа гарлаа: {e}") from e
    else:
        # Локал хадгалалт
        _ensure_local_dir()
        dest_path = LOCAL_STORAGE_DIR / storage_key
        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, dest_path)
            return str(dest_path.resolve())
        except Exception as e:
            raise StorageError(f"Локал санд файл хуулахад алдаа гарлаа: {e}") from e


def get_file(company_id: str, key: str, dest_path: Path) -> None:
    """Хадгалагдсан файлыг татаж локал замд бичнэ."""
    storage_key = f"{company_id}/{key.lstrip('/')}"
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    if STORAGE_PROVIDER == "s3":
        client = S3ClientWrapper.get_client()
        try:
            client.download_file(AWS_BUCKET, storage_key, str(dest_path))
        except Exception as e:
            raise StorageError(f"S3-аас файл татахад алдаа гарлаа: {e}") from e
    else:
        src_path = LOCAL_STORAGE_DIR / storage_key
        if not src_path.exists():
            raise StorageError(f"Хадгалагдсан файл олдсонгүй: {src_path}")
        try:
            shutil.copy2(src_path, dest_path)
        except Exception as e:
            raise StorageError(f"Локал сангаас файл уншихад алдаа гарлаа: {e}") from e


def delete_file(company_id: str, key: str) -> None:
    """Хадгалагдсан файлыг устгана."""
    storage_key = f"{company_id}/{key.lstrip('/')}"

    if STORAGE_PROVIDER == "s3":
        client = S3ClientWrapper.get_client()
        try:
            client.delete_object(Bucket=AWS_BUCKET, Key=storage_key)
        except Exception as e:
            raise StorageError(f"S3-аас файл устгахад алдаа гарлаа: {e}") from e
    else:
        src_path = LOCAL_STORAGE_DIR / storage_key
        if src_path.exists():
            try:
                src_path.unlink()
            except Exception as e:
                raise StorageError(f"Локал сангаас файл устгахад алдаа гарлаа: {e}") from e
