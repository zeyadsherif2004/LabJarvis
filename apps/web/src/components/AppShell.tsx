import type { ReactNode } from "react";
import {
  Activity,
  BookOpen,
  Database,
  FlaskConical,
  Home,
  Settings2,
  ShieldCheck,
} from "lucide-react";

import type { DisplayableError } from "../errors";
import type { Route } from "../routing";
import type { ModelMode, ReadinessResponse } from "../types";
import { ModelStages, profileLabel } from "./ModelStages";

export const MODE_LABELS: Record<ModelMode, string> = {
  fake: "Development fake",
  local: "Local models",
  hosted: "Hosted API",
};

interface AppShellProps {
  readiness: ReadinessResponse | null;
  degraded: boolean;
  route: Route;
  /** The session the user is currently working in, if any. */
  sessionId: string | null;
  message: string | null;
  error: DisplayableError | null;
  onNavigate: (route: Route) => void;
  children: ReactNode;
}

export function AppShell({
  readiness,
  degraded,
  route,
  sessionId,
  message,
  error,
  onNavigate,
  children,
}: AppShellProps) {
  const destinations: { key: Route["name"]; label: string; icon: typeof Home; to: Route }[] = [
    { key: "dashboard", label: "Home", icon: Home, to: { name: "dashboard" } },
    ...(sessionId !== null
      ? ([
          {
            key: "session" as const,
            label: "Session",
            icon: ShieldCheck,
            to: { name: "session" as const, sessionId },
          },
          {
            key: "notebook" as const,
            label: "Notebook",
            icon: BookOpen,
            to: { name: "notebook" as const, sessionId },
          },
        ])
      : []),
    {
      key: "data",
      label: "Data",
      icon: Database,
      to: sessionId === null ? { name: "data" } : { name: "data", sessionId },
    },
    {
      key: "diagnostics",
      label: "Diagnostics",
      icon: Activity,
      to: { name: "diagnostics" },
    },
    {
      key: "settings",
      label: "Settings",
      icon: Settings2,
      to: { name: "settings" },
    },
  ];

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <header className="app-header">
        <button
          className="brand"
          type="button"
          aria-label="LabJarvis home"
          onClick={() => onNavigate({ name: "dashboard" })}
        >
          <FlaskConical aria-hidden="true" />
          <span>LabJarvis</span>
        </button>
        <nav aria-label="Primary navigation">
          {destinations.map(({ key, label, icon: Icon, to }) => (
            <button
              className={`nav-link${route.name === key ? " nav-link--active" : ""}`}
              type="button"
              aria-current={route.name === key ? "page" : undefined}
              onClick={() => onNavigate(to)}
              key={key}
            >
              <Icon aria-hidden="true" /> {label}
            </button>
          ))}
        </nav>
        <div className="header-status" aria-live="polite">
          {readiness === null
            ? "Checking model adapters..."
            : readiness.profile !== undefined
              ? `${profileLabel(readiness)} mode`
              : `${MODE_LABELS[readiness.mode]} mode`}
          {readiness !== null ? <ModelStages readiness={readiness} compact /> : null}
          {readiness !== null && readiness.privacy.external_model_processing ? (
            <span className="header-badge header-badge--hosted">
              Sends data off this machine
            </span>
          ) : null}
        </div>
      </header>

      {readiness?.study?.enabled === true ? (
        <div className="study-banner" role="alert">
          <strong>STUDY MODE</strong> - study {readiness.study.study_id}, participant{" "}
          {readiness.study.participant}, {readiness.study.condition} confirmation. Some proposals
          in this session are deliberately wrong. Check every value before confirming.
        </div>
      ) : null}
      {degraded ? (
        <div className="global-notice" role="status">
          {/* The service answered - it is a dependency that is not ready. Saying
              "offline" here would send the operator to diagnose the wrong thing. */}
          A model is not usable yet. Typed control still works.{" "}
          <button type="button" onClick={() => onNavigate({ name: "diagnostics" })}>
            See what is missing
          </button>
        </div>
      ) : null}

      {error !== null ? (
        <div className="global-alert" role="alert">
          <strong>{error.message}</strong>
          {error.action !== null ? <span> {error.action}</span> : null}
          {error.reference !== null ? (
            <small className="error-reference">Reference: {error.reference}</small>
          ) : null}
        </div>
      ) : null}

      {message !== null ? (
        <div className="visually-hidden" aria-live="polite">
          {message}
        </div>
      ) : null}
      {children}
    </div>
  );
}
