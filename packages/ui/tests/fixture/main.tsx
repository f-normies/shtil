import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ComponentType, type ReactNode, useState } from "react";
import { createRoot } from "react-dom/client";
import * as UI from "../../src";
import "../../src/styles.css";
import "./fixture.css";
import { ChatFixture } from "./chat-fixture";
import { DynamicImageIconCase, VisualReviewCase, VisualSourceCase } from "./visual-review";

type LooseComponent = ComponentType<Record<string, unknown> & { children?: ReactNode }>;
const components = UI as unknown as Record<string, LooseComponent | undefined>;

function Missing({ name }: { name: string }) {
  return <p role="status">Компонент «{name}» пока не подключён</p>;
}

function PasswordCase() {
  const [submits, setSubmits] = useState(0);
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        setSubmits((count) => count + 1);
      }}
    >
      <UI.TextField label="Пароль" name="password" type="password" defaultValue="secret123" />
      <output data-testid="submit-count">{submits}</output>
    </form>
  );
}

function ButtonCase() {
  const [submits, setSubmits] = useState(0);
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        setSubmits((count) => count + 1);
      }}
    >
      <UI.Button type="submit" loading onClick={() => setSubmits((count) => count + 1)}>
        Сохранить
      </UI.Button>
      <UI.Button type="button" disabled onClick={() => setSubmits((count) => count + 1)}>
        Отключено
      </UI.Button>
      <output data-testid="submit-count">{submits}</output>
    </form>
  );
}

function SegmentedCase() {
  const [value, setValue] = useState("week");
  const SegmentedControl = components.SegmentedControl;
  if (!SegmentedControl) return <Missing name="Сегментированный переключатель" />;

  return (
    <>
      <SegmentedControl
        ariaLabel="Период"
        value={value}
        onValueChange={setValue}
        options={[
          { value: "week", label: "Неделя" },
          { value: "month", label: "Месяц" },
          { value: "disabled", label: "Недоступно", disabled: true },
          { value: "all", label: "Всё время" },
        ]}
      />
      <output data-testid="selected-segment">{value}</output>
    </>
  );
}

function PopoverCase() {
  const Popover = components.Popover;
  if (!Popover) return <Missing name="Поповер" />;

  return (
    <>
      <button type="button" data-testid="outside" style={{ position: "fixed", left: 8, top: 8 }}>
        Действие снаружи
      </button>
      <div style={{ position: "fixed", right: 8, bottom: 8 }}>
        <Popover trigger={<span>Подробности</span>} label="Пояснение">
          <button type="button">Действие внутри</button>
        </Popover>
      </div>
    </>
  );
}

function LongPopoverContent() {
  const lines = Array.from(
    { length: 24 },
    (_, index) => `Строка содержимого прокручиваемой подсказки ${index + 1}.`,
  );

  return (
    <div style={{ width: 260 }}>
      {lines.map((line) => (
        <p key={line}>{line}</p>
      ))}
    </div>
  );
}

function ScrollPopoverCase() {
  const Popover = components.Popover;
  if (!Popover) return <Missing name="Поповер" />;

  return (
    <main className="ui-fixture ui-fixture--scroll-popover">
      <div style={{ position: "fixed", left: 40, top: 40 }}>
        <Popover
          trigger={<span>Короткий поповер снизу</span>}
          label="Короткая подсказка снизу"
          placement="bottom"
        >
          <p>Короткая подсказка с направленной границей.</p>
        </Popover>
      </div>
      <div style={{ position: "fixed", left: 40, bottom: 40 }}>
        <Popover
          trigger={<span>Короткий поповер сверху</span>}
          label="Короткая подсказка сверху"
          placement="top"
        >
          <p>Короткая подсказка с направленной границей.</p>
        </Popover>
      </div>
      <div style={{ position: "fixed", left: "55%", top: 96 }}>
        <Popover
          trigger={<span>Длинный поповер</span>}
          label="Прокручиваемая подсказка"
          placement="bottom"
        >
          <LongPopoverContent />
        </Popover>
      </div>
      <div style={{ position: "fixed", left: "55%", top: 16 }}>
        <Popover
          trigger={<span>Длинный поповер у верхнего края</span>}
          label="Длинная подсказка у верхнего края"
          placement="top"
        >
          <LongPopoverContent />
        </Popover>
      </div>
      <div style={{ position: "fixed", left: "55%", bottom: 16 }}>
        <Popover
          trigger={<span>Длинный поповер у нижнего края</span>}
          label="Длинная подсказка у нижнего края"
          placement="bottom"
        >
          <LongPopoverContent />
        </Popover>
      </div>
    </main>
  );
}

function LoginCase() {
  return (
    <main className="ui-fixture">
      <UI.LoginForm onSuccess={() => undefined} />
    </main>
  );
}

