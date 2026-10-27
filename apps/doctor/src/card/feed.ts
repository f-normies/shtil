/** Лента событий пациента (спека #13, раздел 4.1): шкалы, сигналы и предложения. */
import type { Schemas } from "@compass/api-client";
import { proposalTitle } from "./proposals";

export type FeedKind = "assessment" | "alert" | "proposal";
export type FeedEvent = { key: string; kind: FeedKind; at: string; text: string };

const LIMIT = 20;

type FeedInput = {
  assessments: readonly Schemas["AssessmentOut"][];
  alerts: readonly Schemas["AlertOut"][];
  proposals: readonly Schemas["ProposalOut"][];
  currentDoctorId: string;
};

/** 20 последних событий, новые первыми. */
export function buildFeed({
  assessments,
  alerts,
  proposals,
  currentDoctorId,
}: FeedInput): FeedEvent[] {
  const byWhom = (id: string | null) => (id === currentDoctorId ? "вами" : "другим врачом");
  const events: FeedEvent[] = [];
  for (const a of assessments) {
    const first = Object.values(a.scores)[0];
    const score = first ? `: ${first.value}, ${first.band.label}` : "";
    events.push({
      key: `assessment:${a.id}`,
      kind: "assessment",
      at: a.completed_at,
      text: `${a.scale_title}${score}`,
    });
  }
  for (const a of alerts) {
    events.push({
      key: `alert:${a.id}`,
      kind: "alert",
      at: a.created_at,
      text: `Сигнал: ${a.title}`,
    });
    if (a.acknowledged_at) {
      events.push({
        key: `alert-ack:${a.id}`,
        kind: "alert",
        at: a.acknowledged_at,
        text: `Сигнал взят в работу ${byWhom(a.acknowledged_by)}`,
      });
    }
    // Отсрочка и решение (спека #95). «Взять в работу» уже выше по `acknowledged_at`.
    (a.actions ?? []).forEach((x, i) => {
      const who = byWhom(x.actor_id);
      const text =
        x.action === "snooze" && x.snoozed_until
          ? `Сигнал отложен ${who} до ${x.snoozed_until.split("-").reverse().join(".")}`
          : x.action === "resolve"
            ? `Решение по сигналу ${who}: ${[x.decision_text, x.reason].filter(Boolean).join(". ")}`
            : null;
      if (text) {
        events.push({ key: `alert-action:${a.id}:${i}`, kind: "alert", at: x.at, text });
      }
    });
  }
  for (const p of proposals) {
    const title = proposalTitle(p);
    const named = p.kind === "freetext.finding" ? title.toLowerCase() : title;
    events.push({
      key: `proposal:${p.id}`,
      kind: "proposal",
      at: p.created_at,
      text: `Предложение: ${named}`,
    });
    if (p.decided_at && p.status !== "proposed") {
      const verdict = p.status === "confirmed" ? "Подтверждено" : "Отклонено";
      const reason = p.decision_comment ? `: ${p.decision_comment}` : "";
      events.push({
        key: `proposal-decision:${p.id}`,
        kind: "proposal",
        at: p.decided_at,
        text: `${verdict} ${byWhom(p.decided_by)}${reason}`,
      });
    }
  }
  return events.sort((a, b) => b.at.localeCompare(a.at)).slice(0, LIMIT);
}

export type FeedDay = { key: string; title: string; events: FeedEvent[] };

const DAY_MONTH = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long" });
const WEEKDAY = new Intl.DateTimeFormat("ru-RU", { weekday: "long" });

/**
 * Лента по дням в часовом поясе браузера: «Сегодня, 19 октября», «Вчера, 18 октября»,
 * раньше — «14 октября, среда». События — новые первыми, как из `buildFeed`.
 */
export function feedByDay(events: readonly FeedEvent[], now: Date): FeedDay[] {
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const days: FeedDay[] = [];
  for (const e of events) {
    const at = new Date(e.at);
    const key = at.toDateString();
    let day = days.at(-1);
    if (day?.key !== key) {
      const date = DAY_MONTH.format(at);
      const title =
        key === now.toDateString()
          ? `Сегодня, ${date}`
          : key === yesterday.toDateString()
            ? `Вчера, ${date}`
            : `${date}, ${WEEKDAY.format(at)}`;
      day = { key, title, events: [] };
      days.push(day);
    }
    day.events.push(e);
  }
  return days;
}
