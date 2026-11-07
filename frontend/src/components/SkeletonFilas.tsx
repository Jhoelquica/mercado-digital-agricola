// Filas grises mientras carga una lista (renderSkeletonFilas del frontend anterior).
export function SkeletonFilas({ cantidad = 3 }: { cantidad?: number }) {
  return (
    <>
      {Array.from({ length: cantidad }, (_, i) => (
        <div className="skeleton-row-card" key={i}>
          <div className="skeleton skeleton-line w-40" />
          <div className="skeleton skeleton-line tall w-70" />
          <div className="skeleton skeleton-line w-60" />
        </div>
      ))}
    </>
  );
}
