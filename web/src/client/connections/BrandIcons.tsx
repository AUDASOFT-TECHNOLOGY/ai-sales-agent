/** The mark of each service the client connects, so that "Connect my Gmail" looks like what it is. Drawn
 *  small and flat, in the service's own colours, on a white tile (readable on any button). */

import type { ReactNode } from "react";

export type Brand = "gmail" | "whatsapp" | "telegram" | "hubspot" | "drive" | "sheets";

export const MARKS: Record<Brand, ReactNode> = {
  gmail: (
    <>
      <path fill="#4285F4" d="M2 8.2V18a2 2 0 0 0 2 2h3v-9z" />
      <path fill="#34A853" d="M22 8.2V18a2 2 0 0 1-2 2h-3v-9z" />
      <path fill="#FBBC04" d="M17 5.5V11l5-3.8v-.9c0-1.4-1.6-2.2-2.7-1.4z" />
      <path fill="#EA4335" d="M7 11V5.5l5 3.8 5-3.8V11l-5 3.8z" />
      <path fill="#C5221F" d="M2 6.3v.9L7 11V5.5L4.7 4.9C3.6 4.1 2 4.9 2 6.3z" />
    </>
  ),
  sheets: (
    <>
      <path fill="#0F9D58" d="M6 2h8l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z" />
      <path fill="#87CEAC" d="M14 2l5 5h-4a1 1 0 0 1-1-1z" />
      <path fill="#fff" d="M7.5 11h9v7.5h-9zm1.4 1.4v1.5h2.2v-1.5zm3.6 0v1.5h3.1v-1.5zm-3.6 2.9v1.8h2.2v-1.8zm3.6 0v1.8h3.1v-1.8z" />
    </>
  ),
  drive: (
    <>
      <path fill="#0F9D58" d="M8.5 3 2 14.3l3 5.2 6.5-11.3z" />
      <path fill="#F4B400" d="M8.5 3h7L22 14.3h-7z" />
      <path fill="#4285F4" d="M5 19.5h14l3-5.2H8z" />
    </>
  ),
  hubspot: (
    <>
      <circle cx="15.5" cy="13.2" r="3.4" fill="none" stroke="#FF7A59" strokeWidth="2" />
      <path stroke="#FF7A59" strokeWidth="2" strokeLinecap="round" d="M15.5 9.8V6M12.3 14.8 8.6 16.4" />
      <circle cx="15.5" cy="4.6" r="1.7" fill="#FF7A59" />
      <circle cx="7" cy="17" r="2.1" fill="#FF7A59" />
      <circle cx="9.6" cy="9.4" r="1.4" fill="#FF7A59" />
    </>
  ),
  whatsapp: (
    <>
      <path fill="#25D366" d="M12 2a10 10 0 0 0-8.6 15.1L2 22l5-1.3A10 10 0 1 0 12 2z" />
      <path
        fill="#fff"
        d="M8.6 7.2c.2-.4.5-.4.7-.4h.5c.2 0 .4 0 .5.4l.7 1.7c.1.2 0 .4-.1.6l-.5.6c-.1.2-.1.3 0 .5.5.9 1.4 1.7 2.4 2.2.2.1.4.1.5-.1l.6-.7c.2-.2.4-.2.6-.1l1.6.8c.2.1.3.2.3.4 0 .7-.4 1.4-1.1 1.8-.7.4-1.5.4-2.3.2-2.4-.7-4.3-2.5-5.2-4.8-.3-.8-.2-1.7.3-2.4z"
      />
    </>
  ),
  telegram: (
    <>
      <circle cx="12" cy="12" r="10" fill="#2AABEE" />
      <path
        fill="#fff"
        d="M5.5 11.8 16.8 7.4c.5-.2 1 .1.8.9l-1.9 9c-.1.6-.5.8-1 .5l-2.8-2.1-1.4 1.3c-.2.2-.3.3-.6.3l.2-2.9 5.3-4.8c.2-.2 0-.3-.3-.1l-6.5 4.1-2.8-.9c-.6-.2-.6-.6.1-.9z"
      />
    </>
  ),
};

export function BrandIcon({ brand, size = 24 }: { brand: Brand; size?: number }) {
  return (
    <span className="rv-brand" style={{ width: size, height: size }} aria-hidden>
      <svg viewBox="0 0 24 24" width={size - 6} height={size - 6} focusable="false">
        {MARKS[brand]}
      </svg>
    </span>
  );
}
