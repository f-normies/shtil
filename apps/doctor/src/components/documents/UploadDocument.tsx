import { $api } from "@compass/api-client";
import { Alert, Button } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Info, LoaderCircle, Plus } from "lucide-react";
import { useRef, useState } from "react";
import { ACCEPT, LIMITS_HINT, uploadErrorText } from "../../card/documents";
import { documentForm, PATIENT_DOCUMENTS, refreshDocuments } from "../../card/queries";

/**
 * «Загрузить историю болезни» (кадр 07в): выбор одного файла, подсказка с ограничениями
 * и причина отказа по коду ответа. Тот же файл возвращает прежний документ (200).
 */
export function UploadDocument({
  patientId,
  onUploaded,
}: {
  patientId: string;
  onUploaded: (documentId: string) => void;
}) {
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [filename, setFilename] = useState("");
  const upload = $api.useMutation("post", PATIENT_DOCUMENTS, {
    onSuccess: async (document) => {
      onUploaded(document.id);
      await refreshDocuments(queryClient);
    },
  });

  const send = (file: File) => {
    setFilename(file.name);
    upload.mutate({ params: { path: { patient_id: patientId } }, body: documentForm(file) });
  };

  return (
    <div className="flex flex-col gap-2 px-4">
      <div>
        <Button
          variant="secondary"
          disabled={upload.isPending}
          aria-describedby="upload-hint"
          onClick={() => input.current?.click()}
        >
          {upload.isPending ? (
            <LoaderCircle aria-hidden className="size-5 motion-safe:animate-spin" />
          ) : (
            <Plus aria-hidden className="size-5" />
          )}
          {upload.isPending ? "Загружается…" : "Загрузить историю болезни"}
        </Button>
        <input
          ref={input}
          type="file"
          accept={ACCEPT}
          className="sr-only"
          tabIndex={-1}
          aria-hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            // Сброс: тот же файл можно выбрать ещё раз после ошибки.
            e.target.value = "";
            if (file) {
              send(file);
            }
          }}
        />
      </div>
      <p
        id="upload-hint"
        className="flex items-center gap-2 text-[13px] leading-[18px] text-ink-muted"
      >
        <Info aria-hidden className="size-5 shrink-0" />
        {LIMITS_HINT}
      </p>
      {upload.isError && (
        <Alert tone="error">
          {uploadErrorText(upload.error)}. Файл «{filename}» не загружен.
        </Alert>
      )}
    </div>
  );
}
