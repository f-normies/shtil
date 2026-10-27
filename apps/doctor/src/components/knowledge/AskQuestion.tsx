import { Button } from "@compass/ui";
import { Search } from "lucide-react";
import { useId } from "react";
import { QUESTION_TEXT } from "../../card/question";
import { useInspector } from "../inspector/context";
import { QuestionPanel } from "./QuestionPanel";

/** Вопрос уже открыт: повторный клик не стирает его, а переводит фокус в поле. */
const QUESTION_ID = "knowledge-question";

/**
 * «Спросить по КР» под «Вариантами по клиническим рекомендациям» (спека #108, 3.3; кадр
 * «Врач v4 · 10в»): открывает вопрос к базе знаний в инспекторе справа. Данные пациента
 * в вопрос не подставляются.
 */
export function AskQuestion() {
  const { open } = useInspector();
  const hintId = useId();
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4">
      <Button
        variant="secondary"
        icon={<Search className="size-5" strokeWidth={1.75} />}
        aria-describedby={hintId}
        onClick={(event) =>
          open(
            { id: QUESTION_ID, title: QUESTION_TEXT.title, body: <QuestionPanel /> },
            event.currentTarget,
          )
        }
      >
        Спросить по КР
      </Button>
      <p id={hintId} className="min-w-0 flex-1 text-[13px] leading-[18px] text-ink-muted">
        Свой вопрос по клиническим рекомендациям и инструкциям
      </p>
    </div>
  );
}
