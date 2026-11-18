# Smart Dashboard Web

Фронтенд умного CRM-дашборда (Next.js + TypeScript). Вёрстка повторяет визуал текущих статических страниц и содержит блоки для будущей интеграции `view_state`.

## Требования

- Node.js 20+
- npm 10+

## Установка

```bash
cd smart-dashboard-web
npm install
```

## Запуск в режиме разработки

```bash
npm run dev
```

Откройте [http://localhost:3000](http://localhost:3000).

## Страницы

- `/` — Пульс сделок
- `/leads` — Пульс лидов
- `/employees` — Сотрудники

## Примечания по интеграции

- Блок **"Активные ситуации"** вверху каждой страницы — визуальный плейсхолдер для ответа `GET /api/view-state`.
- Сценарии и эффекты описаны в `plans/smart_dashboard_architecture_and_roadmap.md`.
