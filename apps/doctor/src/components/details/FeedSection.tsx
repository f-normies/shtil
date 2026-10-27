import { StatusIcon } from "@compass/ui";
import { Bell, LineChart } from "lucide-react";
import { type FeedEvent, type FeedKind, feedByDay } from "../../card/feed";
import { Section } from "../signals/Section";
import { EventList, EventRow } from "./EventRow";

const TIME = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" });

/** Значок строки ленты по виду события. */
export const FEED_ICON: Record<FeedKind, React.ReactNode> = {
  assessment: <LineChart aria-hidden className="size-5" />,
  alert: <Bell aria-hidden className="size-5" />,
  proposal: <StatusIcon status="proposed" decorative />,
};

/**
 * «Лента событий» (спека #13, раздел 10.3): 20 последних событий шкал, сигналов и
 * предложений по дням, новые выше. Каждый день — своя секция, лента — список по дням.
 */
export function FeedSection({ events }: { events: readonly FeedEvent[] }) {
  const days = feedByDay(events, new Date());
  return (
    <section aria-labelledby="feed" className="flex flex-col gap-4 pt-4">
      <h2 id="feed" className="text-xl leading-[26px] font-semibold text-ink">
        Лента событий
      </h2>
      {days.length === 0 && <p className="text-sm text-ink-muted">Событий пока нет.</p>}
      {days.map((day) => (
        <Section key={day.key} title={day.title} flush>
          <EventList>
            {day.events.map((e) => (
              <li key={e.key}>
                <EventRow
                  icon={FEED_ICON[e.kind]}
                  title={e.text}
                  time={<time dateTime={e.at}>{TIME.format(new Date(e.at))}</time>}
                />
              </li>
            ))}
          </EventList>
        </Section>
      ))}
    </section>
  );
}
