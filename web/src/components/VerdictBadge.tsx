import type { Verdict } from "../types";

export function verdictColor(v: Verdict): string {
  const colors: Record<Verdict, string> = {
    scam: "#dc2626",
    likely_scam: "#f97316",
    uncertain: "#f59e0b",
    likely_legitimate: "#22c55e",
    legitimate: "#16a34a",
  };
  return colors[v];
}

const pillClasses: Record<Verdict, string> = {
  scam: "bg-red-100 text-red-700",
  likely_scam: "bg-orange-100 text-orange-700",
  uncertain: "bg-amber-100 text-amber-700",
  likely_legitimate: "bg-green-100 text-green-700",
  legitimate: "bg-green-200 text-green-800",
};

const verdictLabel: Record<Verdict, string> = {
  scam: "Scam",
  likely_scam: "Likely scam",
  uncertain: "Uncertain",
  likely_legitimate: "Likely legitimate",
  legitimate: "Legitimate",
};

interface VerdictBadgeProps {
  verdict: Verdict;
}

export default function VerdictBadge({ verdict }: VerdictBadgeProps) {
  return (
    <span
      className={`inline-block px-2.5 py-0.5 rounded-full text-xs font-semibold ${pillClasses[verdict]}`}
    >
      {verdictLabel[verdict]}
    </span>
  );
}
