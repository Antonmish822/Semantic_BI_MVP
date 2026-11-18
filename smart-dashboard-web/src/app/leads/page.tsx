export default function LeadsPulsePage() {
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
        <a className="active" href="/leads">
          Пульс лидов
        </a>
        <a href="/employees">Сотрудники</a>
      </nav>

      <section className="view-state" aria-label="Активные ситуации">
        <div className="view-state-card">
          <strong>Активные ситуации</strong>
          <div className="badge">
            <span className="badge-dot" aria-hidden="true"></span>
            Рост времени реакции на лид
          </div>
        </div>
        <div className="view-state-card">
          <strong>Рекомендации вида</strong>
          <div>Поднять «Пульс активностей по лидам»</div>
          <div>Активировать фильтр «ответственный»</div>
        </div>
      </section>

      <section aria-labelledby="leads-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">ВХОДЯЩИЙ ПОТОК</p>
            <h2 id="leads-title">Пульс лидов</h2>
          </div>
          <p className="section-note">Даты и интервалы считаются по текущему времени</p>
        </div>
        <div className="metric-grid lead-metric-grid">
          <article className="metric-card metric-primary">
            <div className="metric-label">Поступило за 1 день</div>
            <div className="metric-value">56</div>
            <div className="metric-foot">По дате создания лида</div>
          </article>
          <article className="metric-card">
            <div className="metric-label">Поступило за 7 дней</div>
            <div className="metric-value">392</div>
            <div className="metric-foot">По дате создания лида</div>
          </article>
          <article className="metric-card">
            <div className="metric-label">Поступило за 30 дней</div>
            <div className="metric-value">1204</div>
            <div className="metric-foot">По дате создания лида</div>
          </article>
          <article className="metric-card">
            <div className="metric-label">Средняя скорость реакции</div>
            <div className="metric-value metric-value-text">1 ч 42 мин</div>
            <div className="metric-foot">Время до первой активности · лиды за 30 дней</div>
          </article>
          <article className="metric-card metric-primary">
            <div className="metric-label">Среднее время до сделки</div>
            <div className="metric-value metric-value-text">4.6 дн</div>
            <div className="metric-foot">Лид → сделка на стадии P</div>
          </article>
        </div>
      </section>

      <section className="panel" aria-labelledby="red-leads-title">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">КОНТРОЛЬ РЕАКЦИИ</p>
            <h2 id="red-leads-title">Красные лиды</h2>
            <p className="panel-subtitle">10 последних лидов, у которых первая активность наступила позже чем через 2 часа после создания или отсутствует.</p>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Создан</th>
                <th>Лид</th>
                <th>Источник</th>
                <th>Ответственный</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>18.11.2026 09:20</td>
                <td className="employee-name">Запрос на внедрение</td>
                <td>
                  <span className="lead-meta-line">Источник: Organic</span>
                </td>
                <td>Ольга Морозова</td>
              </tr>
              <tr>
                <td>18.11.2026 08:10</td>
                <td className="employee-name">Консультация по API</td>
                <td>
                  <span className="lead-meta-line">Источник: Bitrix24 (form)</span>
                </td>
                <td>Илья Давыдов</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel" aria-labelledby="conversion-trend-title">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">СКОРОСТЬ КОНВЕРСИИ</p>
            <h2 id="conversion-trend-title">Пульс конвертации лида в сделку</h2>
            <p className="panel-subtitle">Среднее время до связанной сделки P · скользящее окно 30 дней · последние 90 дней</p>
          </div>
          <label className="team-filter">
            <span>Воронка</span>
            <select className="project-filter" aria-label="Фильтр графика по воронке" defaultValue="all">
              <option value="all">Все воронки</option>
              <option value="2">Воронка 2</option>
              <option value="3">Воронка 3</option>
            </select>
          </label>
        </div>
        <div className="empty-state">Нет данных о конверсиях в выбранной воронке.</div>
      </section>

      <section className="panel" aria-labelledby="response-trend-title">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">СКОРОСТЬ ОБРАБОТКИ</p>
            <h2 id="response-trend-title">Пульс активностей по лидам</h2>
            <p className="panel-subtitle">Среднее время до первой активности · скользящее окно 30 дней · последние 90 дней</p>
          </div>
        </div>
        <div className="empty-state">Недостаточно данных для графика.</div>
      </section>

      <footer>
        <span>Только чтение · GraphDB · bitrix24_v2_parallel</span>
        <span>Конверсия определяется по originLead связанной сделки со stageSemantic = P.</span>
      </footer>
    </main>
  );
}
