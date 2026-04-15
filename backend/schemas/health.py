from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(default="ok", examples=["ok"])
    service: str = Field(default="agent-demo-api")
    version: str = Field(default="1.0.0")
