from io import BytesIO
from pathlib import Path
from typing import IO, BinaryIO, overload
from uuid import uuid4
from starlette.datastructures import UploadFile
from ..utils.decorators import class_instance
from .BaseStorage import BaseStorage
from .FileModel import FileModel
from .LocalStorage import LocalStorage
from .S3Storage import S3Storage
from .StorageName import StorageName


@class_instance()
class Storage:
    def __init__(self):
        self._storages: dict[str, BaseStorage] = {
            S3Storage.storage_type: S3Storage(),
            LocalStorage.storage_type: LocalStorage(),
        }

    def get(self, storage_type: str, storage_name: str, filename: str) -> bytes | None:
        storage_type = BaseStorage.decrypt_storage_type(storage_type)
        if not storage_type or storage_type not in self._storages:
            return None

        destination = BytesIO()
        if not self._download(storage_type, storage_name, filename, destination):
            return None
        return destination.getvalue()

    def get_file(self, file_model: FileModel) -> bytes | None:
        if file_model.storage_type not in self._storages:
            return None

        destination = BytesIO()
        if not self.download_file(file_model, destination):
            return None
        return destination.getvalue()

    def download(self, storage_type: str, storage_name: str, filename: str, destination: IO[bytes]) -> bool:
        storage_type = BaseStorage.decrypt_storage_type(storage_type)
        if not storage_type or storage_type not in self._storages:
            return False

        return self._download(storage_type, storage_name, filename, destination)

    def download_file(self, file_model: FileModel, destination: IO[bytes]) -> bool:
        if file_model.storage_type not in self._storages:
            return False

        return self._download(file_model.storage_type, file_model.storage_name, file_model.filename, destination)

    def _download(self, storage_type, storage_name, filename, destination):
        from ...domain.services.AttachmentStorageOperations import resolve

        managed, physical = resolve(storage_type, storage_name, filename)
        if managed:
            if physical is None:
                return False
            storage_name, filename = physical
        return self._storages[storage_type].download(storage_name, filename, destination)

    @overload
    def upload(self, file: UploadFile, storage_name: StorageName) -> FileModel | None: ...
    @overload
    def upload(self, file: BinaryIO, storage_name: StorageName) -> FileModel | None: ...
    def upload(self, file: UploadFile | BinaryIO, storage_name: StorageName) -> FileModel | None:
        if isinstance(file, UploadFile):
            filename = file.filename
            file = file.file
        else:
            filename = file.name

        if not filename:
            return None

        if storage_name.value in {"card_attachment", "project_attachment", "wiki"}:
            return self.upload_named(file, storage_name, "ref_" + uuid4().hex + Path(filename).suffix, filename)
        storage = self._select_storage()
        return storage.upload(file, filename, storage_name)

    def _select_storage(self):
        for storage_type, storage in self._storages.items():
            if storage_type != LocalStorage.storage_type and storage.is_connectable():
                return storage
        return self._storages[LocalStorage.storage_type]

    def upload_named(
        self,
        file: BinaryIO,
        storage_name: StorageName,
        stored_filename: str,
        original_filename: str,
    ) -> FileModel | None:
        """Upload using a deterministic key for restartable workflows."""

        if not stored_filename or not original_filename:
            return None
        storage = self._select_storage()
        if storage_name.value in {"card_attachment", "project_attachment", "wiki"}:
            from ...domain.services.AttachmentStorageOperations import upload

            return upload(storage, file, storage_name, stored_filename, original_filename)
        return storage.upload_named(file, original_filename, storage_name, stored_filename)

    def delete(self, file_model: FileModel) -> bool:
        if file_model.storage_type not in self._storages:
            return False

        from ...domain.services.AttachmentStorageOperations import release

        managed, released = release(file_model)
        if managed:
            return released
        storage = self._storages[file_model.storage_type]
        return storage.delete(file_model)
