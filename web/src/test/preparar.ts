import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { cerrarSesion } from "../api/sesion";

afterEach(() => {
  cleanup();
  cerrarSesion();
  vi.unstubAllGlobals();
});
