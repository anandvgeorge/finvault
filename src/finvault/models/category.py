"""Category definitions and rules shared by configuration and the API."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

CategoryId = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
Label = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=80)]
Keyword = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)]


class Category(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: CategoryId
    label: Label


class CategoryRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: CategoryId
    keywords: tuple[Keyword, ...] = Field(min_length=1, max_length=100)
    transaction_type: Literal["debit", "credit"] | None = None


class CategorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    categories: tuple[Category, ...] = Field(max_length=500)
    rules: tuple[CategoryRule, ...] = Field(default=(), max_length=1000)

    @model_validator(mode="after")
    def validate_catalog(self) -> Self:
        ids = {category.id for category in self.categories}
        if len(ids) != len(self.categories):
            raise ValueError("Category IDs must be unique")
        if len({category.label.casefold() for category in self.categories}) != len(self.categories):
            raise ValueError("Category labels must be unique")
        if any(rule.category not in ids for rule in self.rules):
            raise ValueError("Every rule must reference a configured category")
        return self
