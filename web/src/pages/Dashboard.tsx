import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { api } from "../api";
import type { Stats, Verdict } from "../types";
import { verdictColor } from "../components/VerdictBadge";
import StatCard from "../components/StatCard";

const VERDICT_ORDER: Verdict[] = [
  "scam",
  "likely_scam",
  "uncertain",
  "likely_legitimate",
  "legitimate",
];

const VERDICT_LABELS: Record<Verdict, string> = {
  scam: "詐騙",
  likely_scam: "可能詐騙",
  uncertain: "不確定",
  likely_legitimate: "可能合法",
  legitimate: "合法",
};

export default function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    api
      .stats()
      .then((data) => {
        setStats(data);
        setLoading(false);
      })
      .catch(() => {
        setError(true);
        setLoading(false);
      });
  }, []);

  if (loading) return <p className="text-gray-500">載入中…</p>;
  if (error || !stats) return <p className="text-red-500">載入失敗</p>;

  if (stats.total === 0) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold">儀表板</h1>
        <div className="bg-white border rounded-lg p-8 text-center text-gray-500">
          尚無分析紀錄，前往
          <Link to="/query" className="text-blue-600 underline mx-1">
            『查詢』
          </Link>
          分析第一個網站。
        </div>
      </div>
    );
  }

  const verdictChartData = VERDICT_ORDER.map((key) => ({
    name: VERDICT_LABELS[key],
    value: stats.by_verdict[key] ?? 0,
    key,
  }));

  const scamTypeChartData = Object.entries(stats.scam_types).map(
    ([name, value]) => ({ name, value })
  );

  const avgCost = stats.total > 0 ? stats.total_cost_usd / stats.total : 0;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">儀表板</h1>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="總分析數" value={String(stats.total)} />
        <StatCard
          label="詐騙"
          value={String(stats.scam_count)}
          accent="text-red-600"
        />
        <StatCard
          label="合法"
          value={String(stats.legit_count)}
          accent="text-green-600"
        />
        <StatCard
          label="不確定"
          value={String(stats.uncertain_count)}
          accent="text-amber-600"
        />
        <StatCard
          label="詐騙率"
          value={(stats.scam_rate * 100).toFixed(1) + "%"}
          accent="text-red-600"
        />
        <StatCard
          label="平均耗時"
          value={stats.avg_duration_s.toFixed(1) + "s"}
        />
        <StatCard
          label="平均成本"
          value={"$" + avgCost.toFixed(4)}
        />
        <StatCard
          label="總成本"
          value={"$" + stats.total_cost_usd.toFixed(4)}
        />
      </div>

      <section
        className="bg-white border rounded-lg p-5 shadow-sm"
        aria-label="判定分佈"
      >
        <h2 className="text-lg font-semibold mb-4">判定分佈</h2>
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={verdictChartData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" />
            <YAxis allowDecimals={false} />
            <Tooltip />
            <Bar dataKey="value">
              {verdictChartData.map((entry) => (
                <Cell
                  key={entry.key}
                  fill={verdictColor(entry.key as Verdict)}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </section>

      <section
        className="bg-white border rounded-lg p-5 shadow-sm"
        aria-label="詐騙類型"
      >
        <h2 className="text-lg font-semibold mb-4">詐騙類型</h2>
        {scamTypeChartData.length > 0 ? (
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={scamTypeChartData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="name" />
              <YAxis allowDecimals={false} />
              <Tooltip />
              <Bar dataKey="value" fill="#dc2626" />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <p className="text-gray-500">尚無詐騙類型資料</p>
        )}
      </section>
    </div>
  );
}
