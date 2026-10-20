from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Id = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
Color = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]
Revision = Annotated[int, Field(ge=0, le=9007199254740991, strict=True)]
Coordinate = Annotated[float, Field(ge=-1000, le=1000, allow_inf_nan=False)]
Extent = Annotated[float, Field(ge=0.1, le=100, allow_inf_nan=False)]
Shape = Literal["box", "sphere", "cylinder"]
Ids = Annotated[list[Id], Field(max_length=64)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class Position(Model):
    x: Coordinate
    y: Coordinate
    z: Coordinate


class Dimensions(Model):
    x: Extent
    y: Extent
    z: Extent


class SceneObject(Model):
    id: Id
    name: Annotated[str, Field(min_length=1, max_length=80)]
    shape: Shape
    position: Position
    dimensions: Dimensions
    color: Color
    tags: Annotated[list[Annotated[str, Field(min_length=1, max_length=32)]], Field(max_length=8)]

    @model_validator(mode="after")
    def validate_geometry(self) -> Self:
        dimensions = self.dimensions
        if self.shape == "sphere" and not dimensions.x == dimensions.y == dimensions.z:
            raise ValueError("Sphere extents must be equal.")
        if self.shape == "cylinder" and dimensions.x != dimensions.z:
            raise ValueError("Cylinder x and z extents must be equal.")
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("Tags must be unique.")
        return self


class SceneSnapshot(Model):
    schemaVersion: Literal[1] = 1
    revision: Revision = 0
    objects: Annotated[list[SceneObject], Field(min_length=1, max_length=64)]
    selectedIds: Ids = Field(default_factory=list)
    highlightedIds: Ids = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_ids(self) -> Self:
        ids = {obj.id for obj in self.objects}
        if len(ids) != len(self.objects):
            raise ValueError("Object ids must be unique.")
        for selected in (self.selectedIds, self.highlightedIds):
            if len(set(selected)) != len(selected) or not set(selected) <= ids:
                raise ValueError("Selection and highlight ids must be unique existing ids.")
        return self


class Edit(Model):
    color: Color | None = None
    position: Position | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        if self.color is None and self.position is None:
            raise ValueError("An edit requires a non-null color or position.")
        return self


class EditRequest(Model):
    ids: Annotated[list[Id], Field(min_length=1, max_length=16)]
    expectedRevision: Revision
    edit: Edit

    @model_validator(mode="after")
    def validate_unique(self) -> Self:
        if len(set(self.ids)) != len(self.ids):
            raise ValueError("Edit ids must be unique.")
        return self


class OperationReceipt(Model):
    operationId: Id
    affectedIds: Annotated[list[Id], Field(max_length=16)]
    oldRevision: Revision
    newRevision: Revision
    outcome: Literal["committed", "rejected", "cancelled", "stale"]
