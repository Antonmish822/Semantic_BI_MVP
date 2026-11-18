export default function DealsPulsePage() {
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
            <span>Категория</span>
            <select className="project-filter" aria-label="Фильтр дашборда по категории" defaultValue="all">
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
        <a className="active" href="/">
          Пульс сделок
        </a>
        <a href="/leads">Пульс лидов</a>
        <a href="/employees">Сотрудники</a>
      </nav>

      <section className="view-state" aria-label="Активные ситуации">
        <div className="view-state-card">
          <strong>Активные ситуации</strong>
          <div className="badge">
            <span className="badge-dot" aria-hidden="true"></span>
            Всплеск красных сделок
          </div>
        </div>
        <div className="view-state-card">
          <strong>Рекомендации вида</strong>
          <div>Поднять блок «Красные сделки»</div>
          <div>Активировать фильтр категории</div>
        </div>
      </section>

      <section aria-labelledby="pulse-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">ТЕКУЩАЯ ВОРОНКА</p>
            <h2 id="pulse-title">
              Активные сделки <span className="stage-pill">P · В работе</span>
            </h2>
          </div>
          <p className="section-note">Срез обновляется автоматически · все интервалы считаются от текущего времени</p>
        </div>
        <div className="metric-grid">
          <article className="metric-card metric-primary">
            <div className="metric-label">Всего активных</div>
            <div className="metric-value">218</div>
            <div className="metric-foot">Семантика стадии P</div>
          </article>
          <article className="metric-card">
            <div className="metric-label">Создано за 24 часа</div>
            <div className="metric-value">9</div>
            <div className="metric-foot">Новые сделки в работе</div>
          </article>
          <article className="metric-card">
            <div className="metric-label">Создано за 7 дней</div>
            <div className="metric-value">68</div>
            <div className="metric-foot">Новые сделки в работе</div>
          </article>
          <article className="metric-card">
            <div className="metric-label">Создано за 30 дней</div>
            <div className="metric-value">210</div>
            <div className="metric-foot">Новые сделки в работе</div>
          </article>
          <article className="metric-card metric-danger">
            <div className="metric-label">Без активности 7 дней</div>
            <div className="metric-value">18</div>
            <div className="metric-foot">Требуют внимания менеджера</div>
          </article>
        </div>
      </section>

      <section className="panel" aria-labelledby="activity-distribution-title">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">ДАВНОСТЬ АКТИВНОСТИ</p>
            <h2 id="activity-distribution-title">Распределение активных сделок по последней активности</h2>
            <p className="panel-subtitle">Интервалы по 5 дней · сделки стадии P</p>
          </div>
          <label className="team-filter">
            <span>Категория графика</span>
            <select className="project-filter" aria-label="Фильтр графика по категории" defaultValue="all">
              <option value="all">Все категории</option>
              <option value="2">Категория 2</option>
              <option value="3">Категория 3</option>
            </select>
          </label>
        </div>
        <div className="empty-state">Загружаю распределение…</div>
      </section>

      <section className="panel red-panel" aria-labelledby="red-title">
        <div className="panel-heading">
          <div>
            <p className="eyebrow danger-eyebrow">ТРЕБУЮТ ВНИМАНИЯ</p>
            <h2 id="red-title">Красные сделки</h2>
            <p className="panel-subtitle">Владелец-сделка · нет записи активности за последние 7 дней</p>
          </div>
          <div className="red-controls">
            <button className="secondary-button" type="button">
              <span>Показать список</span>
              <span className="count-badge">18</span>
            </button>
          </div>
        </div>
        <div className="red-content">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Сделка</th>
                  <th>Ответственный</th>
                  <th>Создана</th>
                  <th>Последняя активность в карточке</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>
                    <span className="red-title">Контракт Q2</span>
                    <span className="deal-uri">deal-2981</span>
                  </td>
                  <td>Иван Петров</td>
                  <td>05.11.2026 11:20</td>
                  <td className="muted-cell">08.11.2026 15:40</td>
                </tr>
                <tr>
                  <td>
                    <span className="red-title">Расширение CRM</span>
                    <span className="deal-uri">deal-3022</span>
                  </td>
                  <td>Анна Козлова</td>
                  <td>06.11.2026 09:12</td>
                  <td className="muted-cell">07.11.2026 17:05</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="panel activities-panel" aria-labelledby="activities-title">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">ПОСЛЕДНИЕ СОБЫТИЯ</p>
            <h2 id="activities-title">20 последних активностей по сделкам</h2>
            <p className="panel-subtitle">Тема активности, семантический класс и название сделки, ответственный сотрудник.</p>
          </div>
          <button className="secondary-button" type="button">
            <span>Показать список</span>
            <span className="count-badge">20</span>
          </button>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Дата</th>
                <th>Содержание активности</th>
                <th>Сделка</th>
                <th>Сотрудник</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>18.11.2026 18:05</td>
                <td>
                  <span className="activity-subject">Звонок клиенту</span>
                  <span className="activity-uri">activity-1201</span>
                </td>
                <td>
                  <span className="deal-class">В процессе</span>
                  <span className="activity-deal-title">Перезапуск интеграции</span>
                  <span className="activity-uri">deal-3045</span>
                </td>
                <td>
                  <span className="activity-employee">Мария Громова</span>
                  <span className="activity-uri">employee-91</span>
                </td>
              </tr>
              <tr>
                <td>18.11.2026 16:45</td>
                <td>
                  <span className="activity-subject">Письмо с КП</span>
                  <span className="activity-uri">activity-1199</span>
                </td>
                <td>
                  <span className="deal-class">Активная</span>
                  <span className="activity-deal-title">Крупный ретейл</span>
                  <span className="activity-uri">deal-3030</span>
                </td>
                <td>
                  <span className="activity-employee">Сергей Литвин</span>
                  <span className="activity-uri">employee-74</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <footer>
        <span>Только чтение · GraphDB · bitrix24_v2_parallel</span>
        <span>Активность считается по дате создания записи CRM Activity.</span>
      </footer>
    </main>
  );
}
