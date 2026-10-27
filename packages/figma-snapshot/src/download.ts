/**
 * Скачивание временных адресов Figma MCP.
 *
 * Скриншот большого кадра Figma иногда отдаёт ответом 200 с пустым телом, пока рендер не
 * готов: такой ответ и сетевой сбой повторяем с паузой. Ответ 4xx значит, что адрес истёк
 * или неверен, повторять его бесполезно.
 *
 * Имена файлов (`figma.png`, `assets/img3.svg`) от обновления к обновлению те же, а
 * содержимое меняется. Поэтому файл не перекачиваем, только если журнал скачиваний
 * (`ledger`: путь → адрес) помнит для него тот же адрес.
 */
import { stat, writeFile } from "node:fs/promises";

const ATTEMPTS = 5;
const DELAY_MS = 3_000;
const TIMEOUT_MS = 60_000;

export interface DownloadOptions {
  readonly fetch?: typeof fetch;
  readonly delayMs?: number;
  readonly timeoutMs?: number;
  /** Путь файла → адрес, с которого он скачан. Обновляется после скачивания. */
  readonly ledger?: Map<string, string>;
}

async function hasContent(file: string): Promise<boolean> {
  try {
    return (await stat(file)).size > 0;
  } catch {
    return false;
  }
}

/** Скачивает `url` в `target`, если там нет непустого файла с этого же адреса. */
export async function download(
  url: string,
  target: string,
  options: DownloadOptions = {},
): Promise<void> {
  const ledger = options.ledger ?? new Map<string, string>();
  if (ledger.get(target) === url && (await hasContent(target))) return;
  const get = options.fetch ?? fetch;
  const delayMs = options.delayMs ?? DELAY_MS;
  let problem = "";
  for (let attempt = 1; attempt <= ATTEMPTS; attempt += 1) {
    if (attempt > 1) await new Promise((resolve) => setTimeout(resolve, delayMs));
    let response: Response;
    try {
      response = await get(url, { signal: AbortSignal.timeout(options.timeoutMs ?? TIMEOUT_MS) });
    } catch (error) {
      problem = `сбой сети: ${String(error)}`;
      continue;
    }
    if (response.status >= 400 && response.status < 500) {
      throw new Error(`ответ ${response.status}, адрес истёк или неверен: ${url} → ${target}`);
    }
    const body = response.ok ? Buffer.from(await response.arrayBuffer()) : undefined;
    if (body && body.length > 0) {
      await writeFile(target, body);
      ledger.set(target, url);
      return;
    }
    problem = response.ok ? "пустой ответ" : `ответ ${response.status}`;
  }
  throw new Error(`${problem} после ${ATTEMPTS} попыток: ${url} → ${target}`);
}
