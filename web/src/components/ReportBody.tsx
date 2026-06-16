import type { Curated } from "../types";
import VerdictBadge from "./VerdictBadge";

interface ReportBodyProps {
  data: Curated;
}

function nullOrDash(v: string | number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return String(v);
}

function boolOrDash(
  v: boolean | null | undefined,
  trueLabel: string,
  falseLabel: string,
): string {
  if (v === null || v === undefined) return "—";
  return v ? trueLabel : falseLabel;
}

function mapOutcome(s: string): string {
  const map: Record<string, string> = {
    succeeded: "成功",
    failed: "失敗",
    unclear: "不明",
    not_attempted: "未嘗試",
  };
  return map[s] ?? s;
}

export default function ReportBody({ data }: ReportBodyProps) {
  const obs = data.observation;
  const sig = data.signals;
  const tel = data.telemetry;
  const declined = data.payment_explicitly_declined;
  // The card-decline signal is only meaningful if a card was actually submitted to a
  // payment flow. If no card/payment was attempted, "no explicit decline" says nothing.
  const cardSubmitted =
    obs.credit_card_submitted || obs.payment_outcome !== "not_attempted";

  const costStr =
    tel.cost_usd == null
      ? "(pricing unknown)"
      : `$${tel.cost_usd.toFixed(4)}`;

  const stageStr = tel.stages
    .map((s) => `${s.name} ${s.duration_s.toFixed(1)}s`)
    .join(" / ");

  return (
    <div className="space-y-6">
      {/* 1. Header */}
      <div className="bg-white border border-gray-200 rounded-lg p-6 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <p
              className="text-2xl font-bold text-gray-900 break-all leading-tight"
              translate="no"
            >
              {data.domain}
            </p>
            <p
              className="mt-1 text-sm text-gray-500 break-all"
              title={data.url}
              translate="no"
            >
              {data.url}
            </p>
            <p className="mt-1 text-xs text-gray-400">
              {new Date(data.started_at).toLocaleString("zh-TW")}
            </p>
          </div>
          <div className="flex flex-col items-end gap-1 shrink-0">
            <VerdictBadge verdict={data.verdict} />
            {data.scam_type && (
              <span className="text-xs text-gray-600">
                類型：{data.scam_type}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* 2. Card-decline signal — only shown when a card was actually submitted, where it
          carries weight. It is one of several signals; the verdict also draws on the
          reasoning, risk factors, and static signals below. */}
      {cardSubmitted &&
        (declined ? (
          <div
            className="border-2 border-green-400 bg-green-50 rounded-lg p-5"
            role="region"
            aria-labelledby="signal-heading"
          >
            <h2
              id="signal-heading"
              className="text-base font-bold text-green-800 mb-2"
            >
              <span aria-hidden="true">✓ </span>出現明確刷卡失敗
            </h2>
            <p className="text-sm text-green-800 leading-relaxed">
              此網站明確回報刷卡失敗／卡號無效，代表背後有真實的金流處理器在驗證——屬於合法網站的跡象。
            </p>
          </div>
        ) : (
          <div
            className="border-2 border-red-400 bg-red-50 rounded-lg p-5"
            role="alert"
          >
            <h2 className="text-base font-bold text-red-800 mb-2">
              <span aria-hidden="true">⚠ </span>送出偽造卡號後未出現明確刷卡失敗
            </h2>
            <p className="text-sm text-red-800 leading-relaxed">
              合法網站背後有真正的金流處理器，會明確拒絕一張偽造的卡號；此網站收下了卡號卻沒有出現明確的刷卡失敗訊息——這是詐騙網站的常見特徵（它們沒有真正的金流，因此照單全收）。
            </p>
          </div>
        ))}

      {/* 3. 判定理由 */}
      <div className="bg-white border border-gray-200 rounded-lg p-6 shadow-sm">
        <h2 className="text-base font-semibold text-gray-900 mb-3">
          判定理由
        </h2>
        <p className="text-sm text-gray-700 leading-relaxed">{data.reasoning}</p>
        {data.risk_factors.length > 0 && (
          <ul className="mt-3 space-y-1 list-disc list-inside">
            {data.risk_factors.map((rf, i) => (
              <li key={i} className="text-sm text-gray-700">
                {rf}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* 4. 造訪觀察 */}
      <div className="bg-white border border-gray-200 rounded-lg p-6 shadow-sm space-y-5">
        <h2 className="text-base font-semibold text-gray-900">造訪觀察</h2>

        {/* website_summary */}
        <div>
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
            網站摘要
          </h3>
          <p className="text-sm text-gray-700 leading-relaxed">
            {obs.website_summary}
          </p>
        </div>

        {/* form_fields_requested */}
        <div>
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
            要求的資料欄位
          </h3>
          {obs.form_fields_requested.length === 0 ? (
            <span className="text-sm text-gray-400">無</span>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {obs.form_fields_requested.map((f, i) => (
                <span
                  key={i}
                  className="inline-block px-2 py-0.5 bg-gray-100 text-gray-700 text-xs rounded"
                >
                  {f}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* unexpected_events */}
        <div>
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
            異常事件
          </h3>
          {obs.unexpected_events.length === 0 ? (
            <span className="text-sm text-gray-400">無</span>
          ) : (
            <ul className="space-y-0.5 list-disc list-inside">
              {obs.unexpected_events.map((ev, i) => (
                <li key={i} className="text-sm text-gray-700">
                  {ev}
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* login / payment / credit card grid */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div>
            <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
              登入結果
            </h3>
            <p className="text-sm text-gray-800">
              {mapOutcome(obs.login_outcome)}
            </p>
          </div>
          <div>
            <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
              付款結果
            </h3>
            <p className="text-sm text-gray-800">
              {mapOutcome(obs.payment_outcome)}
            </p>
          </div>
          <div>
            <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
              是否送出信用卡
            </h3>
            <p className="text-sm text-gray-800">
              {obs.credit_card_submitted ? "是" : "否"}
            </p>
          </div>
        </div>

        {/* outgoing_links */}
        <div>
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
            外連網域
          </h3>
          {obs.outgoing_links.length === 0 ? (
            <span className="text-sm text-gray-400">無</span>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {obs.outgoing_links.map((link, i) => (
                <span
                  key={i}
                  className="inline-block px-2 py-0.5 bg-gray-100 text-gray-700 text-xs rounded break-all"
                >
                  {link}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 5. 靜態訊號 */}
      <div className="bg-white border border-gray-200 rounded-lg p-6 shadow-sm">
        <h2 className="text-base font-semibold text-gray-900 mb-4">靜態訊號</h2>
        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-3">
          {(
            [
              {
                label: "網域年齡",
                value:
                  sig.domain_age_days != null
                    ? `${sig.domain_age_days} 天`
                    : "—",
              },
              {
                label: "到期天數",
                value:
                  sig.domain_days_until_expiration != null
                    ? `${sig.domain_days_until_expiration} 天`
                    : "—",
              },
              { label: "註冊商", value: nullOrDash(sig.registrar) },
              { label: "註冊國", value: nullOrDash(sig.registrant_country) },
              {
                label: "隱私保護",
                value: boolOrDash(sig.privacy_protected, "是", "否"),
              },
              { label: "TLS 簽發者", value: nullOrDash(sig.tls_issuer) },
              {
                label: "TLS 憑證年齡",
                value:
                  sig.tls_age_days != null ? `${sig.tls_age_days} 天` : "—",
              },
              {
                label: "免費 DV 憑證",
                value: boolOrDash(sig.tls_is_free_dv, "是", "否"),
              },
              {
                label: "MX 紀錄",
                value: boolOrDash(sig.dns_has_mx, "有", "無"),
              },
              {
                label: "Nameservers",
                value:
                  sig.dns_nameservers.length > 0
                    ? sig.dns_nameservers.join(", ")
                    : "—",
              },
            ] as { label: string; value: string }[]
          ).map(({ label, value }) => (
            <div key={label}>
              <dt className="text-xs font-semibold text-gray-500">{label}</dt>
              <dd className="mt-0.5 text-sm text-gray-800 break-all">{value}</dd>
            </div>
          ))}
        </dl>
      </div>

      {/* 6. 執行資訊 */}
      <div className="text-xs text-gray-400 px-1 pb-4 space-y-1">
        <p>
          耗時 {tel.duration_s.toFixed(1)}s・成本 {costStr}・Tokens{" "}
          {tel.total_tokens.toLocaleString()}
        </p>
        {stageStr && <p>{stageStr}</p>}
      </div>
    </div>
  );
}
