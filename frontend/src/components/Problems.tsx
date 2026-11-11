export function Problems({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="problems" role="alert">
      {items.map((item) => (
        <p key={item}>{item}</p>
      ))}
    </div>
  );
}
