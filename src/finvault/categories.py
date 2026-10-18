"""Persistent, private category settings; no built-in category catalog."""

import os
import tempfile
import tomllib
from pathlib import Path
from threading import RLock
from typing import Protocol

import tomli_w
from pydantic import ValidationError

from finvault.models.category import Category, CategoryGroup, CategorySettings


class CategoryConfigError(Exception):
    """An unreadable or invalid settings file; messages never include its data."""


class CategoryStore(Protocol):
    def load(self) -> CategorySettings: ...

    def save(self, settings: CategorySettings) -> None: ...


class TomlCategoryStore:
    def __init__(self, path: Path | None = None):
        configured = path if path is not None else Path(os.environ.get("FINVAULT_CATEGORIES_CONFIG", "categories.toml"))
        self.path = configured.expanduser().resolve()
        self._lock = RLock()

    def load(self) -> CategorySettings:
        with self._lock:
            try:
                with self.path.open("rb") as file:
                    data = tomllib.load(file)
            except FileNotFoundError:
                return CategorySettings(categories=())
            except (OSError, ValueError) as error:
                raise CategoryConfigError("Could not read category settings; check categories.toml") from error
            try:
                if set(data) - {"categories", "rules", "groups", "category_groups", "income_group"}:
                    raise ValueError("Unknown settings fields")
                catalog = data.get("categories", {})
                groups = data.get("groups", {})
                if not isinstance(catalog, dict) or not isinstance(groups, dict):
                    raise ValueError("Categories and main categories must be TOML tables")
                return CategorySettings(
                    categories=tuple(Category(id=key, label=label) for key, label in catalog.items()),
                    rules=data.get("rules", ()),
                    groups=tuple(CategoryGroup(id=key, label=label) for key, label in groups.items()),
                    category_groups=data.get("category_groups", {}),
                    income_group=data.get("income_group"),
                )
            except (ValidationError, ValueError, TypeError) as error:
                raise CategoryConfigError("Invalid category settings; check categories.toml") from error

    def save(self, settings: CategorySettings) -> None:
        data = {
            **({"income_group": settings.income_group} if settings.income_group is not None else {}),
            "categories": {category.id: category.label for category in settings.categories},
            "rules": [rule.model_dump(mode="json", exclude_none=True) for rule in settings.rules],
            "groups": {group.id: group.label for group in settings.groups},
            "category_groups": settings.category_groups,
        }
        temporary = None
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with tempfile.NamedTemporaryFile(
                    mode="wb", dir=self.path.parent, prefix=".finvault-categories-", suffix=".tmp", delete=False,
                ) as file:
                    temporary = Path(file.name)
                    tomli_w.dump(data, file)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, self.path)
            except (OSError, ValueError) as error:
                raise CategoryConfigError("Could not save category settings") from error
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
