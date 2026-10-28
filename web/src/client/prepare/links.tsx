/** Where the screens send the client. Inside "Prepare your agent" every destination is a place of that
 *  one screen (`/prepare?p=...`), so nothing the client does there takes them out of it; anywhere else
 *  the screens keep their own addresses until those are retired. */

import { createContext, useContext, type ReactNode } from "react";

export type Place =
  | { to: "products" }
  | { to: "product"; id: string }
  | { to: "payments" }
  | { to: "rules" }
  | { to: "connections" }
  | { to: "circuits" };

const Inside = createContext(false);

export function PrepareScope({ children }: { children: ReactNode }) {
  return <Inside.Provider value>{children}</Inside.Provider>;
}

export function prepareAddress(place: Place): string {
  switch (place.to) {
    case "products":
      return "/prepare?p=products";
    case "product":
      return `/prepare?p=products&product=${encodeURIComponent(place.id)}`;
    case "payments":
      return "/prepare?p=products";
    case "rules":
      return "/prepare?p=business";
    case "connections":
      return "/prepare?p=connect";
    case "circuits":
      return "/prepare?p=circuits";
  }
}

function ownAddress(place: Place): string {
  switch (place.to) {
    case "products":
      return "/offer/products";
    case "product":
      return `/offer/products/${encodeURIComponent(place.id)}`;
    case "payments":
      return "/offer/payments";
    case "rules":
      return "/offer/rules";
    case "connections":
      return "/connections";
    case "circuits":
      return "/circuits";
  }
}

export function useAddress(): (place: Place) => string {
  const inside = useContext(Inside);
  return inside ? prepareAddress : ownAddress;
}
