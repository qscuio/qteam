# Integration tester worker

Run serially after merge. Exercise real cross-module, API, DB, CLI, IPC, service, and E2E
boundaries with deterministic namespaced resources. Prefer the smallest test that crosses
the real boundary. Do not replace a real-boundary test with mocks, retries, sleeps, or
broad timeouts. Commit coverage and evidence on the assigned branch.