function Showcase() {
  const [segment, setSegment] = useState("all");
  const SegmentedControl = components.SegmentedControl;
  const Popover = components.Popover;
  const StatusBadge = components.StatusBadge;
  const Counter = components.Counter;
  const Avatar = components.Avatar;
  const Brand = components.Brand;
  const BrandMark = components.BrandMark;

  return (
    <main className="ui-fixture">
      <h1 className="type-doctor-title">Общие компоненты</h1>
      <section aria-label="Кнопки" className="ui-fixture__section">
        <UI.Button variant="primary">Основная</UI.Button>
        <UI.Button variant="primary" aria-label="Непрочитанные сообщения" count={4}>
          Сообщения
        </UI.Button>
        <UI.Button variant="secondary">Обычная</UI.Button>
        <UI.Button variant="quiet">Тихая</UI.Button>
        <UI.Button variant="link">Ссылка</UI.Button>
        <UI.Button variant="crisis">Экстренная помощь</UI.Button>
        <UI.Button variant="soft">Мягкая</UI.Button>
        <UI.Button variant="primary" loading>
          Ожидание
        </UI.Button>
      </section>
      <section aria-label="Поля" className="ui-fixture__section">
        <UI.TextField label="Электронная почта" hint="Рабочий адрес из приглашения" />
        <UI.TextField label="Пароль" type="password" defaultValue="secret123" />
        <UI.TextField label="Причина" multiline rows={3} />
        <UI.TextField label="Причина с ошибкой" multiline rows={3} error="Проверьте описание" />
        <UI.TextField label="Ошибка" error="Проверьте введённое значение" />
      </section>
      <section aria-label="Статусы и счётчики" className="ui-fixture__section">
        {StatusBadge ? <StatusBadge status="required" /> : <Missing name="Статус" />}
        {StatusBadge ? <StatusBadge status="advised" /> : null}
        {StatusBadge ? <StatusBadge status="proposed" /> : null}
        {StatusBadge ? <StatusBadge status="confirmed" /> : null}
        {Counter ? (
          <Counter value={4} aria-label="Четыре сигнала" tone="required" />
        ) : (
          <Missing name="Счётчик" />
        )}
        {Counter ? <Counter value={2} aria-label="Два приглашения" /> : null}
      </section>
      <section aria-label="Сегменты и аватары" className="ui-fixture__section">
        {SegmentedControl ? (
          <SegmentedControl
            ariaLabel="Фильтр списка"
            value={segment}
            onValueChange={setSegment}
            options={[
              { value: "all", label: "Все" },
              { value: "alerts", label: "С сигналами" },
              { value: "invited", label: "Приглашённые" },
            ]}
          />
        ) : (
          <Missing name="Сегментированный переключатель" />
        )}
        {Avatar ? <Avatar initials="АК" size={24} /> : <Missing name="Аватар" />}
        {Avatar ? <Avatar initials="АК" size={36} /> : null}
        {Avatar ? <Avatar initials="АК" size={48} /> : null}
        {Avatar ? <Avatar initials="АК" size={64} /> : null}
      </section>
      <section aria-label="Знак и бренд" className="ui-fixture__section">
        {Brand ? <Brand persona="doctor" /> : <Missing name="Бренд" />}
        {BrandMark ? <BrandMark persona="patient" style={{ height: 48 }} /> : null}
      </section>
      <section aria-label="Поповер" className="ui-fixture__section">
        {Popover ? (
          <Popover trigger={<span>Открыть подсказку</span>} label="Подсказка">
            <button type="button">Открыть источник</button>
          </Popover>
        ) : (
          <Missing name="Поповер" />
        )}
      </section>
    </main>
  );
}

function App() {
  const params = new URLSearchParams(window.location.search);
  const forma = params.get("forma") ?? "vrach";
  const tsvet = params.get("tsvet") ?? (forma === "patsient" ? "patsient-prokhladnaya" : "vrach");
  const nastroenie = params.get("nastroenie") ?? "teplyy-khorosho";
  document.documentElement.dataset.forma = forma;
  document.documentElement.dataset.tsvet = tsvet;
  document.documentElement.dataset.nastroenie = nastroenie;

  switch (params.get("case")) {
    case "chat":
      return <ChatFixture />;
    case "password":
      return <PasswordCase />;
    case "button":
      return <ButtonCase />;
    case "segments":
      return <SegmentedCase />;
    case "popover":
      return <PopoverCase />;
    case "scroll-popover":
      return <ScrollPopoverCase />;
    case "login":
      return <LoginCase />;
    case "visual-review":
      return <VisualReviewCase />;
    case "dynamic-icon":
      return <DynamicImageIconCase />;
    case "visual-source":
      return <VisualSourceCase source={params.get("source") ?? ""} />;
    default:
      return <Showcase />;
  }
}

const root = document.getElementById("root");
if (!root) throw new Error("Fixture root element is missing");
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
});
createRoot(root).render(
  <QueryClientProvider client={queryClient}>
    <App />
  </QueryClientProvider>,
);
