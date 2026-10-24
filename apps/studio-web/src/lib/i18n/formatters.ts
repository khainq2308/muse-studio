import i18n from "i18next";

export function getIntlLocale(language?: string): string {
  const current = language || i18n.language || "vi";
  return current === "en" ? "en-US" : "vi-VN";
}

function parseDateInput(value: Date | string | number | null | undefined): Date | null {
  if (value === null || value === undefined) return null;
  if (value instanceof Date) {
    return isNaN(value.getTime()) ? null : value;
  }
  if (typeof value === "number") {
    const d = new Date(value);
    return isNaN(d.getTime()) ? null : d;
  }
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp) : null;
}

/**
 * Format a date via Intl.DateTimeFormat according to active locale.
 */
export function formatDate(
  value: Date | string | number | null | undefined,
  options?: Intl.DateTimeFormatOptions,
  language?: string,
): string {
  const date = parseDateInput(value);
  if (!date) return "";
  const locale = getIntlLocale(language);
  return new Intl.DateTimeFormat(locale, options ?? { dateStyle: "medium" }).format(date);
}

/**
 * Format a time via Intl.DateTimeFormat according to active locale.
 */
export function formatTime(
  value: Date | string | number | null | undefined,
  options?: Intl.DateTimeFormatOptions,
  language?: string,
): string {
  const date = parseDateInput(value);
  if (!date) return "";
  const locale = getIntlLocale(language);
  return new Intl.DateTimeFormat(locale, options ?? { timeStyle: "medium" }).format(date);
}

/**
 * Format a date and time via Intl.DateTimeFormat according to active locale.
 */
export function formatDateTime(
  value: Date | string | number | null | undefined,
  options?: Intl.DateTimeFormatOptions,
  language?: string,
): string {
  const date = parseDateInput(value);
  if (!date) return "";
  const locale = getIntlLocale(language);
  return new Intl.DateTimeFormat(
    locale,
    options ?? { dateStyle: "medium", timeStyle: "medium" },
  ).format(date);
}

/**
 * Format a number via Intl.NumberFormat according to active locale.
 */
export function formatNumber(
  value: number | null | undefined,
  options?: Intl.NumberFormatOptions,
  language?: string,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "";
  const locale = getIntlLocale(language);
  return new Intl.NumberFormat(locale, options).format(value);
}
