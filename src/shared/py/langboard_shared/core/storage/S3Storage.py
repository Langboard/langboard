from io import BytesIO
from typing import IO, BinaryIO
from ...Env import Env
from .BaseStorage import BaseStorage
from .FileModel import FileModel
from .StorageName import StorageName


class S3Storage(BaseStorage):
    storage_type = "s3"

    def get(self, storage_name: str, filename: str) -> bytes | None:
        destination = BytesIO()
        if not self.download(storage_name, filename, destination):
            return None
        return destination.getvalue()

    def download(self, storage_name: str, filename: str, destination: IO[bytes]) -> bool:
        s3_client = None
        try:
            s3_client = self._connect_client()
            try:
                s3_client.download_fileobj(
                    Bucket=Env.S3_BUCKET_NAME, Key=f"{storage_name}/{filename}", Fileobj=destination
                )
            except Exception as error:
                from botocore.exceptions import ClientError

                if not isinstance(error, ClientError) or error.response.get("Error", {}).get("Code") not in {
                    "404",
                    "NoSuchKey",
                    "NotFound",
                }:
                    return False
                legacy_key = self._legacy_key(storage_name, filename)
                if legacy_key is None:
                    return False
                s3_client.download_fileobj(Bucket=Env.S3_BUCKET_NAME, Key=legacy_key, Fileobj=destination)
            return True
        except Exception:
            return False
        finally:
            if s3_client:
                s3_client.close()

    def upload(self, file: BinaryIO, filename: str, storage_name: StorageName) -> FileModel | None:
        if not filename:
            return None

        return self.upload_named(file, filename, storage_name, self.get_random_filename(filename))

    def upload_named(
        self,
        file: BinaryIO,
        filename: str,
        storage_name: StorageName,
        stored_filename: str,
    ) -> FileModel | None:
        if not filename or not stored_filename or "/" in stored_filename:
            return None

        s3_client = None
        try:
            s3_client = self._connect_client()
            s3_client.upload_fileobj(
                Fileobj=file,
                Bucket=Env.S3_BUCKET_NAME,
                Key=f"{storage_name.value}/{stored_filename}",
            )

            return FileModel(
                storage_type=S3Storage.storage_type,
                storage_name=storage_name.value,
                original_filename=filename,
                filename=stored_filename,
                path=f"/file/{self._encrypt_storage_type(S3Storage.storage_type)}/{storage_name.value}/{stored_filename}",
            )
        except Exception:
            return None
        finally:
            if s3_client:
                s3_client.close()

    def upload_immutable(
        self,
        file: BinaryIO,
        filename: str,
        storage_name: StorageName,
        stored_filename: str,
    ) -> FileModel | None:
        if not filename or not stored_filename or "/" in stored_filename:
            return None

        s3_client = None
        upload_id = None
        conditional_operation = False
        location = {"Bucket": Env.S3_BUCKET_NAME, "Key": f"{storage_name.value}/{stored_filename}"}
        try:
            from botocore.exceptions import ClientError

            s3_client = self._connect_client()
            # Reject SDKs without native conditional creation before allocating an upload.
            for operation in ("PutObject", "CompleteMultipartUpload"):
                shape = s3_client.meta.service_model.operation_model(operation).input_shape
                if "IfNoneMatch" not in shape.members:
                    return None

            part_size = 8 * 1024 * 1024

            def read_part() -> bytes:
                # Streams may return short reads; only EOF terminates a part.
                result = bytearray()
                while len(result) < part_size:
                    chunk = file.read(part_size - len(result))
                    if not chunk:
                        break
                    result.extend(chunk)
                return bytes(result)

            first, second = read_part(), read_part()
            try:
                if not second:
                    conditional_operation = True
                    s3_client.put_object(**location, Body=first, IfNoneMatch="*")
                else:
                    upload_id = s3_client.create_multipart_upload(**location)["UploadId"]
                    parts = []
                    body = first
                    while body:
                        number = len(parts) + 1
                        if number > 10000:
                            raise ValueError("Immutable upload exceeds multipart part limit")
                        response = s3_client.upload_part(
                            **location,
                            UploadId=upload_id,
                            PartNumber=number,
                            Body=body,
                        )
                        parts.append({"PartNumber": number, "ETag": response["ETag"]})
                        body = second if number == 1 else read_part()
                    conditional_operation = True
                    s3_client.complete_multipart_upload(
                        **location,
                        UploadId=upload_id,
                        MultipartUpload={"Parts": parts},
                        IfNoneMatch="*",
                    )
                    upload_id = None
            except ClientError as error:
                code = error.response.get("Error", {}).get("Code")
                if not conditional_operation or code not in {"412", "PreconditionFailed"}:
                    raise
                # A precondition failure identifies existing bytes, not their integrity.

            return FileModel(
                storage_type=S3Storage.storage_type,
                storage_name=storage_name.value,
                original_filename=filename,
                filename=stored_filename,
                path=f"/file/{self._encrypt_storage_type(S3Storage.storage_type)}/{storage_name.value}/{stored_filename}",
            )
        except Exception:
            return None
        finally:
            if s3_client:
                if upload_id is not None:
                    try:
                        s3_client.abort_multipart_upload(**location, UploadId=upload_id)
                    except Exception:
                        pass
                s3_client.close()

    def delete(self, file_model: FileModel) -> bool:
        if file_model.storage_type != S3Storage.storage_type:
            return False

        s3_client = None
        try:
            s3_client = self._connect_client()
            s3_client.delete_object(Bucket=Env.S3_BUCKET_NAME, Key=f"{file_model.storage_name}/{file_model.filename}")
            legacy_key = self._legacy_key(file_model.storage_name, file_model.filename)
            if legacy_key is not None:
                s3_client.delete_object(Bucket=Env.S3_BUCKET_NAME, Key=legacy_key)
            return True
        except Exception:
            return False
        finally:
            if s3_client:
                s3_client.close()

    def is_connectable(self) -> bool:
        if not Env.S3_ACCESS_KEY_ID or not Env.S3_SECRET_ACCESS_KEY:
            return False

        s3_client = None
        try:
            s3_client = self._connect_client()
            return True
        except Exception:
            return False
        finally:
            if s3_client:
                s3_client.close()

    @staticmethod
    def _legacy_key(storage_name: str, filename: str) -> str | None:
        try:
            return f"{StorageName(storage_name)}/{filename}"
        except ValueError:
            return None

    def _connect_client(self):
        from boto3 import client

        return client(
            "s3",
            region_name=Env.S3_REGION_NAME,
            aws_access_key_id=Env.S3_ACCESS_KEY_ID,
            aws_secret_access_key=Env.S3_SECRET_ACCESS_KEY,
        )
