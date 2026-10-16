import { configure } from "@testing-library/react";

// jsdom lacks a few browser APIs the dashboard uses.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;
window.scrollTo = () => {};

// Lazy-loaded routes plus a fetch can exceed the 1 s default on a loaded CI machine. One such failure was seen
// once (not reproduced in later runs, including under CPU load); a 5 s ceiling keeps the flows deterministic.
configure({ asyncUtilTimeout: 5000 });
