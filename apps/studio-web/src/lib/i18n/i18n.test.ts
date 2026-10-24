import { afterEach, beforeEach, describe, expect, it } from "vitest";
import i18n, { changeLanguage } from "./index";
import { formatDate, formatDateTime, formatNumber, formatTime } from "./formatters";
import {
  DEFAULT_LANGUAGE,
  LOCALE_STORAGE_KEY,
  clearStoredLanguage,
  getStoredLanguage,
  setStoredLanguage,
} from "./storage";

describe("i18n infrastructure & storage", () => {
  beforeEach(() => {
    clearStoredLanguage();
    void changeLanguage("vi");
  });

  afterEach(() => {
    clearStoredLanguage();
    void changeLanguage("vi");
  });

  it("defaults to vi when localStorage is empty without guessing from browser", () => {
    clearStoredLanguage();
    expect(getStoredLanguage()).toBe(DEFAULT_LANGUAGE);
    expect(DEFAULT_LANGUAGE).toBe("vi");
  });

  it("persists language selection in localStorage", () => {
    setStoredLanguage("en");
    expect(getStoredLanguage()).toBe("en");
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en");
    setStoredLanguage("vi");
    expect(getStoredLanguage()).toBe("vi");
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("vi");
  });

  it("updates document.documentElement.lang and i18n language on changeLanguage", async () => {
    await changeLanguage("en");
    expect(i18n.language).toBe("en");
    expect(document.documentElement.lang).toBe("en");
    expect(getStoredLanguage()).toBe("en");

    await changeLanguage("vi");
    expect(i18n.language).toBe("vi");
    expect(document.documentElement.lang).toBe("vi");
    expect(getStoredLanguage()).toBe("vi");
  });
});

describe("i18n formatters", () => {
  const sampleIso = "2026-10-24T12:30:00Z";

  it("formats date according to active locale", () => {
    const viFormatted = formatDate(sampleIso, { dateStyle: "short" }, "vi");
    const enFormatted = formatDate(sampleIso, { dateStyle: "short" }, "en");
    expect(viFormatted).toBeDefined();
    expect(enFormatted).toBeDefined();
    expect(viFormatted.length).toBeGreaterThan(0);
    expect(enFormatted.length).toBeGreaterThan(0);
  });

  it("formats time according to active locale", () => {
    const viFormatted = formatTime(sampleIso, { timeStyle: "short" }, "vi");
    const enFormatted = formatTime(sampleIso, { timeStyle: "short" }, "en");
    expect(viFormatted).toBeDefined();
    expect(enFormatted).toBeDefined();
  });

  it("formats date and time together", () => {
    const formatted = formatDateTime(sampleIso, undefined, "vi");
    expect(formatted).toBeDefined();
    expect(formatted.length).toBeGreaterThan(0);
  });

  it("formats number according to active locale", () => {
    const viFormatted = formatNumber(1234567.89, undefined, "vi");
    const enFormatted = formatNumber(1234567.89, undefined, "en");
    expect(viFormatted).toBeDefined();
    expect(enFormatted).toBeDefined();
  });

  it("handles null and undefined gracefully in formatters", () => {
    expect(formatDate(null)).toBe("");
    expect(formatDate(undefined)).toBe("");
    expect(formatTime(null)).toBe("");
    expect(formatTime(undefined)).toBe("");
    expect(formatDateTime(null)).toBe("");
    expect(formatDateTime(undefined)).toBe("");
    expect(formatNumber(null)).toBe("");
    expect(formatNumber(undefined)).toBe("");
  });
});
