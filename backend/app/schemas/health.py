from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    service: str


class DatabaseHealthResponse(BaseModel):
    status: Literal["ready", "unavailable"]
    connection: Literal["ok", "unavailable"]
    schema_status: Literal["complete", "unavailable"] = Field(alias="schema")
