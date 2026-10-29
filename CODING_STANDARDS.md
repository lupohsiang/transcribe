# Coding standards

## Test observable behavior

Do not write tautological tests. A test must exercise production behavior and compare its result with an independently defined expectation. It must fail when that behavior is wrong.

- Use literal expected results, independently checked reference data, or observable effects such as saved file contents and process exit codes.
- Do not derive the expected result by calling the function under test on the same input or by copying its algorithm. Property checks, including idempotence, must also have an independent concrete behavioral expectation.
- Do not assert that fixtures equal themselves, repeat implementation constants, or check only that a mocked function was called.
- When using test doubles, exercise the real integration boundary and assert the resulting output, state, or payload.
- A rejection or empty-result test must have a corresponding valid or nonempty case.
- During review, identify a concrete behavioral defect that each test would catch. Rewrite or remove tests that cannot distinguish correct behavior from that defect.

Successful inference, fluent output, and larger transcript character counts do not establish recognition accuracy. Use a human-checked reference to measure character error rate. Evaluate speaker attribution and timestamps separately.
