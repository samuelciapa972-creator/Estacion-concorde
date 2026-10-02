import { render, screen } from "@testing-library/react";
import { Search } from "lucide-react";
import { describe, expect, it } from "vitest";
import { Boton } from "./ui";

describe("Boton con ícono", () => {
  it("dibuja el ícono como SVG decorativo y conserva el nombre accesible", () => {
    render(<Boton icono={Search}>Buscar</Boton>);
    const boton = screen.getByRole("button", { name: "Buscar" });
    const svg = boton.querySelector("svg");
    expect(svg).not.toBeNull();
    expect(svg?.getAttribute("aria-hidden")).toBe("true");
  });

  it("sin ícono no agrega ningún SVG", () => {
    render(<Boton>Buscar</Boton>);
    expect(screen.getByRole("button", { name: "Buscar" }).querySelector("svg")).toBeNull();
  });
});
