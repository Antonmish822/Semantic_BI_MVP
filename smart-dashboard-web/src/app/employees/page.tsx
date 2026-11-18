export default function EmployeesPage() {
  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            P
          </div>
          <div>
            <p className="eyebrow">SEMANTIC BI · BITRIX24</p>
            <h1>CRM Pulse</h1>
          </div>
        </div>
        <div className="header-actions">
          <label className="team-filter global-filter">
            <span>Категория портфеля</span>
            <select className="project-filter" aria-label="Фильтр портфеля по категории" defaultValue="all">
              <option value="all">Все категории</option>
              <option value="2">Категория 2</option>
              <option value="3">Категория 3</option>
            </select>
          </label>
          <span className="connection online">
            <i></i>
            <span>GraphDB подключён</span>
          </span>
          <span className="updated">Обновлено 18.11.2026 20:14</span>
          <button className="refresh" type="button" aria-label="Обновить данные" title="Обновить данные">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M20 7v5h-5M4 17v-5h5" />
              <path d="M5.6 9a7 7 0 0 1 11.8-2L20 12M4 12l2.6 5a7 7 0 0 0 11.8-2" />
            </svg>
            <span>Обновить</span>
          </button>
        </div>
      </header>

      <nav className="page-nav" aria-label="Страницы дашборда">
        <a href="/">Пульс сделок</a>
        <a href="/leads">Пульс лидов</a>
        <a className="active" href="/employees">
          Сотрудники
        </a>
      </nav>

      <section className="view-state" aria-label="Активные ситуации">
        <div className="view-state-card">
          <strong>Активные ситуации</strong>
          <div className="badge">
            <span className="badge-dot" aria-hidden="true"></span>
            Нагрузка на сотрудников
          </div>
        </div>
        <div className="view-state-card">
          <strong>Рекомендации вида</strong>
          <div>Поднять таблицу сотрудников</div>
          <div>Активировать фильтр «категория портфеля»</div>
        </div>
      </section>

      <section className="panel" aria-labelledby="employee-pulse-title">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">ЭФФЕКТИВНОСТЬ КОМАНДЫ</p>
            <h2 id="employee-pulse-title">Пульс сотрудников</h2>
            <p className="panel-subtitle">Средняя реакция на лид и время конвертации · скользящее окно 30 дней · последние 90 дней</p>
          </div>
          <label className="team-filter">
            <span>Сотрудник</span>
            <select className="project-filter" aria-label="Фильтр графика по сотруднику" defaultValue="all">
              <option value="all">Все сотрудники</option>
              <option value="lead-1">Алексей Семёнов</option>
              <option value="lead-2">Мария Громова</option>
            </select>
          </label>
        </div>
        <div className="employee-pulse-legend">
          <span>
            <i className="legend-response"></i>Средняя скорость реакции
          </span>
          <span>
            <i className="legend-conversion"></i>Среднее время конвертации
          </span>
        </div>
        <div className="empty-state">Недостаточно данных по выбранному сотруднику.</div>
      </section>

      <section className="panel team-panel" aria-labelledby="team-title">
        <div className="panel-heading team-heading">
          <div>
            <p className="eyebrow">НАГРУЗКА И РЕАКЦИЯ</p>
            <h2 id="team-title">20 сотрудников с наибольшим портфелем</h2>
            <p className="panel-subtitle">Активности считаются по ответственному CRM-дела; сделки — по назначенному сотруднику.</p>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Сотрудник</th>
                <th className="number">Сделки P</th>
                <th className="number">Активности · 24 ч</th>
                <th className="number">7 дн.</th>
                <th className="number">30 дн.</th>
                <th className="number">Сделки без активности · 7 дн.</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="employee-name">
                  Алексей Семёнов
                  <span className="employee-uri">employee-101</span>
                </td>
                <td className="number">38</td>
                <td className="number">24</td>
                <td className="number">108</td>
                <td className="number">420</td>
                <td className="number">6</td>
              </tr>
              <tr>
                <td className="employee-name">
                  Мария Громова
                  <span className="employee-uri">employee-091</span>
                </td>
                <td className="number">32</td>
                <td className="number">19</td>
                <td className="number">95</td>
                <td className="number">360</td>
                <td className="number">4</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <footer>
        <span>Только чтение · GraphDB · bitrix24_v2_parallel</span>
        <span>Пульс рассчитывается по ответственному лида (assignedTo).</span>
      </footer>
    </main>
  );
}
