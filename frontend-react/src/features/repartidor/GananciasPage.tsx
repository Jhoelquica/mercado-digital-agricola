import { MisGanancias } from '../ganancias/MisGanancias';

// Lo que gana el repartidor y cómo cobra: ChakraShop le paga cada lunes lo entregado hasta el domingo.
export default function GananciasPage() {
  return (
    <div className="repartidor-shell">
      <section className="ganancias-seccion" aria-labelledby="repartidor-ganancias-titulo">
        <div className="section-header inner">
          <h3 id="repartidor-ganancias-titulo"><i className="ti ti-wallet" /> Mis ganancias</h3>
        </div>
        <MisGanancias id="repartidor-ganancias" />
      </section>
    </div>
  );
}
