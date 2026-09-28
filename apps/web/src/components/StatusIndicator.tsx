import { AlertTriangle, CheckCircle2, CircleDashed, XCircle } from "lucide-react";


type StatusTone = "ready" | "warning" | "danger" | "neutral";

interface StatusIndicatorProps {
  label: string;
  tone: StatusTone;
}

const icons = {
  ready: CheckCircle2,
  warning: AlertTriangle,
  danger: XCircle,
  neutral: CircleDashed,
};

export function StatusIndicator({ label, tone }: StatusIndicatorProps) {
  const Icon = icons[tone];
  return (
    <span className={`status-indicator status-indicator--${tone}`}>
      <Icon aria-hidden="true" />
      {label}
    </span>
  );
}
