from __future__ import annotations

import re
import json
import io
import sys
from collections import Counter
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Iterable, Sequence, Iterator, Callable, Mapping, Any

from pathlib import PurePath, Path
import namesgenerator


@dataclass
class RenameMap:
    gen_name: Callable[[], str] = namesgenerator.get_random_name
    _map: dict[str, str] = field(default_factory=dict)

    def assign_name(self, key: str) -> str:
        if key not in self._map:
            self._map[key] = self.gen_name()
        return self._map[key]

    def assign_many(self, keys: Iterable[str]) -> Sequence[str]:
        return tuple(map(self.assign_name, keys))

    @property
    def mapping(self) -> Mapping[str, str]:
        return MappingProxyType(self._map)


def relative_path_to_components(relpath: PurePath) -> Iterator[str]:
    """st"""
    assert not relpath.is_absolute()
    parents = relpath.parents
    if not parents:
        raise ValueError(f"Path with no parents {relpath}")
    *named_parents, _ = parents
    # PurePath.parents gives us these in reverse order, but we want them in path order
    for parent_path in reversed(named_parents):
        yield parent_path.name
    yield relpath.name


def last_path_part(s: str) -> str:
    return s.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class Address:
    path: str
    name: str
    subtarget: str

    capture_pattern = re.compile(
        r"""
        (?://)?
        (?P<path>[^:#]+)
        (?::(?P<name>[^:#]+))?
        (?:\#(?P<subtarget>[^:#]+))?
        """,
        re.VERBOSE,
    )

    @classmethod
    def from_str(cls, s: str) -> Address:
        m = cls.capture_pattern.match(s)
        if m is None:
            raise ValueError(f"cannot parse address from {s!r}")

        path = m["path"]
        # drop name if it equals the last bit
        name = m["name"]
        name = "" if not name or name == last_path_part(path) else name
        subtarget = m["subtarget"] or ""

        return cls(path, name, subtarget)

    def __str__(self) -> str:
        path = self.path or "//"
        name = self.name or last_path_part(self.path)
        subtarget = f"#{self.subtarget}" if self.subtarget else ""
        return f"{path}:{name}{subtarget}"


@dataclass(frozen=True)
class TargetInfo:
    address: Address
    target_type: str
    dependencies: Sequence[str]
    dependencies_raw: Sequence[str]
    other: Mapping[str, Any]

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> TargetInfo:
        d = dict(d)
        address = Address.from_str(d.pop("address"))
        target_type = d.pop("target_type")
        dependencies = d.pop("dependencies") or ()
        dependencies_raw = d.pop("dependencies_raw", None) or ()
        return cls(address, target_type, dependencies, dependencies_raw, d)


def load_peek_data(file: io.TextIOBase = sys.stdin) -> Sequence[TargetInfo]:
    peek_json = json.load(file)
    return [TargetInfo.from_dict(d) for d in peek_json]


def rename_python_module(path: str, rename_map: RenameMap) -> str:
    path_parts = path.removesuffix(".py").split("/")
    if path_parts[-1] in ("__init__", "__main__"):
        renamed_parts = [*rename_map.assign_many(path_parts[:-1]), path_parts[-1]]
    else:
        renamed_parts = rename_map.assign_many(path_parts)
    return "/".join(renamed_parts) + ".py"


def render_imports(deps: Iterable[str]) -> Iterable[str]:
    seen_imports = Counter()
    for dep in deps:
        parts = dep.removesuffix(".py").split("/")
        if parts:
            *froms, import_ = parts
            if import_ == "__init__":
                continue
            from_ = ".".join(froms)
            seen_imports.update([import_])
            alias = f"{import_}{seen_imports[import_]}"
            if from_:
                yield f"from {from_} import {import_} as {alias}"
            else:
                yield f"import {import_} as {alias}"
        else:
            raise ValueError("zero-length dependency address")


@dataclass(frozen=True)
class TargetWithContent(TargetInfo):
    content: str


def handle_python_module(info: TargetInfo, target_type: str, rename_map: RenameMap) -> TargetWithContent:
    new_path = rename_python_module(info.address.path, rename_map)
    new_address = replace(info.address, path=new_path)

    inferred_deps = set(info.dependencies).difference(info.dependencies_raw)
    renamed_deps = [rename_python_module(x, rename_map) for x in inferred_deps]
    content = "\n".join([*render_imports(renamed_deps), ""])

    return TargetWithContent(
        address=new_address,
        target_type=info.target_type,
        dependencies=renamed_deps,
        dependencies_raw=(),
        other=info.other,
        content=content,
    )


type_to_handler = {
    "python_source": handle_python_module,
    "python_test": handle_python_module,
}


def rename_python_targets(peek_data: Sequence[TargetInfo]):
    relevant_types = type_to_handler.keys()
    rename_map = RenameMap()
    return [
        type_to_handler[ti.target_type](ti, ti.target_type, rename_map)
        for ti in peek_data
        if ti.target_type in relevant_types
    ]


def write_python_targets(renamed: Sequence[TargetWithContent]) -> None:
    for twc in renamed:
        path = Path(twc.address.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(twc.content)


def main() -> int | None:
    peek_data = load_peek_data()
    renamed = rename_python_targets(peek_data)
    write_python_targets(renamed)


if __name__ == "__main__":
    sys.exit(main())
