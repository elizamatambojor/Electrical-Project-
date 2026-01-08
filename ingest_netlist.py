#!/usr/bin/env python3

# Universidad de Costa Rica 
# Escuela de Ingenieria Electrica
# IE0499 - Proyecto Electrico
# Desarrollo de una interfaz grafica interactiva para la
# elaboracion de floorplan en ASICs

# Estudiante: Elizabeth Matamoros Bojorge C04652

# Este script toma un netlist.json generado por Yosys y le extrae 
# puertos, celdas y nets para luego generar un nuevo JSON con todo lo 
# necesario para la funcionalidad de la GUI 

# Dependencias necesarias
import argparse, json, math
from pathlib import Path


def to_float(v, default=300.0):
    """
    Funcion que convierte un valor a float, intenta convertir directamente
    si falla, limpia espacios y comillas dobles y reintenta
    Si no es posible, devuelve default

    Parametros
    v: valor a convertir
    default: valor de retorno si no se puede convertir

    Return
    float: numero en punto flotante resultante de la conversion

    """
    if v is None: return float(default) # Trata el caso default
    try: return float(v) # Primer intento directo
    except: # Si falla el primer intento, entra al segundo intento
        try: return float(str(v).strip().strip('"'))
        except: return float(default) # Si aun no se puede, devuelve default


def build_design(netlist_path: Path, top: str, out_path: Path,
                 die_w: int, die_h: int, grid: int, core_margin: int,
                 max_side_frac: float, min_side_px: int):
    """
    Funcion que construye el design.json para la GUI a partir de 
    un netlist JSON de Yosys, esta funcion lee el netlist, 
    toma el modulo top y genera una representacion para la interfaz con: die,
    modules, ports y nets, ademas se implementa un regla de escalado

    Parametros
    netlist_path (Path): ruta al netlist JSON de Yosys
    top (str): nombre del modulo top presente en modules
    out_path (Path): ruta de salida del design.json
    die_w (int): ancho del canvas (px)
    die_h (int): alto del canvas (px)
    grid (int): tamano de la grilla de alineacion (px).
    core_margin (int): margen entre borde del die y area util (px).
    max_side_frac (float): fraccion del lado menor del core asignada al bloque mas grande
    min_side_px (int): tamano minimo por bloque para asegurar legibilidad

    Return
    Path: ruta al archivo design.json generado

    """
    # Lee el archivo netlist_path como texto y lo parsea a dict
    data = json.loads(netlist_path.read_text())
    modules = data.get("modules", {}) # Extrae el mapeo dde modulos del JSON de Yosys
    if top not in modules: # Verifica que el nombre de top solicitado exista en el netlist 
        # Si no esta, termina el programa con un mensaje
        raise SystemExit(f"Top '{top}' no esta en modules: {list(modules.keys())}")
    topm = modules[top] # Obtiene el diccionario del modulo top

    # Puertos del top: extraccion de los puertos del modulo top
    ports = []
    # Itera sobre el dic pde uertos del top
    for pname, pobj in (topm.get("ports") or {}).items():
        # Cuenta cuantos bits tiene el puerto,
        width = len(pobj.get("bits", [])) or 1 # Si no hay bits o la lista esta vacia asume ancho 1 
        # Agrega un registro con nombre del puerto, direccion y ancho
        ports.append({
            "name": pname, 
            "dir": pobj.get("direction","in"), 
            "width": width
        })

    # Instancias: calculo de tamano base por instacia a partir de un area
    cells = topm.get("cells", {}) or {} # Toma las celdas declaradas dentro del modulo top
    mods_base = []
    # Itera cada instancia y su objeto en el top
    for inst, cobj in cells.items():
        # Tipo de instancia: submodulo o celda estandar
        mtype = cobj.get("type")
        # Busca el modulo con nombre en modules y toma sus atributos, si no hay nada en modules {}
        attrs = (modules.get(mtype) or {}).get("attributes") or {}
        # Intenta leer area_hint, si no existe, usa 300.0 como valor por defecto
        area  = to_float(attrs.get("area_hint"), 300.0)
        # Magnitud base para tamano del bloque proporcional 
        # a sqrt(area), se fuerza un tamano minimo de 1.0
        base  = max(1.0, math.sqrt(area))  
        # Registro de datos de la instancia
        mods_base.append({
            "inst": inst, 
            "type": mtype, 
            "area": area, 
            "base": base
        })

    # Core disponible: calculo de area util para ubicar los bloques
    # Ancho del core, minimo 1
    core_w = max(1, die_w - 2*core_margin)
    # Alto del core, minimo 1
    core_h = max(1, die_h - 2*core_margin)
    # Lado menor del core, se usa como referencia para limitar el tamano del bloque mas grande
    die_min = min(core_w, core_h)

    # Escalado: fija un tope relativo para el bloque mas grande
    # Busca la base maxima entre las instancias, default 1.0 si no hay modulos
    max_base = max((m["base"] for m in mods_base), default=1.0)
    # Define el tamano objetivo del bloque mas grande
    target_max_side = max(min_side_px, int(round(die_min * max_side_frac)))
    # Factor de escala comun de todos los bloques
    k = target_max_side / max_base  
    # Tamano minimo visible
    min_side = max(2*grid, min_side_px)

    # Colocacion inicial: empaquetado en filas dentro del core
    # Lista dee salida con cada bloque ya escalado y posicionado 
    modules_out = []
    x = core_margin + grid # Inicio x con margen + una celda de grilla
    y = core_margin + grid # Inicio y con margen + una celda de grilla
    row_h = 0 # Altura maxima de la fila actual
    # Itera las instancias calculadas anteriormente
    for m in mods_base:
        # Convierte la base a pixeles multiplicando por el factor global (k)
        side = max(min_side, int(round(m["base"] * k)))
        # Cabe en el ancho util? Si al colocarlo se pasa del borde derecho del core
        # se hace salto de linea
        if x + side > (die_w - core_margin):
            x = core_margin + grid # Reinicia columna al borde izquierdo del core
            y += row_h + grid # Baja a la siguiente fila, dejando un espacio de grilla entre filas
            row_h = 0 # Reinicia la altura de la nueva fila
         # Registra el bloque con su posición inicial y orientacion
        modules_out.append({
            "inst": m["inst"], 
            "type": m["type"], 
            "area": m["area"],
            "w": side,
            "h": side, 
            "orient": "N", 
            "x": x, 
            "y": y
        })
        # Avanza en x y actualiza la altura de la fila
        x += side + grid
        row_h = max(row_h, side)

    # Conectividad: seccion que arma la informacion de nets que la GUI 
    # usara para dibujar conexiones y calcular metricas simples
    bit_eps = {}
    # Itera sobre cada instancia del top
    for inst, cobj in cells.items():
        # Para cada pin de esa instancia, se toma la lista de bits a los que esta conectado
        for port_name, conn_bits in (cobj.get("connections") or {}).items():
            # Itera cada bit conectado a ese pin
            for bit in conn_bits:
                # Registra el endpoint a la lista de ese bit
                bit_eps.setdefault(bit, []).append(f"{inst}.{port_name}")
    # Se repite el proceso anterior para puertos del top
    for pname, pobj in (topm.get("ports") or {}).items():
        # Itera los bits que componen ese puerto
        for bit in pobj.get("bits", []):
            # Regista el endpoint asociado a ese bit
            bit_eps.setdefault(bit, []).append(f"top.{pname}")

    nets_out = []
    # Recorre los nombres de net definidos por Yosys en el top
    for nname, nobj in (topm.get("netnames") or {}).items():
        eps = []
        # Lista de bits que pertenecen a esa net
        bits = nobj.get("bits", []) or []
        # Itera los bits que forman la net 
        for bit in bits:
            # Anade los endpoints que se conectaron a ese bit
            eps.extend(bit_eps.get(bit, []))
        # Quita duplicados y realiza orden estable
        eps = sorted(set(eps))
        # Toma en cuenta solo las nets que conectan en dos extremos
        if len(eps) >= 2:
            # Calcula el ancho del bus, para contemplar las conexiones mas gruesas en la GUI
            bw = max(1, len(bits))
            # Registra la net con nombre, endpoints, weight y ancho en bits
            nets_out.append({
                "name": nname,
                "endpoints": eps,
                "weight": len(eps) - 1,
                "bw": bw
            })
    # Construye el objeto final que tomara la GUI 
    design = {
        "die": {"width": die_w, "height": die_h, "grid": grid, "core_margin": core_margin},
        "top": top,
        "modules": modules_out,
        "ports": ports,
        "nets": nets_out
    }
    # Guarda el contenido de design como JSON legible en el archivo de salida
    out_path.write_text(json.dumps(design, indent=2))
    return out_path # Devuelve la ruta del archivo generado

