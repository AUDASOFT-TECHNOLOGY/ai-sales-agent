import { describe, expect, it } from "vitest";
import { adminHashFor } from "./adminEntry";

describe("Admin address", () => {
  it("sends a plain /admin address to the Admin route, never the landing", () => {
    expect(adminHashFor("/admin/login", "", "")).toBe("/#/admin/login");
    expect(adminHashFor("/admin/", "?next=%2Fclients", "")).toBe("/#/admin?next=%2Fclients");
    expect(adminHashFor("/admin", "", "")).toBe("/#/admin");
  });
  it("leaves hash routes, the API and other pages alone", () => {
    expect(adminHashFor("/", "", "#/admin/login")).toBeNull();
    expect(adminHashFor("/admin/v1/clients", "", "")).toBeNull();
    expect(adminHashFor("/", "", "")).toBeNull();
    expect(adminHashFor("/administrator", "", "")).toBeNull();
  });
});
