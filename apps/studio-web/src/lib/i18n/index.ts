import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import {
  DEFAULT_LANGUAGE,
  getStoredLanguage,
  setStoredLanguage,
  type AppLanguage,
} from "@/lib/i18n/storage";

// Import Vietnamese namespaces
import viCommon from "./locales/vi/common.json";
import viAdminSession from "./locales/vi/admin-session.json";
import viApiStatus from "./locales/vi/api-status.json";
import viWorkspaceOverview from "./locales/vi/workspace-overview.json";
import viProjects from "./locales/vi/projects.json";
import viConnections from "./locales/vi/connections.json";
import viScriptStudio from "./locales/vi/script-studio.json";
import viShotBoard from "./locales/vi/shot-board.json";
import viWorkflowCanvas from "./locales/vi/workflow-canvas.json";
import viQueueMonitor from "./locales/vi/queue-monitor.json";
import viTimelineEditor from "./locales/vi/timeline-editor.json";
import viSystemOps from "./locales/vi/system-ops.json";

// Import English namespaces
import enCommon from "./locales/en/common.json";
import enAdminSession from "./locales/en/admin-session.json";
import enApiStatus from "./locales/en/api-status.json";
import enWorkspaceOverview from "./locales/en/workspace-overview.json";
import enProjects from "./locales/en/projects.json";
import enConnections from "./locales/en/connections.json";
import enScriptStudio from "./locales/en/script-studio.json";
import enShotBoard from "./locales/en/shot-board.json";
import enWorkflowCanvas from "./locales/en/workflow-canvas.json";
import enQueueMonitor from "./locales/en/queue-monitor.json";
import enTimelineEditor from "./locales/en/timeline-editor.json";
import enSystemOps from "./locales/en/system-ops.json";

export const defaultNS = "common";

export const resources = {
  vi: {
    common: viCommon,
    "admin-session": viAdminSession,
    "api-status": viApiStatus,
    "workspace-overview": viWorkspaceOverview,
    projects: viProjects,
    connections: viConnections,
    "script-studio": viScriptStudio,
    "shot-board": viShotBoard,
    "workflow-canvas": viWorkflowCanvas,
    "queue-monitor": viQueueMonitor,
    "timeline-editor": viTimelineEditor,
    "system-ops": viSystemOps,
  },
  en: {
    common: enCommon,
    "admin-session": enAdminSession,
    "api-status": enApiStatus,
    "workspace-overview": enWorkspaceOverview,
    projects: enProjects,
    connections: enConnections,
    "script-studio": enScriptStudio,
    "shot-board": enShotBoard,
    "workflow-canvas": enWorkflowCanvas,
    "queue-monitor": enQueueMonitor,
    "timeline-editor": enTimelineEditor,
    "system-ops": enSystemOps,
  },
} as const;

export type AppNamespace = keyof (typeof resources)["vi"];

const initialLang = getStoredLanguage();

// Synchronize document.documentElement.lang on startup if window is defined
if (typeof document !== "undefined") {
  document.documentElement.lang = initialLang;
}

i18n.use(initReactI18next).init({
  resources,
  lng: initialLang,
  fallbackLng: DEFAULT_LANGUAGE,
  defaultNS,
  interpolation: {
    escapeValue: false, // React already escapes values
  },
  react: {
    useSuspense: false,
  },
});

export async function changeLanguage(lang: AppLanguage): Promise<void> {
  setStoredLanguage(lang);
  await i18n.changeLanguage(lang);
  if (typeof document !== "undefined") {
    document.documentElement.lang = lang;
  }
}

export default i18n;
