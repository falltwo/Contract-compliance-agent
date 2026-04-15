export type AppTarget = "frontend" | "admin" | "full";

// Safe default: frontend should never expose admin navigation accidentally.
const rawTarget = String(import.meta.env.VITE_APP_TARGET || "frontend").toLowerCase();

export const APP_TARGET: AppTarget =
  rawTarget === "frontend" || rawTarget === "admin" ? rawTarget : "full";

export const IS_FRONTEND_TARGET = APP_TARGET === "frontend";
export const IS_ADMIN_TARGET = APP_TARGET === "admin";
