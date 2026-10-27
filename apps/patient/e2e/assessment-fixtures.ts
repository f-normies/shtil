import type { Schemas } from "@compass/api-client";

export const PATIENT_A = "00000000-0000-4000-8000-0000000000a1";
export const PATIENT_B = "00000000-0000-4000-8000-0000000000b1";
export const SCALE_ID = "synthetic-scale";
export const SUBMISSION_A = "00000000-0000-4000-8000-000000000011";
export const SUBMISSION_B = "00000000-0000-4000-8000-000000000012";

const VALIDATION_REF = { source: "synthetic-validation", locator: "section 1" };

export const DEFINITION_V1 = {
  scale: {
    id: SCALE_ID,
    title: "Synthetic scale",
    instruction: "Synthetic test instruction",
    version: 1,
    purpose: "monitoring",
    respondent: "patient",
    status: "reviewed",
    items: [{ id: "q1", text: "Synthetic question", options: [{ value: 0, text: "Never" }] }],
    license: { terms: "Synthetic test license", allowed: true, source: VALIDATION_REF },
    scores: [
      {
        id: "total",
        items: ["q1"],
        bands: [{ min: 0, max: 0, code: "low", label: "Synthetic band", source: VALIDATION_REF }],
      },
    ],
    validation: [VALIDATION_REF],
  },
  sources: [
    {
      id: "synthetic-validation",
      title: "Synthetic validation source",
      url: "https://example.invalid/validation",
      supplementary: true,
    },
  ],
} satisfies Schemas["ScaleDefinitionOut"];

export const DEFINITION_V2 = {
  ...DEFINITION_V1,
  scale: {
    ...DEFINITION_V1.scale,
    version: 2,
    items: [{ id: "q2", text: "New synthetic question", options: [{ value: 1, text: "Often" }] }],
  },
} satisfies Schemas["ScaleDefinitionOut"];

export const DEFINITION_TWO_ITEMS = {
  ...DEFINITION_V1,
  scale: {
    ...DEFINITION_V1.scale,
    items: [
      ...DEFINITION_V1.scale.items,
      { id: "q2", text: "Synthetic second question", options: [{ value: 1, text: "Often" }] },
    ],
    scores: [
      {
        id: "total",
        items: ["q1", "q2"],
        bands: [
          {
            min: 0,
            max: 1,
            code: "low",
            label: "Synthetic band",
            source: VALIDATION_REF,
          },
        ],
      },
    ],
  },
} satisfies Schemas["ScaleDefinitionOut"];

export const DEFINITION_FORM = {
  ...DEFINITION_V1,
  scale: {
    ...DEFINITION_V1.scale,
    title: "Synthetic wellbeing check",
    instruction: "Choose the answer that best describes your recent experience.",
    items: [
      {
        id: "calm",
        text: "I felt calm.",
        options: [
          { value: 0, text: "Never" },
          { value: 2, text: "Sometimes" },
          { value: 5, text: "Often" },
        ],
      },
      {
        id: "rest",
        text: "I could rest.",
        options: [
          { value: 1, text: "Never" },
          { value: 3, text: "Sometimes" },
          { value: 4, text: "Often" },
        ],
      },
    ],
    scores: [
      {
        id: "calmness",
        items: ["calm"],
        bands: [
          {
            min: 0,
            max: 5,
            code: "synthetic",
            label: "Synthetic calmness band",
            source: VALIDATION_REF,
          },
        ],
      },
      {
        id: "rest",
        items: ["rest"],
        bands: [
          {
            min: 1,
            max: 4,
            code: "synthetic",
            label: "Synthetic rest band",
            source: VALIDATION_REF,
          },
        ],
      },
    ],
  },
} satisfies Schemas["ScaleDefinitionOut"];

export function payload(
  patientId = PATIENT_A,
  submissionId = SUBMISSION_A,
  answers: Record<string, number> = { q1: 0 },
): Schemas["AssessmentCreateIn"] {
  return {
    patient_id: patientId,
    submission_id: submissionId,
    scale_id: SCALE_ID,
    scale_version: 1,
    completed_at: "2026-10-17T10:00:00.000Z",
    answers,
  };
}
