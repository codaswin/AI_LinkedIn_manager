const STATUS_CLASS: Record<string, string> = {
  pending: "badge badge-pending",
  approved: "badge badge-approved",
  auto_applied: "badge badge-approved",
  rejected: "badge badge-rejected",
  success: "badge badge-approved",
  error: "badge badge-rejected",
  timeout: "badge badge-rejected",
  blocked: "badge badge-rejected",
};

export function StatusBadge({ status }: { status: string }) {
  return <span className={STATUS_CLASS[status] ?? "badge"}>{status.replace(/_/g, " ")}</span>;
}
