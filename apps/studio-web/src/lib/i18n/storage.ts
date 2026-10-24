export const LOCALE_STORAGE_KEY = "muse:studio:locale";

export const SUPPORTED_LANGUAGES = ["vi", "en"] as const;
export type AppLanguage = (typeof SUPPORTED_LANGUAGES)[number];

export const DEFAULT_LANGUAGE: AppLanguage = "vi";

export function isSupportedLanguage(value: unknown): value is AppLanguage {
  return typeof value === "string" && (SUPPORTED_LANGUAGES as readonly string[]).includes(value);
}

/**
 * Retrieve the user's stored language preference.
 * Defaults strictly to "vi" (Vietnamese) when no valid preference is saved.
 * Does NOT infer or guess from navigator.language.
 */
export function getStoredLanguage(): AppLanguage {
  if (typeof window === "undefined" || !window.localStorage) {
    return DEFAULT_LANGUAGE;
  }
  try {
    const stored = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    return isSupportedLanguage(stored) ? stored : DEFAULT_LANGUAGE;
  } catch {
    return DEFAULT_LANGUAGE;
  }
}

/**
 * Persist the user's language selection to localStorage.
 */
export function setStoredLanguage(lang: AppLanguage): void {
  if (typeof window === "undefined" || !window.localStorage) return;
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, lang);
  } catch {
    // Ignore storage quota or access errors
  }
}

/**
 * Clear the stored language (used in tests to verify fresh launch behavior).
 */
export function clearStoredLanguage(): void {
  if (typeof window === "undefined" || !window.localStorage) return;
  try {
    window.localStorage.removeItem(LOCALE_STORAGE_KEY);
  } catch {
    // Ignore storage quota or access errors
  }
}
