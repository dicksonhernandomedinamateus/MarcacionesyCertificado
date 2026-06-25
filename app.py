import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, date, time, timedelta
import json
import io
import os

# ─── Configuración de página ───────────────────────────────────────────────────
st.set_page_config(
    page_title="Análisis de Marcaciones DIAN",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Estilos CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main-header {
        background: linear-gradient(90deg, #003366 0%, #0055a5 100%);
        color: white;
        padding: 1.2rem 2rem;
        border-radius: 8px;
        margin-bottom: 1.5rem;
    }
    .main-header h1 { margin: 0; font-size: 1.6rem; }
    .main-header p  { margin: 0.3rem 0 0; font-size: 0.9rem; opacity: 0.85; }
    .metric-card {
        background: white;
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        padding: 1rem;
        text-align: center;
        box-shadow: 0 2px 4px rgba(0,0,0,0.06);
    }
    .metric-card .value { font-size: 2rem; font-weight: 700; }
    .metric-card .label { font-size: 0.8rem; color: #666; margin-top: 0.2rem; }
    .ok   { color: #2e7d32; }
    .warn { color: #e65100; }
    .danger { color: #c62828; }
    .badge {
        display: inline-block;
        padding: 0.2rem 0.6rem;
        border-radius: 12px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .badge-ok     { background:#e8f5e9; color:#2e7d32; }
    .badge-late   { background:#fff3e0; color:#e65100; }
    .badge-absent { background:#ffebee; color:#c62828; }
    .badge-fixed  { background:#e3f2fd; color:#1565c0; }
    .badge-commit { background:#f3e5f5; color:#6a1b9a; }
    .stTabs [data-baseweb="tab"] { font-size: 0.9rem; }
</style>
""", unsafe_allow_html=True)

# ─── Constantes ────────────────────────────────────────────────────────────────
HORARIOS = {
    "7:00 - 4:00": {"entrada": time(7, 0),  "salida": time(16, 0), "tolerancia": 0},
    "7:30 - 4:30": {"entrada": time(7, 30), "salida": time(16, 30), "tolerancia": 0},
    "8:00 - 5:00": {"entrada": time(8, 0),  "salida": time(17, 0), "tolerancia": 0},
}

MESES_OPCIONES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio", 
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
]
ANIOS_OPCIONES = ["2024", "2025", "2026", "2027", "2028"]

# ─── Funciones de utilidad ─────────────────────────────────────────────────────

def excel_serial_to_date(serial):
    """Convierte número serial de Excel a date."""
    try:
        base = datetime(1899, 12, 30)
        return (base + timedelta(days=int(serial))).date()
    except Exception:
        return None

def fraction_to_time(frac):
    """Convierte fracción decimal de Excel a time."""
    try:
        if isinstance(frac, str):
            return None
        total_seconds = round(float(frac) * 86400)
        h, rem = divmod(total_seconds, 3600)
        m, s = divmod(rem, 60)
        return time(h % 24, m, s)
    except Exception:
        return None

def parse_time_str(ts):
    """Parsea string HH:MM a time."""
    try:
        ts = ts.strip()
        if not ts or ts in ("No Marco", "-"):
            return None
        h, m = map(int, ts.split(":"))
        return time(h, m)
    except Exception:
        return None

def detect_schedule(arrival_times):
    """
    Detecta el horario oficial del funcionario basándose en la concentración
    de sus marcaciones de entrada.
    """
    valid = [t for t in arrival_times if t is not None]
    if len(valid) < 3:
        return "7:00 - 4:00"  # default

    minutes = [t.hour * 60 + t.minute for t in valid]
    best_schedule = None
    best_score = float("inf")

    candidates = {
        "7:00 - 4:00": 7 * 60,
        "7:30 - 4:30": 7 * 60 + 30,
        "8:00 - 5:00": 8 * 60,
    }
    for sched, ref_min in candidates.items():
        close = [m for m in minutes if abs(m - ref_min) <= 45]
        if len(close) >= len(minutes) * 0.3:
            mean_diff = np.mean([abs(m - ref_min) for m in close])
            if mean_diff < best_score:
                best_score = mean_diff
                best_schedule = sched

    return best_schedule or "7:00 - 4:00"

def is_weekend(d):
    """Retorna True si la fecha es sábado o domingo."""
    return d.weekday() >= 5

def classify_day(row, schedule_key):
    """
    Clasifica un día y retorna la inconsistencia (si existe).
    """
    d = row.get("fecha")
    primera = row.get("primera")
    ultima = row.get("ultima")

    if d is None:
        return None
    if is_weekend(d):
        return None

    sched = HORARIOS[schedule_key]
    hora_entrada = sched["entrada"]
    tolerancia = sched["tolerancia"]

    limite_tarde = (datetime.combine(date.today(), hora_entrada) +
                    timedelta(minutes=tolerancia)).time()

    if primera is None and ultima is None:
        return {
            "tipo": "NO_MARCO",
            "detalle": "No registró marcación",
            "primera": None,
            "ultima": None,
        }

    inconsistencias = []

    if primera is not None and primera > limite_tarde:
        retraso = (datetime.combine(date.today(), primera) -
                   datetime.combine(date.today(), hora_entrada))
        mins = int(retraso.total_seconds() / 60)
        inconsistencias.append(f"Llegada tarde: {primera.strftime('%H:%M')} "
                               f"(+{mins} min sobre {hora_entrada.strftime('%H:%M')})")

    if ultima is None and primera is not None:
        inconsistencias.append("No registró marcación de salida")

    if inconsistencias:
        return {
            "tipo": "INCONSISTENCIA",
            "detalle": " | ".join(inconsistencias),
            "primera": primera,
            "ultima": ultima,
        }

    return None

def parse_time_value(val):
    """
    Convierte cualquier representación de tiempo a datetime.time.
    Maneja: datetime.time, strings 'HH:MM', strings 'No Marco', float fracciones, NaN.
    """
    if val is None:
        return None
    if isinstance(val, time):
        return val
    if isinstance(val, str):
        v = val.strip()
        if not v or "No Marco" in v or v == "-":
            return None
        return parse_time_str(v)
    if isinstance(val, float):
        if np.isnan(val):
            return None
        return fraction_to_time(val)
    if isinstance(val, int):
        return fraction_to_time(val)
    return None

def load_excel(uploaded_file, filename="archivo.xlsx"):
    """
    Lee el archivo Excel y construye un diccionario por funcionario.
    Soporta .xlsx y .xls.
    """
    ext = os.path.splitext(filename)[-1].lower()
    engine = "xlrd" if ext == ".xls" else "openpyxl"
    xl = pd.read_excel(uploaded_file, sheet_name=None, header=0, engine=engine)
    result = {}

    for nombre, df in xl.items():
        df.columns = [str(c).strip() for c in df.columns]

        col_dia     = next((c for c in df.columns if c.lower() in ("dia", "día")), None)
        col_marc    = next((c for c in df.columns if "marcac" in c.lower()), None)
        col_primera = next((c for c in df.columns if "primera" in c.lower()), None)
        col_ultima  = next((c for c in df.columns if "ltima" in c.lower() or "ultima" in c.lower()), None)
        col_nov     = next((c for c in df.columns if "novedad" in c.lower()), None)

        if col_dia is None:
            continue

        rows = []
        for _, r in df.iterrows():
            dia_val = r.get(col_dia)
            if dia_val is None or (isinstance(dia_val, str) and dia_val.upper() == "DIA"):
                continue
            try:
                if pd.isna(dia_val):
                    continue
            except (TypeError, ValueError):
                pass

            if hasattr(dia_val, "date"):
                fecha = dia_val.date()
            else:
                fecha = excel_serial_to_date(dia_val)
            if fecha is None:
                continue

            primera = parse_time_value(r.get(col_primera) if col_primera else None)
            ultima  = parse_time_value(r.get(col_ultima)  if col_ultima  else None)

            marcaciones = r.get(col_marc, "") if col_marc else ""
            novedades   = r.get(col_nov, "")  if col_nov  else ""
            try:
                if pd.isna(marcaciones):
                    marcaciones = ""
            except (TypeError, ValueError):
                pass
            try:
                if pd.isna(novedades):
                    novedades = ""
            except (TypeError, ValueError):
                pass

            rows.append({
                "fecha":       fecha,
                "marcaciones": str(marcaciones),
                "primera":     primera,
                "ultima":      ultima,
                "novedades":   str(novedades),
            })

        if rows:
            result[nombre] = pd.DataFrame(rows)

    return result

# ─── Gestión de estado ─────────────────────────────────────────────────────────

def init_state():
    defaults = {
        "datos":           {},
        "horarios":        {},
        "inconsistencias": {},
        "compromisos":     [],
        "archivo_cargado": False,
        "mes_periodo":     "junio",
        "anio_periodo":    "2026",
        "jefe_nombre":     "JORGE IVÁN RODRÍGUEZ",
        "jefe_cargo":      "JEFE DIVISIÓN DE FISCALIZACIÓN Y LIQUIDACIÓN TRIBUTARIA INTENSIVA",
        "last_file_id":    None,
        "ultimo_func_tab2": None, 
        "ultimo_func_tab3": "— Todos —", 
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()

def compute_inconsistencias(datos, horarios):
    result = {}
    for nombre, df in datos.items():
        sched = horarios.get(nombre, "7:00 - 4:00")
        incs = []
        for _, row in df.iterrows():
            inc = classify_day(row, sched)
            if inc:
                incs.append({
                    "fecha":            row["fecha"],
                    "tipo":             inc["tipo"],
                    "detalle":          inc["detalle"],
                    "primera":          inc["primera"],
                    "ultima":           inc["ultima"],
                    "subsanada":        False,
                    "compromiso":       False,
                    "texto_compromiso": "",
                })
        result[nombre] = incs
    return result

# ─── Generación de certificado PDF ────────────────────────────────────────────

def generar_certificado_pdf(periodo, jefe_nombre, jefe_cargo, tiene_inasistencias):
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
    from reportlab.lib import colors

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        leftMargin=2.5 * cm,
        rightMargin=2.5 * cm,
    )

    styles = getSampleStyleSheet()
    style_center = ParagraphStyle("center", parent=styles["Normal"],
                                  alignment=TA_CENTER, fontSize=11, leading=16)
    style_body   = ParagraphStyle("body",   parent=styles["Normal"],
                                  alignment=TA_JUSTIFY, fontSize=11, leading=18,
                                  spaceAfter=10)
    style_bold_c = ParagraphStyle("boldc",  parent=styles["Normal"],
                                  alignment=TA_CENTER, fontSize=12, leading=18,
                                  fontName="Helvetica-Bold")
    style_footer = ParagraphStyle("footer", parent=styles["Normal"],
                                  alignment=TA_LEFT, fontSize=9, leading=12,
                                  textColor=colors.HexColor("#444444"))
    style_sign   = ParagraphStyle("sign",   parent=styles["Normal"],
                                  alignment=TA_CENTER, fontSize=11,
                                  fontName="Helvetica-Bold")

    story = []

    story.append(Paragraph(
        "EL JEFE DE LA DIVISIÓN DE FISCALIZACIÓN Y LIQUIDACIÓN TRIBUTARIA INTENSIVA<br/>"
        "DE LA DIRECCIÓN SECCIONAL DE IMPUESTOS Y ADUANAS DE ARMENIA<br/>"
        "DE LA UNIDAD ADMINISTRATIVA ESPECIAL DIRECCIÓN DE IMPUESTOS Y<br/>"
        "ADUANAS NACIONALES",
        style_bold_c,
    ))
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(
        "En cumplimiento de lo dispuesto en el Decreto No. 051 del 16 de enero de 2018",
        style_center,
    ))
    story.append(Spacer(1, 0.8 * cm))
    story.append(Paragraph("<b>CERTIFICA:</b>", style_bold_c))
    story.append(Spacer(1, 0.5 * cm))

    story.append(Paragraph(
        f"Que ha verificado los reportes de asistencia del personal adscrito a esta "
        f"División, en el período comprendido entre los días 1° y 30 del mes de "
        f"{periodo}.",
        style_body,
    ))

    no_check = "_X_" if not tiene_inasistencias else "___"
    si_check = "_X_" if tiene_inasistencias else "___"
    story.append(Paragraph(
        f"Que NO {no_check} SI {si_check} se presentaron inasistencias no justificadas "
        f"del personal a mi cargo, durante el período comprendido entre los días 1° y 30 "
        f"de {periodo}.",
        style_body,
    ))

    story.append(Paragraph(
        "Que, en caso de haberse presentado inasistencias no justificadas, se acompaña a "
        "la presente certificación, el reporte correspondiente.",
        style_body,
    ))

    story.append(Paragraph(
        f"Lo anterior para efectos de pago de la remuneración del mes de "
        f"{periodo.split()[0]} a los servidores públicos de esta División.",
        style_body,
    ))

    hoy = datetime.now()
    meses_actual = ["enero","febrero","marzo","abril","mayo","junio",
                    "julio","agosto","septiembre","octubre","noviembre","diciembre"]
    fecha_str = (f"el día {hoy.day:02d} ({hoy.strftime('%d').lstrip('0')}) "
                 f"de {meses_actual[hoy.month-1]} de {hoy.year}")
    story.append(Paragraph(
        f"Se expide en Armenia, {fecha_str}, con destino a la "
        f"Subdirección de Gestión del Empleo Público.",
        style_body,
    ))

    story.append(Spacer(1, 1.5 * cm))
    story.append(HRFlowable(width="60%", thickness=1, color=colors.black, hAlign="CENTER"))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(jefe_nombre.upper(), style_sign))
    story.append(Paragraph(jefe_cargo, style_center))

    story.append(Spacer(1, 1.5 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph(
        "Dirección Seccional de Impuestos y Aduanas de Armenia<br/>"
        "Calle 21 # 14-14 | 6067357376 - 3103158135<br/>"
        "Código postal 630004<br/>"
        "www.dian.gov.co<br/>"
        "Formule su petición, queja, sugerencia o reclamo en el Sistema PQSR de la DIAN",
        style_footer,
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

# ─── INTERFAZ PRINCIPAL ────────────────────────────────────────────────────────

st.markdown("""
<div class="main-header">
    <h1>🏛️ Sistema de Análisis de Marcaciones</h1>
    <p>Dirección Seccional de Impuestos y Aduanas de Armenia — DIAN</p>
</div>
""", unsafe_allow_html=True)

# ─── SIDEBAR ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.subheader("⚙️ Configuración")
    
    st.session_state.mes_periodo = st.selectbox(
        "Mes del certificado", 
        MESES_OPCIONES, 
        index=MESES_OPCIONES.index(st.session_state.mes_periodo)
    )
    st.session_state.anio_periodo = st.selectbox(
        "Año del certificado", 
        ANIOS_OPCIONES, 
        index=ANIOS_OPCIONES.index(st.session_state.anio_periodo)
    )
    st.session_state.jefe_nombre = st.text_input(
        "Nombre del Jefe", value=st.session_state.jefe_nombre
    )
    st.session_state.jefe_cargo = st.text_input(
        "Cargo del Jefe", value=st.session_state.jefe_cargo
    )
    st.markdown("---")
    st.caption("Sistema de gestión de asistencia v1.0")

# ─── TABS PRINCIPALES ─────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs([
    "📂 Cargar Archivo",
    "👤 Funcionarios",
    "📋 Inconsistencias",
    "📜 Certificado",
])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — CARGAR ARCHIVO
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    st.subheader("Cargar archivo de marcaciones")
    st.info(
        "Suba el archivo Excel con las marcaciones. Cada hoja debe corresponder "
        "a un funcionario y contener columnas: DIA, MARCACIONES, PRIMERA MARCACION, "
        "ULTIMA MARCACION."
    )

    uploaded = st.file_uploader(
        "Seleccionar archivo Excel (.xlsx o .xls)", type=["xlsx", "xls"]
    )

    if uploaded:
        if st.session_state.get("last_file_id") != uploaded.file_id:
            with st.spinner("Procesando archivo..."):
                try:
                    datos = load_excel(uploaded, getattr(uploaded, "name", "archivo.xlsx"))
                    st.session_state.datos = datos

                    horarios = {}
                    for nombre, df in datos.items():
                        arrivals = df["primera"].tolist()
                        horarios[nombre] = detect_schedule(arrivals)
                    st.session_state.horarios = horarios

                    st.session_state.inconsistencias = compute_inconsistencias(datos, horarios)
                    st.session_state.archivo_cargado = True
                    st.session_state.last_file_id = uploaded.file_id

                except Exception as e:
                    st.error(f"Error al procesar el archivo: {e}")
                    st.exception(e)

    if st.session_state.get("archivo_cargado"):
        st.success(f"✅ Archivo en memoria. Se encontraron **{len(st.session_state.datos)} funcionarios**.")

    if st.session_state.archivo_cargado:
        st.markdown("### Resumen general")
        datos = st.session_state.datos
        incs  = st.session_state.inconsistencias
        hors  = st.session_state.horarios

        total_func = len(datos)
        total_inc  = sum(len(v) for v in incs.values())
        total_sub  = sum(
            sum(1 for i in v if i["subsanada"] or i["compromiso"])
            for v in incs.values()
        )
        pendientes = total_inc - total_sub

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.markdown(f"""<div class="metric-card">
                <div class="value ok">{total_func}</div>
                <div class="label">Funcionarios</div></div>""",
                unsafe_allow_html=True)
        with col2:
            st.markdown(f"""<div class="metric-card">
                <div class="value warn">{total_inc}</div>
                <div class="label">Total inconsistencias</div></div>""",
                unsafe_allow_html=True)
        with col3:
            st.markdown(f"""<div class="metric-card">
                <div class="value ok">{total_sub}</div>
                <div class="label">Subsanadas</div></div>""",
                unsafe_allow_html=True)
        with col4:
            color = "danger" if pendientes > 0 else "ok"
            st.markdown(f"""<div class="metric-card">
                <div class="value {color}">{pendientes}</div>
                <div class="label">Pendientes</div></div>""",
                unsafe_allow_html=True)

        st.markdown("### Detalle por funcionario")
        rows_sum = []
        
        def count_pend(n):
            n_inc = len(incs.get(n, []))
            n_sub = sum(1 for i in incs.get(n, []) if i["subsanada"] or i["compromiso"])
            return n_inc - n_sub

        nombres_ordenados_resumen = sorted(list(datos.keys()), key=count_pend, reverse=True)

        for nombre in nombres_ordenados_resumen:
            n_inc = len(incs.get(nombre, []))
            n_sub = sum(1 for i in incs.get(nombre, []) if i["subsanada"] or i["compromiso"])
            rows_sum.append({
                "Funcionario":     nombre,
                "Horario":         hors.get(nombre, "—"),
                "Inconsistencias": n_inc,
                "Subsanadas":      n_sub,
                "Pendientes":      n_inc - n_sub,
                "Estado":          "✅ OK" if n_inc == n_sub else f"⚠️ {n_inc-n_sub} pendiente(s)",
            })
        st.dataframe(pd.DataFrame(rows_sum), use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — FUNCIONARIOS
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    if not st.session_state.archivo_cargado:
        st.warning("Primero cargue un archivo en la pestaña anterior.")
    else:
        st.subheader("Detalle por funcionario")

        def count_pendientes(n):
            return sum(1 for i in st.session_state.inconsistencias.get(n, []) if not i["subsanada"] and not i["compromiso"])
            
        nombres = list(st.session_state.datos.keys())
        nombres_ordenados = sorted(nombres, key=count_pendientes, reverse=True)
        
        try:
            idx_t2 = nombres_ordenados.index(st.session_state.ultimo_func_tab2)
        except (ValueError, TypeError):
            idx_t2 = 0
            
        func_sel = st.selectbox(
            "Seleccionar funcionario", 
            nombres_ordenados, 
            index=idx_t2,
        )
        st.session_state.ultimo_func_tab2 = func_sel

        if func_sel:
            df_func = st.session_state.datos[func_sel]
            sched   = st.session_state.horarios[func_sel]

            col_a, col_b = st.columns([2, 2])
            with col_a:
                nuevo_sched = st.selectbox(
                    "Horario detectado / asignar manualmente",
                    list(HORARIOS.keys()),
                    index=list(HORARIOS.keys()).index(sched),
                    key=f"sched_{func_sel}",
                )
            with col_b:
                if st.button("Aplicar horario", key=f"apply_{func_sel}"):
                    st.session_state.horarios[func_sel] = nuevo_sched
                    sub_inc = compute_inconsistencias(
                        {func_sel: df_func}, {func_sel: nuevo_sched}
                    )
                    st.session_state.inconsistencias[func_sel] = sub_inc[func_sel]
                    st.rerun()

            st.markdown(f"**Horario asignado:** `{sched}` — "
                        f"Entrada: `{HORARIOS[sched]['entrada'].strftime('%H:%M')}` | "
                        f"Tolerancia: `{HORARIOS[sched]['tolerancia']} min`")

            inc_fechas = {
                i["fecha"]: i for i in st.session_state.inconsistencias.get(func_sel, [])
            }

            rows_dias = []
            for _, row in df_func.iterrows():
                d = row["fecha"]
                inc = inc_fechas.get(d)
                if is_weekend(d):
                    estado = "🗓️ Fin de semana"
                elif inc is None:
                    estado = "✅ Normal"
                elif inc["subsanada"]:
                    estado = "🔵 Subsanada"
                elif inc["compromiso"]:
                    estado = "🟣 Compromiso"
                else:
                    estado = f"⚠️ {inc['tipo']}"

                rows_dias.append({
                    "Fecha":   d.strftime("%Y-%m-%d"),
                    "Día":     d.strftime("%A").capitalize(),
                    "Entrada": row["primera"].strftime("%H:%M") if row["primera"] else "—",
                    "Salida":  row["ultima"].strftime("%H:%M")  if row["ultima"]  else "—",
                    "Estado":  estado,
                    "Detalle": inc["detalle"] if inc else "",
                })

            st.dataframe(pd.DataFrame(rows_dias), use_container_width=True,
                         hide_index=True, height=450)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — INCONSISTENCIAS
# ══════════════════════════════════════════════════════════════════════════════
with tab3:
    if not st.session_state.archivo_cargado:
        st.warning("Primero cargue un archivo en la pestaña anterior.")
    else:
        st.subheader("Gestión de inconsistencias")

        def count_pendientes_tab3(n):
            return sum(1 for i in st.session_state.inconsistencias.get(n, []) if not i["subsanada"] and not i["compromiso"])
            
        nombres = list(st.session_state.datos.keys())
        nombres_ordenados_tab3 = sorted(nombres, key=count_pendientes_tab3, reverse=True)
        lista_opciones_tab3 = ["— Todos —"] + nombres_ordenados_tab3

        try:
            idx_t3 = lista_opciones_tab3.index(st.session_state.ultimo_func_tab3)
        except ValueError:
            idx_t3 = 0

        col_f1, col_f2 = st.columns([2, 2])
        with col_f1:
            func_filtro = st.selectbox(
                "Filtrar por funcionario", 
                lista_opciones_tab3, 
                index=idx_t3
            )
            st.session_state.ultimo_func_tab3 = func_filtro
            
        with col_f2:
            estado_filtro = st.selectbox(
                "Filtrar por estado",
                ["— Todos —", "Pendientes", "Subsanadas", "Con compromiso"],
                index=1,
                key="filtro_estado",
            )

        if func_filtro != "— Todos —":
            pendientes_func = [i for i in st.session_state.inconsistencias[func_filtro] if not i["subsanada"] and not i["compromiso"]]
            
            if pendientes_func:
                st.markdown("---")
                with st.form(key=f"form_gen_{func_filtro}"):
                    st.subheader("🚀 Acción Rápida: Compromiso General")
                    st.info(f"El funcionario **{func_filtro}** tiene **{len(pendientes_func)} inconsistencias pendientes**. Puede aplicar un compromiso a TODAS en bloque.")
                    
                    texto_general = st.text_area(
                        "Texto del compromiso general", 
                        placeholder="Este compromiso subsanará en bloque las inconsistencias pendientes..."
                    )
                    
                    submit_general = st.form_submit_button("✅ Subsanar todas con este compromiso")
                    
                    if submit_general:
                        if texto_general.strip():
                            conteo = len(pendientes_func)
                            for idx, inc in enumerate(st.session_state.inconsistencias[func_filtro]):
                                if not inc["subsanada"] and not inc["compromiso"]:
                                    st.session_state.inconsistencias[func_filtro][idx]["compromiso"] = True
                                    st.session_state.inconsistencias[func_filtro][idx]["texto_compromiso"] = texto_general.strip()
                            
                            st.session_state.compromisos.append({
                                "funcionario": func_filtro,
                                "fecha": "Múltiples fechas",
                                "tipo": "COMPROMISO GENERAL",
                                "detalle": f"Se eliminaron y subsanaron {conteo} inconsistencias en bloque.",
                                "compromiso": texto_general.strip(),
                                "registrado": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            })
                            st.rerun()
                        else:
                            st.warning("Debe escribir el texto del compromiso general.")
                st.markdown("---")

        lista_trabajo = []
        for nombre, incs_list in st.session_state.inconsistencias.items():
            if func_filtro != "— Todos —" and nombre != func_filtro:
                continue
            for idx, inc in enumerate(incs_list):
                est = ("Subsanada" if inc["subsanada"]
                       else "Con compromiso" if inc["compromiso"]
                       else "Pendiente")
                if estado_filtro == "Pendientes"      and est != "Pendiente":      continue
                if estado_filtro == "Subsanadas"      and est != "Subsanada":      continue
                if estado_filtro == "Con compromiso"  and est != "Con compromiso": continue
                lista_trabajo.append((nombre, idx, inc, est))

        if not lista_trabajo:
            st.success("🎉 No hay inconsistencias en esta vista con el filtro actual.")
        else:
            st.markdown(f"**{len(lista_trabajo)} inconsistencia(s) en vista**")

            for nombre, idx, inc, estado in lista_trabajo:
                fecha_str = inc["fecha"].strftime("%d/%m/%Y") if hasattr(inc["fecha"], 'strftime') else inc["fecha"]
                dia_str   = inc["fecha"].strftime("%A").capitalize() if hasattr(inc["fecha"], 'strftime') else ""
                borde = ("#1565c0" if estado == "Subsanada"
                         else "#6a1b9a" if estado == "Con compromiso"
                         else "#c62828")

                with st.container():
                    st.markdown(
                        f"<div style='border-left:4px solid {borde}; "
                        f"padding:0.5rem 1rem; margin-bottom:0.5rem; "
                        f"background:#fafafa; border-radius:4px;'>"
                        f"<b>{nombre}</b> — {dia_str} {fecha_str} — "
                        f"<span style='color:{borde}'>{estado}</span><br/>"
                        f"<small>{inc['tipo']}: {inc['detalle']}</small></div>",
                        unsafe_allow_html=True,
                    )

                    if estado == "Pendiente":
                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button("✅ Marcar subsanada", key=f"sub_{nombre}_{idx}"):
                                st.session_state.inconsistencias[nombre][idx]["subsanada"] = True
                                st.rerun()
                        with c2:
                            with st.expander("📝 Registrar compromiso individual"):
                                texto = st.text_area(
                                    "Texto del compromiso",
                                    key=f"txt_{nombre}_{idx}",
                                    placeholder="Describa el compromiso adquirido...",
                                    height=80,
                                )
                                if st.button("Guardar compromiso", key=f"commit_{nombre}_{idx}"):
                                    if texto.strip():
                                        st.session_state.inconsistencias[nombre][idx]["compromiso"] = True
                                        st.session_state.inconsistencias[nombre][idx]["texto_compromiso"] = texto.strip()
                                        st.session_state.compromisos.append({
                                            "funcionario": nombre,
                                            "fecha":       inc["fecha"].strftime("%Y-%m-%d"),
                                            "tipo":        inc["tipo"],
                                            "detalle":     inc["detalle"],
                                            "compromiso":  texto.strip(),
                                            "registrado":  datetime.now().strftime("%Y-%m-%d %H:%M"),
                                        })
                                        st.rerun()
                                    else:
                                        st.warning("Escriba el texto del compromiso.")

                    elif estado == "Con compromiso":
                        st.info(f"💬 **Compromiso:** {inc['texto_compromiso']}")
                        if st.button("↩️ Revertir compromiso", key=f"revert_{nombre}_{idx}"):
                            st.session_state.inconsistencias[nombre][idx]["compromiso"] = False
                            st.session_state.inconsistencias[nombre][idx]["texto_compromiso"] = ""
                            st.rerun()

                    elif estado == "Subsanada":
                        if st.button("↩️ Revertir subsanación", key=f"revsub_{nombre}_{idx}"):
                            st.session_state.inconsistencias[nombre][idx]["subsanada"] = False
                            st.rerun()

            if st.session_state.compromisos:
                st.markdown("---")
                st.subheader("📒 Registro de compromisos")
                df_comp = pd.DataFrame(st.session_state.compromisos)
                df_comp.columns = ["Funcionario", "Fecha", "Tipo",
                                   "Detalle", "Compromiso adquirido", "Registrado"]
                st.dataframe(df_comp, use_container_width=True, hide_index=True)

                buf_comp = io.BytesIO()
                df_comp.to_excel(buf_comp, index=False)
                st.download_button(
                    "⬇️ Descargar registro de compromisos (Excel)",
                    data=buf_comp.getvalue(),
                    file_name="compromisos_asistencia.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — CERTIFICADO
# ══════════════════════════════════════════════════════════════════════════════
with tab4:
    st.subheader("Generar certificado de cumplimiento de horario")

    if not st.session_state.archivo_cargado:
        st.warning("Primero cargue un archivo en la pestaña anterior.")
    else:
        incs_total = sum(len(v) for v in st.session_state.inconsistencias.values())
        subsanadas = sum(
            sum(1 for i in v if i["subsanada"] or i["compromiso"])
            for v in st.session_state.inconsistencias.values()
        )
        pendientes = incs_total - subsanadas

        # Calculamos el texto final del período para usarlo en esta sección
        periodo_certificado = f"{st.session_state.mes_periodo} de {st.session_state.anio_periodo}"

        if pendientes > 0:
            st.error(
                f"⛔ Aún hay **{pendientes} inconsistencia(s) pendiente(s)** por subsanar. "
                "Resuelva todas las inconsistencias antes de generar el certificado."
            )
            st.markdown("### Inconsistencias pendientes")
            for nombre, incs_list in st.session_state.inconsistencias.items():
                pend = [i for i in incs_list if not i["subsanada"] and not i["compromiso"]]
                if pend:
                    st.markdown(f"**{nombre}** ({len(pend)} pendiente[s])")
                    for inc in pend:
                        st.markdown(
                            f"  - {inc['fecha'].strftime('%d/%m/%Y')}: "
                            f"{inc['tipo']} — {inc['detalle']}"
                        )
        else:
            if incs_total == 0:
                st.success("✅ No se registraron inconsistencias. El certificado puede generarse.")
                tiene_inasistencias = False
            else:
                st.success(
                    f"✅ Todas las inconsistencias ({incs_total}) han sido subsanadas "
                    "o documentadas con compromisos. El certificado puede generarse."
                )
                tiene_inasistencias = True

            st.markdown("### Vista previa del certificado")
            st.markdown(f"""
> **EL JEFE DE LA DIVISIÓN DE FISCALIZACIÓN Y LIQUIDACIÓN TRIBUTARIA INTENSIVA** > **DE LA DIRECCIÓN SECCIONAL DE IMPUESTOS Y ADUANAS DE ARMENIA** >
> En cumplimiento de lo dispuesto en el Decreto No. 051 del 16 de enero de 2018  
>
> **CERTIFICA:**
>
> Que ha verificado los reportes de asistencia del personal adscrito a esta División,  
> en el período comprendido entre los días 1° y 30 del mes de **{periodo_certificado}**.
>
> Que **NO {'_X_' if not tiene_inasistencias else '___'} SI {'_X_' if tiene_inasistencias else '___'}** > se presentaron inasistencias no justificadas del personal a mi cargo.
>
> _______________________________________________  
> **{st.session_state.jefe_nombre}** > {st.session_state.jefe_cargo}
""")

            if st.button("📄 Generar y descargar certificado PDF", type="primary"):
                try:
                    with st.spinner("Generando certificado..."):
                        pdf_bytes = generar_certificado_pdf(
                            periodo=periodo_certificado,
                            jefe_nombre=st.session_state.jefe_nombre,
                            jefe_cargo=st.session_state.jefe_cargo,
                            tiene_inasistencias=tiene_inasistencias,
                        )
                    st.download_button(
                        label="⬇️ Descargar certificado PDF",
                        data=pdf_bytes,
                        file_name=f"certificado_horario_{periodo_certificado.replace(' ', '_')}.pdf",
                        mime="application/pdf",
                    )
                    st.success("Certificado generado correctamente.")
                except ImportError as e:
                    st.error("Error: Falta la librería 'reportlab' para generar el PDF. Instálala ejecutando 'pip install reportlab' en tu terminal.")