def main():
    """

    Funcion que ejecuta la conversion de un netlist JSON de Yosys
    a un design.json para la GUI. Lee argumentos de linea de comandos 
    (--netlist, --top, --out, --die, etc.), parsea el tamano del canvas 
    y delega en build_design para generar design.json. Imprime la ruta 
    del archivo generado al finalizar con exito.

    """
    # Parser de argumentos
    ap = argparse.ArgumentParser(description="Ingesta netlist.json -> design.json")
    ap.add_argument("--netlist", default="artifacts/netlist.json")
    ap.add_argument("--top", default="simple_top")
    ap.add_argument("--out", default="artifacts/design.json")
    ap.add_argument("--die", default="1000x1000", help="W×H en pixeles (ej: 1000x1000)")
    ap.add_argument("--grid", type=int, default=20)
    ap.add_argument("--core-margin", type=int, default=40)
    # Controles del escalado
    ap.add_argument("--max-side-frac", type=float, default=0.10,
                    help="Fraccion del lado menor del core que tomara el modulo mas grande (ej: 0.10 = 10%)")
    ap.add_argument("--min-side", type=int, default=24,
                    help="Tamano minimo de bloque en pixeles (tambien se respeta 2×grid)")
    args = ap.parse_args()

    # Normaliza el separador, divide en ["W","H"] y convierte a enteros 
    die_w, die_h = map(int, args.die.lower().replace("×","x").split("x"))
    # Construye design.json con todos los parametros parseados
    out = build_design(Path(args.netlist), args.top, Path(args.out),
                       die_w, die_h, args.grid, args.core_margin,
                       args.max_side_frac, args.min_side)
    print(f"OK -> {out}") 

if __name__ == "__main__":
    main()
