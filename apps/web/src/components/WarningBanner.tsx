import type { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";


interface WarningBannerProps {
  title: string;
  children: ReactNode;
  danger?: boolean;
}

export function WarningBanner({ title, children, danger = false }: WarningBannerProps) {
  return (
    <div className={`warning-banner${danger ? " warning-banner--danger" : ""}`} role="alert">
      <AlertTriangle aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <div>{children}</div>
      </div>
    </div>
  );
}
