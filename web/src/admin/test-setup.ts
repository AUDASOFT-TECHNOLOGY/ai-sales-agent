import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// No `test.globals` in vite.config.ts (test files import describe/it/expect explicitly), so
// @testing-library/react's automatic per-test DOM cleanup never triggers on its own: register it
// here instead, once, for every test file.
afterEach(() => {
  cleanup();
});
