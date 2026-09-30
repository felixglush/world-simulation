---
name: tdd
description: Risk-calibrated test-driven development with red-green-refactor, failure matrices, integration tests, and end-to-end evidence. Use when the user asks for TDD, test-first development, integration tests, or failure-first verification.
---

# Test-Driven Development

## Philosophy

**Core principle**: Tests should verify behavior through public interfaces, not implementation details. Code can change entirely; tests should not.

**Good tests** are integration-style: they exercise real code paths through public APIs. They describe _what_ the system does, not _how_ it does it. A good test reads like a specification - "user can checkout with valid cart" tells you exactly what capability exists. These tests survive refactors because they don't care about internal structure.

**Bad tests** are coupled to implementation. They mock internal collaborators, test private methods, or assert incidental call order. The warning sign: a refactor breaks the test even though public behavior did not change.

See [tests.md](tests.md) for examples and [mocking.md](mocking.md) for mocking guidelines.

## Choose the TDD Shape from the Risk

There are two valid working shapes.

### Simple or exploratory behavior

Use a tracer bullet and repeat one coherent behavior at a time:

```text
RED -> GREEN: first behavior -> smallest complete implementation
RED -> GREEN: next behavior  -> next implementation change
```

This is useful when the interface or domain is still being discovered.

### Complex, concurrent, distributed, or failure-sensitive behavior

Before implementation, enumerate the observable success path and every meaningful failure class. Write the behavioral integration and end-to-end test matrix first, then run it and confirm that it is RED for the intended reasons. Implement vertical slices until the matrix is GREEN.

```text
CONTRACT: public outcomes and invariants
MATRIX:   success + meaningful failures
RED:      run the matrix and inspect the failure reasons
GREEN:    implement vertical slices against the matrix
REFACTOR: improve the design while the matrix stays green
```

This is not speculative horizontal slicing when the tests come from an approved observable contract. It prevents an early happy-path implementation from defining away timeout, cancellation, overload, cleanup, or partial-failure requirements.

Avoid bulk tests that merely assert imagined class shapes, private functions, or implementation structure. A failure matrix must describe caller-visible outcomes and system invariants.

## Workflow

### 1. Establish the Observable Contract

When exploring the codebase, use the project's domain glossary so that test names and interface vocabulary match the project's language, and respect ADRs in the area you're touching.

Before writing implementation code:

- [ ] Identify the public interface and caller-visible outcomes
- [ ] Use an existing specification or user decision when one is already available; do not ask for redundant approval
- [ ] Identify opportunities for [deep modules](deep-modules.md) (small interface, deep implementation)
- [ ] Design interfaces for [testability](interface-design.md)
- [ ] List behavior and invariants, not implementation steps
- [ ] Identify the cheapest test boundary that proves each claim

Ask for clarification only when an unresolved choice would materially change the contract.

**You cannot test every possible input.** Cover meaningful equivalence classes and risks, not arbitrary permutations.

### 2. Classify the Change

Use the simple loop for a local, well-understood behavior. Use a failure matrix when the change crosses a process, network, storage, concurrency, security, or lifecycle boundary.

For complex work, consider these failure classes where relevant:

- invalid or oversized input
- dependency unavailable, slow, malformed, or partially successful
- deadline expiry and client cancellation
- concurrency races, overload, and capacity exhaustion
- cleanup after success, error, timeout, and cancellation
- startup, readiness, shutdown, and recovery
- persistence or message-delivery boundaries
- authentication and authorization failures
- metrics, logs, and bounded-label invariants

Record why any class is not applicable.

### 3. Prove RED

For simple work, write one test for one coherent behavior:

```text
RED:   Write test for first behavior → test fails
GREEN: Write minimal code to pass → test passes
```

For complex work, write the complete meaningful behavioral matrix before implementation. Run it and inspect the failures. A collection error, broken fixture, or unrelated environment failure is not useful RED evidence unless that exact setup failure is the behavior under test.

### 4. Implement Vertical Slices

Implement the smallest coherent production slice that satisfies the agreed contract and architecture. It is fine for one implementation slice to make several already-written matrix cases green.

"Minimal" means no speculative product behavior. It does not mean bypassing an agreed service boundary, dependency direction, cleanup guarantee, or security control merely because a narrower implementation passes the current assertion.

Rules:

- Keep tests focused on observable behavior
- Prefer real internal collaborators
- Substitute dependencies at architectural ports
- Preserve the agreed dependency direction
- Run focused tests after each slice and the broader matrix at useful checkpoints
- Do not add behavior that is absent from the contract or risk model

### 5. Verify at the Right Layers

Use the fewest layers that prove the claims:

- **Focused tests** for pure calculations and difficult local state transitions
- **Integration tests** through the public application or HTTP interface as the default
- **Process or container end-to-end tests** for networking, packaging, configuration, startup, shutdown, and real dependency wiring
- **Remote acceptance tests** only when the claim depends on the real external system

Do not use an in-process test to claim that a container, network, deployment, or remote service works.

### 6. Refactor

After all tests pass, look for [refactor candidates](refactoring.md):

- [ ] Extract duplication
- [ ] Deepen modules (move complexity behind simple interfaces)
- [ ] Apply SOLID principles where natural
- [ ] Consider what new code reveals about existing code
- [ ] Run tests after each refactor step

Do not mix unrelated refactoring into a RED debugging step. Small design corrections needed to make the intended slice possible are acceptable; return to GREEN before broadening them.

## Checklist Per Cycle or Matrix Slice

```text
[ ] Test describes behavior, not implementation
[ ] Test uses public interface only
[ ] Test would survive internal refactor
[ ] Assertions prove one coherent outcome, even if several facts are required
[ ] Failure is for the intended reason
[ ] Test boundary is strong enough for the claim
[ ] Dependencies are real or substituted at an architectural port
[ ] Code is minimal for the agreed behavior and architecture
[ ] Cleanup and cancellation invariants are covered where applicable
[ ] No speculative features added
```
