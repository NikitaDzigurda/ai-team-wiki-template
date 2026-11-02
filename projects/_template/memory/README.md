# memory/

Один факт — один файл `<slug>.md`. Создание: `wiki.py new-memory <slug> -t <type> -d "<описание>"`.

```markdown
---
name: <slug, совпадает с именем файла>
description: <одна строка: по ней агент решает, открывать ли файл>
type: fact | feedback | reference | context
created: {{DATE}}
updated: {{DATE}}
---

<Факт.>

**Почему:** <источник / причина>

**Как применять:** <что делать в следующий раз>
```

Подробно: `standards/memory/memory-format.md`.
