from pydantic import BaseModel, Field


class GraphInterrupt(BaseModel):
    id: str | None = None
    value: dict | str | int | float | bool | list | None = None


class GraphRunResult(BaseModel):
    message: str = ""
    interrupts: list[GraphInterrupt] = Field(default_factory=list)


class GraphRunOutput(BaseModel):
    messages: list[dict[str, str]] = Field(default_factory=list)


class RunResponse(BaseModel):
    outputs: list[GraphRunOutput] = Field(default_factory=list)
    session_id: str | None = None
    thread_id: str | None = None
    message: str = ""
    interrupts: list[GraphInterrupt] = Field(default_factory=list)

    @classmethod
    def from_graph_result(
        cls, result: GraphRunResult, session_id: str | None = None, thread_id: str | None = None
    ) -> "RunResponse":
        return cls(
            outputs=[GraphRunOutput(messages=[{"message": result.message}])],
            session_id=session_id,
            thread_id=thread_id,
            message=result.message,
            interrupts=result.interrupts,
        )

    @classmethod
    def from_message(cls, message: str, session_id: str | None = None, thread_id: str | None = None) -> "RunResponse":
        return cls.from_graph_result(GraphRunResult(message=message), session_id=session_id, thread_id=thread_id)
