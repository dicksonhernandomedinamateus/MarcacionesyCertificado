import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, date, time, timedelta
import io
import re
 
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
    .badge-ocr    { background:#fff8e1; color:#8d6e00; }
    .stTabs [data-baseweb="tab"] { font-size: 0.9rem; }
</style>
""", unsafe_allow_html=True)
 
# ─── Constantes ────────────────────────────────────────────────────────────────
HORARIOS = {
    "7:00 - 4:00": {"entrada": time(7, 0),  "salida": time(16, 0), "tolerancia": 0},
    "7:30 - 4:30": {"entrada": time(7, 30), "salida": time(16, 30), "tolerancia": 0},
    "8:00 - 5:00": {"entrada": time(8, 0),  "salida": time(17, 0), "tolerancia": 0},
}
 
MESES_NOMBRE = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}
 
TOP_N_REVISION = 3  # número de funcionarios que se revisan manualmente
 
# ─── Parseo de los reportes PDF individuales ──────────────────────────────────
 
TIME_RE = re.compile(r'\d{1,2}:\d{2}:\d{2}|\d{1,2}:\d{2}')
ROW_DATE_RE = re.compile(r'(\d{2})/(\d{2})/(\d{4})')
 
 
def _extraer_texto_pdf(file_bytes, filename):
    """
    Extrae el texto del PDF. Si el PDF no tiene capa de texto (por ejemplo,
    fue generado como una captura/impresión de pantalla), se intenta OCR
    como respaldo. Retorna (texto, used_ocr, error).
    """
    import pdfplumber
 
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            paginas = [p.extract_text() or "" for p in pdf.pages]
            texto = "\n".join(paginas)
 
            if len(texto.strip()) > 50:
                return texto, False, None
 
            # Sin capa de texto -> intentar OCR
            try:
                import pytesseract
            except ImportError:
                return None, False, (
                    "Este PDF no contiene texto (parece ser una imagen/captura de "
                    "pantalla) y la librería 'pytesseract' no está instalada, por lo "
                    "que no se puede leer automáticamente. Instale 'pytesseract' y "
                    "el motor 'tesseract-ocr', o solicite al funcionario un reporte "
                    "exportado directamente (no impreso/capturado)."
                )
 
            ocr_paginas = []
            for p in pdf.pages:
                img = p.to_image(resolution=400)
                ocr_paginas.append(
                    pytesseract.image_to_string(img.original, lang="spa", config="--psm 6")
                )
            texto_ocr = "\n".join(ocr_paginas)
            if len(texto_ocr.strip()) < 20:
                return None, True, "No fue posible extraer contenido legible de este PDF, ni siquiera con OCR."
            return texto_ocr, True, None
 
    except Exception as e:
        return None, False, f"No se pudo abrir el archivo: {e}"
 
 
def _parsear_fila(linea):
    """Parsea una línea de la tabla de marcaciones. Retorna (fecha_date, primera, ultima) o None."""
    m = ROW_DATE_RE.search(linea)
    if not m:
        return None
    d, mo, y = m.groups()
    try:
        fecha = date(int(y), int(mo), int(d))
    except ValueError:
        return None
 
    resto = linea[m.end():]
    tokens = []
    for tm in TIME_RE.finditer(resto):
        val = tm.group()
        kind = "dur" if val.count(":") == 2 else "time"
        tokens.append((tm.start(), kind, val))
 
    times = [t for t in tokens if t[1] == "time"]
    durs = [t for t in tokens if t[1] == "dur"]
 
    primera = ultima = None
    if durs:
        dur_pos = durs[0][0]
        times_before = [t for t in times if t[0] < dur_pos]
        if len(times_before) >= 2:
            primera, ultima = times_before[-2][2], times_before[-1][2]
        elif len(times_before) == 1:
            primera = times_before[0][2]
    else:
        if times:
            primera = times[-1][2]
 
    def to_time(s):
        if s is None:
            return None
        h, mi = s.split(":")[:2]
        try:
            return time(int(h) % 24, int(mi))
        except ValueError:
            return None
 
    return fecha, to_time(primera), to_time(ultima)
 
 
def parsear_reporte_pdf(uploaded_file):
    """
    Parsea un reporte individual (PDF) de marcaciones de un funcionario.
    Retorna un diccionario con nombre, identificación, periodo, DataFrame de días,
    días no legibles (posible falla de OCR) y si se usó OCR. Si falla, incluye 'error'.
    """
    file_bytes = uploaded_file.getvalue()
    texto, used_ocr, error = _extraer_texto_pdf(file_bytes, uploaded_file.name)
    if error:
        return {"archivo": uploaded_file.name, "error": error}
 
    lineas = [l.strip() for l in texto.split("\n")]
 
    # Nombre: línea inmediatamente anterior a "Datos del Colaborador"
    nombre = None
    for i, l in enumerate(lineas):
        if "datos del colaborador" in l.lower():
            for j in range(i - 1, -1, -1):
                if lineas[j]:
                    nombre = lineas[j]
                    break
            break
    if not nombre:
        nombre = uploaded_file.name.rsplit(".", 1)[0]
 
    ident_m = re.search(r'Identificaci[oó]n:?\s*(\d+)', texto)
    identificacion = ident_m.group(1) if ident_m else "—"
 
    fi_idx = texto.lower().find("fecha inicial")
    fechas_periodo = re.findall(r'\d{1,2}/\d{1,2}/\d{4}', texto[fi_idx:]) if fi_idx >= 0 else []
    if len(fechas_periodo) < 2:
        return {"archivo": uploaded_file.name, "nombre": nombre,
                "error": "No se pudo determinar el periodo (Fecha Inicial / Fecha Final) del reporte."}
 
    def parse_ddmmyyyy(s):
        d, m, y = s.split("/")
        return date(int(y), int(m), int(d))
 
    fecha_inicial = parse_ddmmyyyy(fechas_periodo[0])
    fecha_final = parse_ddmmyyyy(fechas_periodo[1])
 
    filas = {}
    for l in lineas:
        r = _parsear_fila(l)
        if r:
            f, primera, ultima = r
            if fecha_inicial <= f <= fecha_final:
                filas[f] = {"fecha": f, "primera": primera, "ultima": ultima}
 
    dias_no_legibles = []
    cur = fecha_inicial
    while cur <= fecha_final:
        if cur not in filas:
            dias_no_legibles.append(cur)
        cur += timedelta(days=1)
 
    df = pd.DataFrame(list(filas.values())) if filas else pd.DataFrame(columns=["fecha", "primera", "ultima"])
 
    return {
        "archivo": uploaded_file.name,
        "nombre": nombre,
        "identificacion": identificacion,
        "fecha_inicial": fecha_inicial,
        "fecha_final": fecha_final,
        "df": df,
        "dias_no_legibles": dias_no_legibles,
        "used_ocr": used_ocr,
        "error": None,
    }
 
 
# ─── Funciones de análisis (horario, inconsistencias) ─────────────────────────
 
def detect_schedule(arrival_times):
    """Detecta el horario oficial del funcionario según sus marcaciones de entrada."""
    valid = [t for t in arrival_times if t is not None]
    if len(valid) < 3:
        return "7:00 - 4:00"
 
    minutes = [t.hour * 60 + t.minute for t in valid]
    best_schedule, best_score = None, float("inf")
    candidates = {"7:00 - 4:00": 7 * 60, "7:30 - 4:30": 7 * 60 + 30, "8:00 - 5:00": 8 * 60}
    for sched, ref_min in candidates.items():
        close = [m for m in minutes if abs(m - ref_min) <= 45]
        if len(close) >= len(minutes) * 0.3:
            mean_diff = np.mean([abs(m - ref_min) for m in close])
            if mean_diff < best_score:
                best_score, best_schedule = mean_diff, sched
    return best_schedule or "7:00 - 4:00"
 
 
def is_weekend(d):
    return d.weekday() >= 5
 
 
def classify_day(row, schedule_key):
    """Clasifica un día y retorna la inconsistencia (si existe)."""
    d = row.get("fecha")
    primera = row.get("primera")
    ultima = row.get("ultima")
 
    if d is None or is_weekend(d):
        return None
 
    sched = HORARIOS[schedule_key]
    hora_entrada = sched["entrada"]
    tolerancia = sched["tolerancia"]
    limite_tarde = (datetime.combine(date.today(), hora_entrada) + timedelta(minutes=tolerancia)).time()
 
    if primera is None and ultima is None:
        return {"tipo": "NO_MARCO", "detalle": "No registró marcación", "primera": None, "ultima": None}
 
    inconsistencias = []
    if primera is not None and primera > limite_tarde:
        retraso = datetime.combine(date.today(), primera) - datetime.combine(date.today(), hora_entrada)
        mins = int(retraso.total_seconds() / 60)
        inconsistencias.append(
            f"Llegada tarde: {primera.strftime('%H:%M')} (+{mins} min sobre {hora_entrada.strftime('%H:%M')})"
        )
    if ultima is None and primera is not None:
        inconsistencias.append("No registró marcación de salida")
 
    if inconsistencias:
        return {"tipo": "INCONSISTENCIA", "detalle": " | ".join(inconsistencias), "primera": primera, "ultima": ultima}
    return None
 
 
def compute_inconsistencias_funcionario(func_data, schedule_key):
    """Recorre todo el periodo (días hábiles) y arma la lista de inconsistencias,
    incluyendo los días que no se pudieron leer del PDF (posible falla de OCR)."""
    incs = []
    df = func_data["df"]
    filas_por_fecha = {r["fecha"]: r for r in df.to_dict("records")} if not df.empty else {}
 
    cur = func_data["fecha_inicial"]
    while cur <= func_data["fecha_final"]:
        if not is_weekend(cur):
            if cur in func_data["dias_no_legibles"]:
                incs.append({
                    "fecha": cur, "tipo": "SIN_DATO",
                    "detalle": "No se pudo leer el registro de este día en el PDF (posible falla de OCR). Verifique manualmente contra el reporte original.",
                    "primera": None, "ultima": None,
                    "subsanada": False, "compromiso": False, "texto_compromiso": "",
                })
            elif cur in filas_por_fecha:
                inc = classify_day(filas_por_fecha[cur], schedule_key)
                if inc:
                    incs.append({
                        "fecha": cur, "tipo": inc["tipo"], "detalle": inc["detalle"],
                        "primera": inc["primera"], "ultima": inc["ultima"],
                        "subsanada": False, "compromiso": False, "texto_compromiso": "",
                    })
        cur += timedelta(days=1)
    return incs
 
 
# ─── Generación de certificado PDF (sin cambios funcionales) ──────────────────
 
def generar_certificado_pdf(periodo, jefe_nombre, jefe_cargo, tiene_inasistencias):
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
    from reportlab.lib import colors
 
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        topMargin=2 * cm, bottomMargin=2 * cm, leftMargin=2.5 * cm, rightMargin=2.5 * cm,
    )
 
    styles = getSampleStyleSheet()
    style_center = ParagraphStyle("center", parent=styles["Normal"], alignment=TA_CENTER, fontSize=11, leading=16)
    style_body = ParagraphStyle("body", parent=styles["Normal"], alignment=TA_JUSTIFY, fontSize=11, leading=18, spaceAfter=10)
    style_bold_c = ParagraphStyle("boldc", parent=styles["Normal"], alignment=TA_CENTER, fontSize=12, leading=18, fontName="Helvetica-Bold")
    style_footer = ParagraphStyle("footer", parent=styles["Normal"], alignment=TA_LEFT, fontSize=9, leading=12, textColor=colors.HexColor("#444444"))
    style_sign = ParagraphStyle("sign", parent=styles["Normal"], alignment=TA_CENTER, fontSize=11, fontName="Helvetica-Bold")
 
    story = []
    story.append(Paragraph(
        "EL JEFE DE LA DIVISIÓN DE FISCALIZACIÓN Y LIQUIDACIÓN TRIBUTARIA INTENSIVA<br/>"
        "DE LA DIRECCIÓN SECCIONAL DE IMPUESTOS Y ADUANAS DE ARMENIA<br/>"
        "DE LA UNIDAD ADMINISTRATIVA ESPECIAL DIRECCIÓN DE IMPUESTOS Y<br/>"
        "ADUANAS NACIONALES", style_bold_c,
    ))
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph("En cumplimiento de lo dispuesto en el Decreto No. 051 del 16 de enero de 2018", style_center))
    story.append(Spacer(1, 0.8 * cm))
    story.append(Paragraph("<b>CERTIFICA:</b>", style_bold_c))
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(
        f"Que ha verificado los reportes de asistencia del personal adscrito a esta "
        f"División, en el período comprendido entre los días 1° y 30 del mes de {periodo}.",
        style_body,
    ))
 
    no_check = "_X_" if not tiene_inasistencias else "___"
    si_check = "_X_" if tiene_inasistencias else "___"
    story.append(Paragraph(
        f"Que NO {no_check} SI {si_check} se presentaron inasistencias no justificadas "
        f"del personal a mi cargo, durante el período comprendido entre los días 1° y 30 de {periodo}.",
        style_body,
    ))
    story.append(Paragraph(
        "Que, en caso de haberse presentado inasistencias no justificadas, se acompaña a "
        "la presente certificación, el reporte correspondiente.", style_body,
    ))
    story.append(Paragraph(
        f"Lo anterior para efectos de pago de la remuneración del mes de "
        f"{periodo.split()[0]} a los servidores públicos de esta División.", style_body,
    ))
 
    hoy = datetime.now()
    fecha_str = (f"el día {hoy.day:02d} ({hoy.strftime('%d').lstrip('0')}) "
                 f"de {MESES_NOMBRE[hoy.month]} de {hoy.year}")
    story.append(Paragraph(
        f"Se expide en Armenia, {fecha_str}, con destino a la Subdirección de Gestión del Empleo Público.",
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
 
 
# ─── Gestión de estado ─────────────────────────────────────────────────────────
 
def init_state():
    defaults = {
        "funcionarios":       {},   # nombre -> datos parseados + horario
        "inconsistencias":    {},   # nombre -> lista de inconsistencias (solo en_revision)
        "en_revision":        [],   # nombres seleccionados para revisión manual
        "ranking":            [],   # [(nombre, conteo_total)] de mayor a menor
        "archivos_excluidos": [],   # [(archivo, motivo)]
        "compromisos":        [],
        "periodo_mes":        None,
        "periodo_anio":       None,
        "datos_cargados":     False,
        "jefe_nombre":        "JORGE IVÁN RODRÍGUEZ",
        "jefe_cargo":         "JEFE DIVISIÓN DE FISCALIZACIÓN Y LIQUIDACIÓN TRIBUTARIA INTENSIVA",
        "last_batch_id":      None,
        "ultimo_func_tab2":   None,
        "ultimo_func_tab3":   "— Todos —",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
 
 
init_state()
 
 
def procesar_lote(uploaded_files):
    """Procesa el lote de PDFs subidos: parsea, valida el periodo, calcula
    horarios/inconsistencias y selecciona a los funcionarios a revisar."""
    parseados, excluidos = [], []
 
    for f in uploaded_files:
        res = parsear_reporte_pdf(f)
        if res.get("error"):
            excluidos.append((f.name, res["error"]))
        else:
            parseados.append(res)
 
    if not parseados:
        return {}, [], excluidos, [], None, None
 
    # Determinar el periodo mayoritario (mes, año) según fecha_inicial
    periodos = [(r["fecha_inicial"].month, r["fecha_inicial"].year) for r in parseados]
    periodo_mayoritario = max(set(periodos), key=periodos.count)
 
    funcionarios, conteos = {}, []
    for r in parseados:
        periodo_r = (r["fecha_inicial"].month, r["fecha_inicial"].year)
        if periodo_r != periodo_mayoritario:
            mes_r = MESES_NOMBRE.get(periodo_r[0], periodo_r[0])
            excluidos.append((
                r["archivo"],
                f"El periodo del reporte ({mes_r} de {periodo_r[1]}) no coincide con el "
                f"periodo mayoritario del lote ({MESES_NOMBRE[periodo_mayoritario[0]]} de {periodo_mayoritario[1]})."
            ))
            continue
 
        arrivals = r["df"]["primera"].tolist() if not r["df"].empty else []
        horario = detect_schedule(arrivals)
        r["horario"] = horario
        nombre = r["nombre"]
        funcionarios[nombre] = r
 
        incs_completas = compute_inconsistencias_funcionario(r, horario)
        conteo = len(incs_completas)
        conteos.append((nombre, conteo, incs_completas))
 
    ranking = sorted(conteos, key=lambda x: x[1], reverse=True)
 
    top3 = [nombre for nombre, _, _ in ranking[:TOP_N_REVISION]]
    forzados_ocr = [nombre for nombre, r in funcionarios.items()
                    if r["used_ocr"] and len(r["dias_no_legibles"]) > 0 and nombre not in top3]
    en_revision = top3 + forzados_ocr
 
    inconsistencias = {nombre: incs for nombre, _, incs in ranking if nombre in en_revision}
    ranking_simple = [(nombre, conteo) for nombre, conteo, _ in ranking]
 
    return funcionarios, en_revision, excluidos, ranking_simple, periodo_mayoritario, inconsistencias
 
 
# ─── INTERFAZ PRINCIPAL ────────────────────────────────────────────────────────
 
st.markdown("""
<div class="main-header">
    <h1>🏛️ Sistema de Análisis de Marcaciones</h1>
    <p>Dirección Seccional de Impuestos y Aduanas de Armenia — DIAN</p>
</div>
""", unsafe_allow_html=True)
 
with st.sidebar:
    st.subheader("⚙️ Configuración")
    st.session_state.jefe_nombre = st.text_input("Nombre del Jefe", value=st.session_state.jefe_nombre)
    st.session_state.jefe_cargo = st.text_input("Cargo del Jefe", value=st.session_state.jefe_cargo)
    st.markdown("---")
    if st.session_state.datos_cargados and st.session_state.periodo_mes:
        st.caption(f"📅 Periodo detectado: **{MESES_NOMBRE[st.session_state.periodo_mes]} de {st.session_state.periodo_anio}**")
    st.caption(f"🔍 Funcionarios en revisión: **top {TOP_N_REVISION}** por inconsistencias"
               " (+ los que requirieron OCR de baja confianza)")
    st.markdown("---")
    st.caption("Sistema de gestión de asistencia v2.0 — carga por reportes PDF individuales")
 
tab1, tab2, tab3, tab4 = st.tabs([
    "📂 Cargar Reportes",
    "👤 Funcionarios en Revisión",
    "📋 Inconsistencias",
    "📜 Certificado",
])
 
# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — CARGAR REPORTES (PDF individuales)
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    st.subheader("Cargar reportes individuales de marcaciones (PDF)")
    st.info(
        "Suba los reportes PDF de **todos los funcionarios del área correspondientes a un "
        "mismo mes** (uno por funcionario, tal como los exporta el portal "
        "soyfuncionario.dian.gov.co). El sistema detecta automáticamente el mes del periodo "
        "y selecciona a los 3 funcionarios con más inconsistencias para revisión manual; "
        "el resto no requiere gestión."
    )
 
    uploaded = st.file_uploader(
        "Seleccionar reportes PDF", type=["pdf"], accept_multiple_files=True
    )
 
    if uploaded:
        batch_id = tuple(sorted((f.name, f.size) for f in uploaded))
        if st.session_state.get("last_batch_id") != batch_id:
            with st.spinner(f"Procesando {len(uploaded)} reporte(s)..."):
                try:
                    (funcionarios, en_revision, excluidos, ranking,
                     periodo, inconsistencias) = procesar_lote(uploaded)
 
                    st.session_state.funcionarios = funcionarios
                    st.session_state.en_revision = en_revision
                    st.session_state.archivos_excluidos = excluidos
                    st.session_state.ranking = ranking
                    st.session_state.inconsistencias = inconsistencias
                    if periodo:
                        st.session_state.periodo_mes, st.session_state.periodo_anio = periodo
                    st.session_state.datos_cargados = bool(funcionarios)
                    st.session_state.last_batch_id = batch_id
                    st.session_state.ultimo_func_tab2 = None
                    st.session_state.ultimo_func_tab3 = "— Todos —"
                except Exception as e:
                    st.error(f"Error inesperado al procesar el lote: {e}")
                    st.exception(e)
 
    if st.session_state.archivos_excluidos:
        with st.expander(f"⚠️ {len(st.session_state.archivos_excluidos)} archivo(s) excluido(s) del análisis", expanded=True):
            for archivo, motivo in st.session_state.archivos_excluidos:
                st.warning(f"**{archivo}**: {motivo}")
 
    if st.session_state.datos_cargados:
        periodo_str = f"{MESES_NOMBRE[st.session_state.periodo_mes]} de {st.session_state.periodo_anio}"
        st.success(
            f"✅ Se procesaron **{len(st.session_state.funcionarios)} funcionarios** "
            f"para el periodo de **{periodo_str}**."
        )
 
        total_func = len(st.session_state.funcionarios)
        total_en_revision = len(st.session_state.en_revision)
        total_inc_revision = sum(len(v) for v in st.session_state.inconsistencias.values())
        pendientes = sum(
            sum(1 for i in v if not i["subsanada"] and not i["compromiso"])
            for v in st.session_state.inconsistencias.values()
        )
 
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.markdown(f"""<div class="metric-card"><div class="value ok">{total_func}</div>
                <div class="label">Funcionarios procesados</div></div>""", unsafe_allow_html=True)
        with col2:
            st.markdown(f"""<div class="metric-card"><div class="value warn">{total_en_revision}</div>
                <div class="label">En revisión</div></div>""", unsafe_allow_html=True)
        with col3:
            st.markdown(f"""<div class="metric-card"><div class="value warn">{total_inc_revision}</div>
                <div class="label">Inconsistencias (en revisión)</div></div>""", unsafe_allow_html=True)
        with col4:
            color = "danger" if pendientes > 0 else "ok"
            st.markdown(f"""<div class="metric-card"><div class="value {color}">{pendientes}</div>
                <div class="label">Pendientes</div></div>""", unsafe_allow_html=True)
 
        st.markdown("### Resumen de todos los funcionarios")
        st.caption(
            "Solo los funcionarios marcados como **🔍 En revisión** requieren gestión de "
            "inconsistencias antes del certificado. Los demás quedan fuera del proceso."
        )
        rows_sum = []
        conteo_map = dict(st.session_state.ranking)
        for nombre, datos in st.session_state.funcionarios.items():
            en_rev = nombre in st.session_state.en_revision
            ocr_flag = " ⚠️ OCR" if datos["used_ocr"] else ""
            rows_sum.append({
                "Funcionario": nombre + ocr_flag,
                "Identificación": datos["identificacion"],
                "Horario detectado": datos["horario"],
                "Inconsistencias detectadas": conteo_map.get(nombre, 0),
                "Estado": "🔍 En revisión" if en_rev else "— Sin gestión requerida",
            })
        rows_sum.sort(key=lambda r: r["Inconsistencias detectadas"], reverse=True)
        st.dataframe(pd.DataFrame(rows_sum), use_container_width=True, hide_index=True)
 
        if any(d["used_ocr"] for d in st.session_state.funcionarios.values()):
            st.caption(
                "⚠️ OCR = el PDF de ese funcionario no traía texto (parecía una imagen/captura) "
                "y se leyó con reconocimiento óptico; los datos pueden ser menos confiables. "
                "Por eso se incluyó automáticamente en la revisión, aunque no quedara en el top 3."
            )
 
# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — FUNCIONARIOS EN REVISIÓN
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    if not st.session_state.datos_cargados:
        st.warning("Primero cargue los reportes en la pestaña anterior.")
    elif not st.session_state.en_revision:
        st.info("No hay funcionarios en revisión.")
    else:
        st.subheader("Detalle día a día — funcionarios en revisión")
 
        def count_pendientes(n):
            return sum(1 for i in st.session_state.inconsistencias.get(n, [])
                       if not i["subsanada"] and not i["compromiso"])
 
        nombres_ordenados = sorted(st.session_state.en_revision, key=count_pendientes, reverse=True)
 
        try:
            idx_t2 = nombres_ordenados.index(st.session_state.ultimo_func_tab2)
        except (ValueError, TypeError):
            idx_t2 = 0
 
        func_sel = st.selectbox("Seleccionar funcionario", nombres_ordenados, index=idx_t2)
        st.session_state.ultimo_func_tab2 = func_sel
 
        if func_sel:
            datos_func = st.session_state.funcionarios[func_sel]
            df_func = datos_func["df"]
            sched = datos_func["horario"]
 
            if datos_func["used_ocr"]:
                st.warning(
                    "⚠️ Este reporte se leyó mediante OCR (el PDF no tenía texto extraíble). "
                    "Verifique los datos contra el PDF original, especialmente los días marcados como 'sin dato'."
                )
 
            col_a, col_b = st.columns([2, 2])
            with col_a:
                nuevo_sched = st.selectbox(
                    "Horario detectado / asignar manualmente", list(HORARIOS.keys()),
                    index=list(HORARIOS.keys()).index(sched), key=f"sched_{func_sel}",
                )
            with col_b:
                if st.button("Aplicar horario", key=f"apply_{func_sel}"):
                    st.session_state.funcionarios[func_sel]["horario"] = nuevo_sched
                    st.session_state.inconsistencias[func_sel] = compute_inconsistencias_funcionario(
                        datos_func, nuevo_sched
                    )
                    st.rerun()
 
            st.markdown(f"**Horario asignado:** `{sched}` — "
                        f"Entrada: `{HORARIOS[sched]['entrada'].strftime('%H:%M')}` | "
                        f"Tolerancia: `{HORARIOS[sched]['tolerancia']} min`")
 
            inc_fechas = {i["fecha"]: i for i in st.session_state.inconsistencias.get(func_sel, [])}
            filas_por_fecha = {r["fecha"]: r for r in df_func.to_dict("records")} if not df_func.empty else {}
 
            rows_dias = []
            cur = datos_func["fecha_inicial"]
            while cur <= datos_func["fecha_final"]:
                inc = inc_fechas.get(cur)
                row = filas_por_fecha.get(cur)
                if is_weekend(cur):
                    estado = "🗓️ Fin de semana"
                elif inc is None:
                    estado = "✅ Normal"
                elif inc["tipo"] == "SIN_DATO":
                    estado = "❓ Sin dato (OCR)"
                elif inc["subsanada"]:
                    estado = "🔵 Subsanada"
                elif inc["compromiso"]:
                    estado = "🟣 Compromiso"
                else:
                    estado = f"⚠️ {inc['tipo']}"
 
                rows_dias.append({
                    "Fecha": cur.strftime("%Y-%m-%d"),
                    "Día": cur.strftime("%A").capitalize(),
                    "Entrada": row["primera"].strftime("%H:%M") if row and row["primera"] else "—",
                    "Salida": row["ultima"].strftime("%H:%M") if row and row["ultima"] else "—",
                    "Estado": estado,
                    "Detalle": inc["detalle"] if inc else "",
                })
                cur += timedelta(days=1)
 
            st.dataframe(pd.DataFrame(rows_dias), use_container_width=True, hide_index=True, height=450)
 
# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — INCONSISTENCIAS
# ══════════════════════════════════════════════════════════════════════════════
with tab3:
    if not st.session_state.datos_cargados:
        st.warning("Primero cargue los reportes en la pestaña anterior.")
    elif not st.session_state.en_revision:
        st.info("No hay funcionarios en revisión.")
    else:
        st.subheader("Gestión de inconsistencias (solo funcionarios en revisión)")
 
        def count_pendientes_tab3(n):
            return sum(1 for i in st.session_state.inconsistencias.get(n, [])
                       if not i["subsanada"] and not i["compromiso"])
 
        nombres_ordenados_tab3 = sorted(st.session_state.en_revision, key=count_pendientes_tab3, reverse=True)
        lista_opciones_tab3 = ["— Todos —"] + nombres_ordenados_tab3
 
        try:
            idx_t3 = lista_opciones_tab3.index(st.session_state.ultimo_func_tab3)
        except ValueError:
            idx_t3 = 0
 
        col_f1, col_f2 = st.columns([2, 2])
        with col_f1:
            func_filtro = st.selectbox("Filtrar por funcionario", lista_opciones_tab3, index=idx_t3)
            st.session_state.ultimo_func_tab3 = func_filtro
        with col_f2:
            estado_filtro = st.selectbox(
                "Filtrar por estado",
                ["— Todos —", "Pendientes", "Subsanadas", "Con compromiso"],
                index=1, key="filtro_estado",
            )
 
        if func_filtro != "— Todos —":
            pendientes_func = [i for i in st.session_state.inconsistencias[func_filtro]
                               if not i["subsanada"] and not i["compromiso"]]
            if pendientes_func:
                st.markdown("---")
                with st.form(key=f"form_gen_{func_filtro}"):
                    st.subheader("🚀 Acción Rápida: Compromiso General")
                    st.info(f"El funcionario **{func_filtro}** tiene **{len(pendientes_func)} inconsistencias "
                            f"pendientes**. Puede aplicar un compromiso a TODAS en bloque.")
                    texto_general = st.text_area(
                        "Texto del compromiso general",
                        placeholder="Este compromiso subsanará en bloque las inconsistencias pendientes...",
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
                                "funcionario": func_filtro, "fecha": "Múltiples fechas",
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
                est = ("Subsanada" if inc["subsanada"] else
                       "Con compromiso" if inc["compromiso"] else "Pendiente")
                if estado_filtro == "Pendientes" and est != "Pendiente":
                    continue
                if estado_filtro == "Subsanadas" and est != "Subsanada":
                    continue
                if estado_filtro == "Con compromiso" and est != "Con compromiso":
                    continue
                lista_trabajo.append((nombre, idx, inc, est))
 
        if not lista_trabajo:
            st.success("🎉 No hay inconsistencias en esta vista con el filtro actual.")
        else:
            st.markdown(f"**{len(lista_trabajo)} inconsistencia(s) en vista**")
            for nombre, idx, inc, estado in lista_trabajo:
                fecha_str = inc["fecha"].strftime("%d/%m/%Y") if hasattr(inc["fecha"], "strftime") else inc["fecha"]
                dia_str = inc["fecha"].strftime("%A").capitalize() if hasattr(inc["fecha"], "strftime") else ""
                borde = ("#1565c0" if estado == "Subsanada" else
                         "#6a1b9a" if estado == "Con compromiso" else "#c62828")
 
                with st.container():
                    st.markdown(
                        f"<div style='border-left:4px solid {borde}; padding:0.5rem 1rem; "
                        f"margin-bottom:0.5rem; background:#fafafa; border-radius:4px;'>"
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
                                    "Texto del compromiso", key=f"txt_{nombre}_{idx}",
                                    placeholder="Describa el compromiso adquirido...", height=80,
                                )
                                if st.button("Guardar compromiso", key=f"commit_{nombre}_{idx}"):
                                    if texto.strip():
                                        st.session_state.inconsistencias[nombre][idx]["compromiso"] = True
                                        st.session_state.inconsistencias[nombre][idx]["texto_compromiso"] = texto.strip()
                                        st.session_state.compromisos.append({
                                            "funcionario": nombre,
                                            "fecha": inc["fecha"].strftime("%Y-%m-%d"),
                                            "tipo": inc["tipo"], "detalle": inc["detalle"],
                                            "compromiso": texto.strip(),
                                            "registrado": datetime.now().strftime("%Y-%m-%d %H:%M"),
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
                df_comp.columns = ["Funcionario", "Fecha", "Tipo", "Detalle", "Compromiso adquirido", "Registrado"]
                st.dataframe(df_comp, use_container_width=True, hide_index=True)
 
                buf_comp = io.BytesIO()
                df_comp.to_excel(buf_comp, index=False)
                st.download_button(
                    "⬇️ Descargar registro de compromisos (Excel)",
                    data=buf_comp.getvalue(), file_name="compromisos_asistencia.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
 
# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — CERTIFICADO
# ══════════════════════════════════════════════════════════════════════════════
with tab4:
    st.subheader("Generar certificado de cumplimiento de horario")
 
    if not st.session_state.datos_cargados:
        st.warning("Primero cargue los reportes en la pestaña anterior.")
    else:
        incs_total = sum(len(v) for v in st.session_state.inconsistencias.values())
        subsanadas = sum(
            sum(1 for i in v if i["subsanada"] or i["compromiso"])
            for v in st.session_state.inconsistencias.values()
        )
        pendientes = incs_total - subsanadas
        periodo_certificado = f"{MESES_NOMBRE[st.session_state.periodo_mes]} de {st.session_state.periodo_anio}"
 
        if pendientes > 0:
            st.error(
                f"⛔ Aún hay **{pendientes} inconsistencia(s) pendiente(s)** por subsanar entre "
                "los funcionarios en revisión. Resuélvalas antes de generar el certificado."
            )
            st.markdown("### Inconsistencias pendientes")
            for nombre, incs_list in st.session_state.inconsistencias.items():
                pend = [i for i in incs_list if not i["subsanada"] and not i["compromiso"]]
                if pend:
                    st.markdown(f"**{nombre}** ({len(pend)} pendiente[s])")
                    for inc in pend:
                        st.markdown(f"  - {inc['fecha'].strftime('%d/%m/%Y')}: {inc['tipo']} — {inc['detalle']}")
        else:
            if incs_total == 0:
                st.success("✅ No se registraron inconsistencias entre los funcionarios en revisión. El certificado puede generarse.")
                tiene_inasistencias = False
            else:
                st.success(
                    f"✅ Todas las inconsistencias de los funcionarios en revisión ({incs_total}) "
                    "han sido subsanadas o documentadas con compromisos. El certificado puede generarse."
                )
                tiene_inasistencias = True
 
            st.markdown("### Vista previa del certificado")
            st.markdown(f"""
> **EL JEFE DE LA DIVISIÓN DE FISCALIZACIÓN Y LIQUIDACIÓN TRIBUTARIA INTENSIVA**
> **DE LA DIRECCIÓN SECCIONAL DE IMPUESTOS Y ADUANAS DE ARMENIA**
>
> En cumplimiento de lo dispuesto en el Decreto No. 051 del 16 de enero de 2018
>
> **CERTIFICA:**
>
> Que ha verificado los reportes de asistencia del personal adscrito a esta División,
> en el período comprendido entre los días 1° y 30 del mes de **{periodo_certificado}**.
>
> Que **NO {'_X_' if not tiene_inasistencias else '___'} SI {'_X_' if tiene_inasistencias else '___'}**
> se presentaron inasistencias no justificadas del personal a mi cargo.
>
> _______________________________________________
> **{st.session_state.jefe_nombre}**
> {st.session_state.jefe_cargo}
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
                        label="⬇️ Descargar certificado PDF", data=pdf_bytes,
                        file_name=f"certificado_horario_{periodo_certificado.replace(' ', '_')}.pdf",
                        mime="application/pdf",
                    )
                    st.success("Certificado generado correctamente.")
                except ImportError:
                    st.error("Error: Falta la librería 'reportlab' para generar el PDF. Instálala ejecutando 'pip install reportlab' en tu terminal.")
