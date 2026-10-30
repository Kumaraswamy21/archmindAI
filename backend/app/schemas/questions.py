"""Question request and response models."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class QuestionValidationRequest(BaseModel):
    """A question to normalize and validate."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"question": "  Where is OrderService implemented?  "}]
        }
    )

    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question", mode="before")
    @classmethod
    def trim_question(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class QuestionValidationResponse(BaseModel):
    """The normalized result of a valid question."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"valid": True, "normalized_question": "Where is OrderService implemented?"}
            ]
        }
    )

    valid: bool
    normalized_question: str


class QuestionExampleResponse(BaseModel):
    """An example engineering question."""

    example_id: str
    question: str
