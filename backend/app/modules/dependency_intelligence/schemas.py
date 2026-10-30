"""Public dependency relationship contracts."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


RelationshipType = Literal["IMPORTS", "EXTENDS", "IMPLEMENTS", "INJECTS", "CALLS"]
ResolutionStatus = Literal["RESOLVED", "UNRESOLVED"]
Direction = Literal["outgoing", "incoming", "both"]


class DependencyRelationship(BaseModel):
    relationship_id: str
    repository_id: UUID
    source_symbol: str
    target_symbol: str
    relationship_type: RelationshipType
    file_path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    resolution_status: ResolutionStatus


class DependencyScanResponse(BaseModel):
    repository_id: UUID
    status: Literal["COMPLETED"] = "COMPLETED"
    total_relationships: int


class DependencyListResponse(BaseModel):
    repository_id: UUID
    total_relationships: int
    relationships: list[DependencyRelationship]
