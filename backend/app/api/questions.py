"""Question validation and example routes."""

from fastapi import APIRouter

from app.errors import ApplicationError, ErrorResponse
from app.schemas.questions import (
    QuestionExampleResponse,
    QuestionValidationRequest,
    QuestionValidationResponse,
)

router = APIRouter(prefix="/api/v1/questions", tags=["questions"])

EXAMPLE_QUESTIONS = {
    "order-service": "Where is OrderService implemented?",
    "service-dependencies": "Which services depend on the payment module?",
}


@router.post(
    "/validate",
    response_model=QuestionValidationResponse,
    summary="Validate a question",
    description="Trim surrounding whitespace and validate the question length.",
)
def validate_question(request: QuestionValidationRequest) -> QuestionValidationResponse:
    return QuestionValidationResponse(valid=True, normalized_question=request.question)


@router.get(
    "/examples/{example_id}",
    response_model=QuestionExampleResponse,
    summary="Get an example question",
    description="Return one of the available engineering question examples.",
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Question example not found.",
            "content": {
                "application/json": {
                    "example": {
                        "error": {
                            "code": "EXAMPLE_NOT_FOUND",
                            "message": "Question example not found.",
                        }
                    }
                }
            },
        }
    },
)
def get_question_example(example_id: str) -> QuestionExampleResponse:
    question = EXAMPLE_QUESTIONS.get(example_id)
    if question is None:
        raise ApplicationError(
            status_code=404,
            code="EXAMPLE_NOT_FOUND",
            message="Question example not found.",
        )
    return QuestionExampleResponse(example_id=example_id, question=question)
