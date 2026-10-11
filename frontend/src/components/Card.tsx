// The white card used on every tab: bold title, optional count and subtitle, optional action on the right.
export default function Card({ title, count, sub, right, className = "", children }: any) {
  return (
    <section className={`card ${className}`}>
      <div className="card-head titled">
        <div className="card-titles">
          <div className="card-title-row">
            <h2>{title}</h2>
            {count !== undefined && <span className="count">{count}</span>}
          </div>
          {sub && <p>{sub}</p>}
        </div>
        {right && <div className="push">{right}</div>}
      </div>
      {children}
    </section>
  );
}
