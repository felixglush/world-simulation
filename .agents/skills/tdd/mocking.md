# Test Doubles at Architectural Boundaries

Prefer real collaborators inside the unit or service boundary. Substitute dependencies at explicit ports when the real dependency would make the test unsafe, slow, nondeterministic, or unable to exercise a failure precisely.

Good substitution boundaries include:

- External APIs (payment, email, model servers)
- Databases (sometimes; prefer a disposable real database when persistence behavior matters)
- Time/randomness
- File systems and object stores (sometimes)
- Message brokers, cloud control planes, and expensive remote services

Avoid mocking:

- private methods or concrete internal call chains
- framework internals
- every class merely because it is easy to patch
- call counts or order that are not part of the contract

"Do not mock your own modules" is not an absolute rule. A port may be defined in your code and still represent an external dependency. Replace the port because of its architectural role, not because of which repository owns its interface.

Choose the narrowest useful double:

- **Fake**: working deterministic implementation, useful across many behavior tests
- **Stub**: returns a controlled result or failure for one scenario
- **Spy**: records an externally meaningful interaction when that interaction is the contract
- **Mock**: expectation-driven double; use sparingly because it couples tests to interaction structure

## Designing for Mockability

At system boundaries, design interfaces that are easy to mock:

**1. Use dependency injection**

Pass external dependencies in rather than creating them internally:

```python
class PaymentPort(Protocol):
    def charge(self, amount: Decimal) -> ChargeResult: ...


def process_payment(order: Order, payment: PaymentPort) -> ChargeResult:
    return payment.charge(order.total)
```

**2. Prefer SDK-style interfaces over generic fetchers**

Create specific functions for each external operation instead of one generic function with conditional logic:

```python
class OrdersPort(Protocol):
    async def get_user(self, user_id: UserId) -> User: ...

    async def get_orders(self, user_id: UserId) -> list[Order]: ...

    async def create_order(self, command: CreateOrder) -> Order: ...
```

The use-case-specific port means:
- Each double returns one specific domain shape
- No conditional logic in test setup
- Easier to see which capability a test exercises
- Type safety per operation

Always retain at least one real integration or contract test for an adapter that is otherwise replaced by a double. A fake can drift from the real dependency.
