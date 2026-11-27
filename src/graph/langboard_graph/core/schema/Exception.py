from fastapi import HTTPException
from pydantic import BaseModel


class GraphInputError(Exception):
    pass


class ExceptionBody(BaseModel):
    message: str | list[str]
    traceback: str | list[str] | None = None
    description: str | list[str] | None = None
    code: str | None = None
    suggestion: str | list[str] | None = None


class APIException(HTTPException):
    def __init__(self, exception: Exception, status_code: int = 500):
        body = self.build_exception_body(exception)
        super().__init__(status_code=status_code, detail=body.model_dump_json())

    @staticmethod
    def build_exception_body(exc: str | list[str] | Exception) -> ExceptionBody:
        body = {"message": str(exc)}
        return ExceptionBody(**body)
