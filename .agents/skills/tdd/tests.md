# Good and Bad Tests

## Functional Evidence Over Line Coverage

Prefer an end-to-end test that proves a complete caller-visible workflow over a collection of unit tests that only exercise its implementation pieces. For example, creating an order and verifying that it is persisted and retrievable proves more than separately testing constructors, setters, and repository call counts.

Use focused tests when they prove meaningful edge cases, invariants, or failure behavior more precisely. Do not write tests whose only purpose is executing uncovered lines. Use coverage gaps to ask which relevant behavior is unproven, and add a test only when there is a meaningful answer.

## Good Tests

**Integration-style**: Test through real interfaces, not mocks of internal parts.

```python
def test_user_can_checkout_with_valid_cart(checkout_service, product) -> None:
    cart = Cart()
    cart.add(product)

    result = checkout_service.checkout(cart, payment_method="test-card")

    assert result.status == "confirmed"
    assert result.order_id is not None
```

Characteristics:

- Tests behavior users/callers care about
- Uses public API only
- Survives internal refactors
- Describes WHAT, not HOW
- One coherent behavior per test; use every assertion needed to prove it

## Bad Tests

**Implementation-detail tests**: Coupled to internal structure.

```python
def test_checkout_calls_payment_service(mocker, checkout_service, cart) -> None:
    process = mocker.patch.object(checkout_service._payment_service, "process")

    checkout_service.checkout(cart, payment_method="test-card")

    process.assert_called_once_with(cart.total)
```

Red flags:

- Mocking internal collaborators
- Testing private methods
- Asserting on call counts/order
- Test breaks when refactoring without behavior change
- Test name describes HOW not WHAT
- Asserting incidental calls instead of an observable result

```python
def test_create_user_saves_a_database_row(database, user_service) -> None:
    user_service.create_user(name="Alice")
    assert database.fetch_one("select name from users") == {"name": "Alice"}


def test_created_user_is_retrievable(user_service) -> None:
    created = user_service.create_user(name="Alice")
    retrieved = user_service.get_user(created.id)

    assert retrieved.name == "Alice"
```

The first test can be appropriate when the database schema itself is the public contract, such as a migration or interoperability requirement. Choose the verification interface from the claim; do not make "never inspect storage" an absolute rule.

## Match Evidence to the Claim

An ASGI test proves application behavior in process. It does not prove that a Docker image starts or that container DNS works. A mocked HTTP client proves error mapping, not that the real HTTP protocol is compatible. Add a process, container, or remote acceptance test when the claim crosses that boundary.

For a complex feature, give each meaningful failure class a caller-visible assertion. Typical assertions include:

- returned status and bounded error body
- no forbidden downstream call occurred
- reserved capacity returned to zero
- a later request still succeeds
- persisted state is visible through the public interface
- emitted metrics use bounded labels and reach their terminal value

Do not add tests whose only value is confirming that a dataclass field, constructor, or constant exists unless that shape is itself a compatibility contract.
