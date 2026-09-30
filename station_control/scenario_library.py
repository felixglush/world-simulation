"""Safe YAML adapter for typed scenario definitions."""

from pathlib import Path

import yaml
from yaml.composer import ComposerError
from yaml.events import AliasEvent
from yaml.nodes import MappingNode

from .scenarios import ScenarioDefinition, scenario_definition_from_dict

MAX_SCENARIO_FILE_BYTES = 64 * 1024
_LIBRARY_DIRECTORY = Path(__file__).resolve().parent.parent / "scenarios"


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects aliases and duplicate mapping keys."""

    def compose_node(self, parent, index):
        if self.check_event(AliasEvent):
            alias = self.peek_event()
            raise ComposerError(
                None,
                None,
                "YAML aliases are not supported",
                alias.start_mark,
            )
        return super().compose_node(parent, index)


def _construct_unique_mapping(loader: _UniqueKeySafeLoader, node: MappingNode, deep: bool = False):
    loader.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            hash(key)
        except TypeError as error:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from error
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_scenario(path: str | Path) -> ScenarioDefinition:
    """Read one bounded YAML file and validate it through the scenario contract."""
    try:
        with Path(path).open("rb") as scenario_file:
            content = scenario_file.read(MAX_SCENARIO_FILE_BYTES + 1)
    except (OSError, TypeError, ValueError) as error:
        raise ValueError(f"could not read scenario file: {error}") from error
    if len(content) > MAX_SCENARIO_FILE_BYTES:
        raise ValueError(f"scenario files are limited to {MAX_SCENARIO_FILE_BYTES} bytes")
    try:
        loader = _UniqueKeySafeLoader(content.decode("utf-8"))
        try:
            data = loader.get_single_data()
        finally:
            loader.dispose()
    except (yaml.YAMLError, UnicodeDecodeError, RecursionError) as error:
        raise ValueError(f"invalid scenario YAML: {error}") from error
    return scenario_definition_from_dict(data)


def load_library_scenario(scenario_id: str) -> ScenarioDefinition:
    """Load a named scenario from the repository's bundled library."""
    for definition in list_scenarios():
        if definition.id == scenario_id:
            return definition
    raise KeyError(f"unknown scenario: {scenario_id}")


def list_scenarios() -> tuple[ScenarioDefinition, ...]:
    """Return all bundled scenarios sorted by file name and reject duplicate IDs."""
    if not _LIBRARY_DIRECTORY.is_dir():
        return ()
    definitions = tuple(load_scenario(path) for path in sorted(_LIBRARY_DIRECTORY.glob("*.yaml")))
    ids = [definition.id for definition in definitions]
    if len(ids) != len(set(ids)):
        duplicates = sorted(scenario_id for scenario_id in set(ids) if ids.count(scenario_id) > 1)
        raise ValueError(f"scenario library contains duplicate id(s): {', '.join(duplicates)}")
    return definitions
