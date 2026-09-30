# Refactor Candidates

After TDD cycle, look for:

- **Duplication** → Extract function/class
- **Long methods** → Break into private helpers (keep tests on public interface)
- **Shallow modules** → Combine or deepen
- **Feature envy** → Move logic to where data lives
- **Primitive obsession** → Introduce value objects
- **Existing code** the new code reveals as problematic
- **Leaky boundaries** → Move framework/vendor details behind application ports
- **Duplicated failure cleanup** → Centralize terminal-state cleanup with `finally` or an equivalent lifecycle construct
- **Tests weaker than claims** → Promote the affected path to an integration, process, or container test

Refactoring is complete only when the same behavioral evidence remains green. Do not preserve a poor public interface solely because the first test happened to encode it; revise the test and implementation together when the agreed contract is unchanged.
