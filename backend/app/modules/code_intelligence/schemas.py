"""Public Java parsing request and response models."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class JavaParseRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"file_path": "src/main/java/com/example/OrderService.java"}]
        },
    )

    file_path: str = Field(min_length=1)


class JavaMethod(BaseModel):
    name: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class JavaClass(BaseModel):
    name: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    methods: list[JavaMethod]


class JavaParseResponse(BaseModel):
    file_path: str
    language: Literal["java"]
    has_syntax_errors: bool
    classes: list[JavaClass]


class JavaSymbolParameter(BaseModel):
    name: str
    type: str


class JavaTypeSymbol(BaseModel):
    name: str
    kind: Literal["class", "interface"]
    qualified_name: str
    enclosing_type: str | None
    annotations: list[str]
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class JavaCallableSymbol(BaseModel):
    name: str
    kind: Literal["method", "constructor"]
    qualified_name: str
    enclosing_type: str
    signature: str
    parameters: list[JavaSymbolParameter]
    annotations: list[str]
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class JavaSymbolsResponse(BaseModel):
    file_path: str
    package: str | None
    imports: list[str]
    symbols: list[JavaTypeSymbol | JavaCallableSymbol]
