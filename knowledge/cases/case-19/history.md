```
export_format: mis-encounter-v2
encounter_id: SYN-2026-019
encounter_date: 2026-10-09
department: gastroenterology
patient:
  sex: M
  age: 56
  employment: "водитель, на больничном с августа"
complaints: "слабость, плохое настроение около 2 месяцев, ничего не радует, засыпает к 3 часам ночи"
anamnesis_morbi: "Цирроз печени установлен в 2025 г. Злоупотреблял алкоголем около 15 лет, до 0,5 л водки в день. Не пьёт 8 месяцев, с февраля 2026 г. Гипотиреоз с 2022 г., принимает левотироксин. Варикозное расширение вен пищевода 1 степени по ЭГДС от 05.2026."
social: "разведён в 2025 г., живёт один"
family_history: "брат лечился от алкогольной зависимости"
labs:
  bilirubin_total: "24 мкмоль/л"
  albumin: "38 г/л"
  inr: "1.2"
  alt: "46 Ед/л"
  ast: "58 Ед/л"
  tsh: "6.8 мМЕ/л"
  creatinine: "82 мкмоль/л"
child_pugh: "A, 5 баллов"
ultrasound: "асцита нет"
scales:
  phq9_total: 13
  phq9_item9: 0
suicidal_ideation: "отрицает"
diagnoses:
  - {code: K70.3, name: "Алкогольный цирроз печени", class: "Child-Pugh A", role: primary}
  - {code: E03.9, name: "Гипотиреоз неуточнённый", role: secondary}
  - {code: F32.1, name: "Депрессивный эпизод средней степени", role: secondary, note: "предварительно, консультация психиатра"}
prescriptions:
  - {drug: "Пропранолол", dose: "20 мг", frequency: "2 раза в день", status: active}
  - {drug: "Левотироксин натрия", dose: "75 мкг", frequency: "утром натощак за 30 минут до еды", status: active, note: "доза увеличена с 50 мкг"}
  - {drug: "Урсодезоксихолевая кислота", dose: "500 мг", frequency: "на ночь", status: discontinued, stop_date: 2026-09-01}
referrals: ["психиатр", "эндокринолог"]
```
