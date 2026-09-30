# Interface Design for Testability

Good interfaces make testing natural:

1. **Accept dependencies, don't create them**

   ```python
   def process_order(order: Order, payment_gateway: PaymentPort) -> Receipt:
       return payment_gateway.charge(order.total)
   ```

2. **Return results, don't produce side effects**

   ```python
   def calculate_discount(cart: Cart) -> Discount:
       ...
   ```

3. **Small surface area**
   - Fewer methods = fewer tests needed
   - Fewer params = simpler test setup

4. **Keep policy separate from infrastructure**
   - Application code depends on a small port
   - Adapters own HTTP, database, framework, or vendor details
   - Tests can exercise policy without patching framework internals

5. **Make lifecycle behavior explicit**
   - Expose close/shutdown behavior for owned resources
   - Pass clocks or cancellation/deadline controls when time is part of the contract
   - Ensure cleanup can be asserted after success and every terminal failure path
