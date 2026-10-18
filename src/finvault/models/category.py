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


class CategoryGroup(Category):
    """A configurable main category used to group existing category IDs."""


class CategoryRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: CategoryId
    keywords: tuple[Keyword, ...] = Field(min_length=1, max_length=100)
    transaction_type: Literal["debit", "credit"] | None = None


class CategorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    categories: tuple[Category, ...] = Field(max_length=500)
    rules: tuple[CategoryRule, ...] = Field(default=(), max_length=1000)
    groups: tuple[CategoryGroup, ...] = Field(default=(), max_length=500)
    category_groups: dict[CategoryId, CategoryId] = Field(default_factory=dict, max_length=500)
    income_group: CategoryId | None = None

    @property
    def resolved_income_group(self) -> str | None:
        """Use an explicit selection, or the existing main category named Income."""
        if self.income_group is not None:
            return self.income_group
        return next((group.id for group in self.groups if group.label.casefold() == "income"), None)

    @model_validator(mode="after")
    def validate_catalog(self) -> Self:
        ids = {category.id for category in self.categories}
        if len(ids) != len(self.categories):
            raise ValueError("Category IDs must be unique")
        if len({category.label.casefold() for category in self.categories}) != len(self.categories):
            raise ValueError("Category labels must be unique")
        if any(rule.category not in ids for rule in self.rules):
            raise ValueError("Every rule must reference a configured category")
        group_ids = {group.id for group in self.groups}
        if len(group_ids) != len(self.groups):
            raise ValueError("Main category IDs must be unique")
        if len({group.label.casefold() for group in self.groups}) != len(self.groups):
            raise ValueError("Main category labels must be unique")
        if self.income_group is not None and self.income_group not in group_ids:
            raise ValueError("Income group must reference a configured main category")
        if any(category not in ids or group not in group_ids
               for category, group in self.category_groups.items()):
            raise ValueError("Every group assignment must reference a configured category and main category")
        return self
