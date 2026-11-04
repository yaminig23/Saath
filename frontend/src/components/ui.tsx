/* Saath design-system components: every surface inherits its look from the
   tokens in index.css, so one token change retints the whole dashboard. */
import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";

type DivProps = HTMLAttributes<HTMLDivElement>;
type BtnProps = ButtonHTMLAttributes<HTMLButtonElement>;

export function GlassCard({ className = "", ...rest }: DivProps) {
  return <div className={`glass-card ${className}`.trim()} {...rest} />;
}

export function DashboardWidget({
  title,
  action,
  className = "",
  children,
}: {
  title: string;
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <GlassCard className={`p-4 ${className}`.trim()}>
      <div className="flex items-center gap-2">
        <h3 className="text-base font-semibold">{title}</h3>
        {action && <span className="ml-auto flex shrink-0">{action}</span>}
      </div>
      <div className="mt-2">{children}</div>
    </GlassCard>
  );
}

export function GlowButton({ className = "", type = "button", ...rest }: BtnProps) {
  return <button type={type} className={`glow-btn ${className}`.trim()} {...rest} />;
}

export function GhostButton({ className = "", type = "button", ...rest }: BtnProps) {
  return <button type={type} className={`ghost-btn ${className}`.trim()} {...rest} />;
}

export function GradientIcon({ tile, children }: { tile: 1 | 2 | 3 | 4 | 5; children: ReactNode }) {
  return <span className={`grad-tile tile-${tile} flex h-12 w-12 shrink-0 items-center justify-center`}>{children}</span>;
}

export function SuggestionRow({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{ borderColor: "var(--sa-line)" }}
      className="flex min-h-11 w-full items-center gap-2 rounded-xl border bg-white/5 px-3 py-2 text-left text-[15px] transition-colors hover:border-[var(--sa-glow)]"
    >
      <span className="grow truncate">{children}</span>
      <span aria-hidden="true" className="sa-muted shrink-0">
        →
      </span>
    </button>
  );
}

export function ContactRow({
  name,
  detail,
  onMessage,
}: {
  name: string;
  detail?: string;
  onMessage: () => void;
}) {
  const initials = name
    .split(/\s+/)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
  return (
    <li className="flex items-center gap-2 text-[15px]">
      <span className="avatar-ring flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-sm font-bold" aria-hidden="true">
        {initials || "?"}
      </span>
      <span className="grow truncate">
        {name}
        {detail && <span className="sa-muted"> · {detail}</span>}
      </span>
      <GhostButton onClick={onMessage} className="min-h-10 shrink-0 px-3 py-1.5 text-sm" aria-label={`Message ${name}`}>
        Message
      </GhostButton>
    </li>
  );
}

export function StatusDot({ tone, label }: { tone: "amber" | "green" | "red"; label: string }) {
  return (
    <span className="flex items-center gap-2 text-sm">
      <span className={`status-dot status-${tone} h-2.5 w-2.5 shrink-0`} aria-hidden="true" />
      {label}
    </span>
  );
}

export function NavigationItem({
  active,
  onClick,
  label,
  icon,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  icon: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={`nav-item flex min-h-12 items-center gap-3 px-3 text-[15px] ${active ? "nav-item-active font-semibold" : ""}`.trim()}
    >
      {icon}
      {label}
    </button>
  );
}
